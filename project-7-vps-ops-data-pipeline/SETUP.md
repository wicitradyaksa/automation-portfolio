# Setup Guide: VPS Ops & Nightly Data Pipeline

Standalone installation for this one workflow. It doesn't depend on any other workflow in this repo.

**What it does:** every night at 02:00 it SSHes into an Ubuntu server and runs a healthcheck that returns JSON (disk, memory, TLS certificate, containers, systemd). If the disk is full, it prunes and checks again, escalating if that didn't help. It then takes a checksum-verified backup of the Docker volumes, and **only if the backup is good** runs an idempotent ETL into PostgreSQL. SQL data-quality checks follow: a bad load is **quarantined** instead of reaching dashboards. Results go to Google Sheets and Slack.

---

## 1. What you need

| Item | Why |
|---|---|
| A self-hosted n8n (Docker) or n8n Cloud | Uses SSH, Postgres, Sheets and Slack nodes only. It reads `OPS_SHEET_ID` from `$env`, so on Cloud put the ID straight into *Log Nightly Run* |
| An Ubuntu VPS with Docker, reachable over SSH from n8n | The healthcheck, prune, backup and ETL run there |
| PostgreSQL reachable from both the VPS (ETL) and n8n (DQ checks) | The analytics database |
| A Google account | The `Ops Log` tab |
| A Slack workspace where you can install an app | The nightly digest and alerts |

---

## 2. Run n8n

**`.env`**
```ini
GENERIC_TIMEZONE=UTC   # the 02:00 schedule runs in this timezone
OPS_SHEET_ID=
```

**`docker-compose.yml`**
```yaml
services:
  n8n:
    image: docker.n8n.io/n8nio/n8n:latest
    restart: unless-stopped
    ports: ["5678:5678"]
    env_file: .env
    environment:
      - N8N_BLOCK_ENV_ACCESS_IN_NODE=false   # lets {{ $env.OPS_SHEET_ID }} resolve
    volumes:
      - n8n_data:/home/node/.n8n
volumes:
  n8n_data:
```

```bash
docker compose up -d
```

---

## 3. Prepare the VPS

Run as root on the VPS.

```bash
# 1. A dedicated user with only the groups it needs
useradd -m -s /bin/bash ops
usermod -aG docker,adm ops          # docker: healthcheck/prune/backup · adm: read nginx logs
install -d -o ops -g ops /opt/ops /opt/ops/exports /var/backups/volumes

# 2. Tools the scripts call
apt-get update && apt-get install -y python3 python3-psycopg2 openssl
```

Copy the scripts from this project's `scripts/` folder, from your machine:

```bash
scp scripts/vps_healthcheck.sh scripts/backup_volumes.sh scripts/etl_load.py root@YOUR_VPS:/opt/ops/
```

Back on the VPS:

```bash
chown ops:ops /opt/ops/* && chmod 750 /opt/ops/*.sh /opt/ops/*.py
```

**3.1 The one root command.** `journalctl --vacuum-time` needs root. Allow exactly that command (`visudo -f /etc/sudoers.d/n8n-ops`):

```
ops ALL=(root) NOPASSWD: /usr/bin/journalctl --vacuum-time=7d
```

**3.2 Settings for the scripts.** SSH commands run in a non-interactive shell that doesn't read `~/.profile`, so put the settings in a file. Create `/opt/ops/.env` owned by `ops`, mode `600`:

```ini
PGHOST=localhost
PGPORT=5432
PGDATABASE=analytics
PGUSER=etl
PGPASSWORD=change-me
HEALTHCHECK_DOMAIN=yourdomain.com   # enables the TLS-expiry check
```

**3.3 SSH key for n8n.** Generate a key on your machine:

```bash
ssh-keygen -t ed25519 -f n8n_ops -C n8n -N ""
```

Append `n8n_ops.pub` to `/home/ops/.ssh/authorized_keys` (mode `600`, owned by `ops`).

---

## 4. Prepare PostgreSQL

Two roles: `etl` (used by the script on the VPS) and `n8n_dq` (used by n8n, least privilege).

```sql
CREATE DATABASE analytics;
\c analytics

CREATE ROLE etl LOGIN PASSWORD 'change-me';
GRANT CONNECT ON DATABASE analytics TO etl;
GRANT USAGE, CREATE ON SCHEMA public TO etl;   -- the script creates its tables on first run
```

Create the tables by running the ETL once on the VPS:

```bash
sudo -u ops bash -c 'set -a; . /opt/ops/.env; set +a; python3 /opt/ops/etl_load.py --since-hours 24 --target postgres --json'
```

Then create the role n8n uses:

```sql
CREATE ROLE n8n_dq LOGIN PASSWORD 'change-me-too';
GRANT CONNECT ON DATABASE analytics TO n8n_dq;
GRANT USAGE ON SCHEMA public TO n8n_dq;
GRANT SELECT, DELETE ON events TO n8n_dq;       -- DQ checks read; quarantine removes today's rows
GRANT INSERT ON events_quarantine TO n8n_dq;    -- ...and stores them here
```

If n8n connects over the internet, enable SSL in Postgres, allow the n8n IP in `pg_hba.conf`, and use `SSL: require` in the credential.

---

## 5. Credentials and scopes

This workflow needs **four** credentials.

| Credential | n8n type | Nodes |
|---|---|---|
| SSH | SSH Private Key (recommended) or SSH Password | Run VPS Healthcheck, Reclaim Disk Space, Re-Check Disk, Backup Volumes, Run ETL Load |
| Postgres | Postgres | Data Quality Checks, Quarantine Bad Load |
| Google Sheets | Google Sheets OAuth2 API | Log Nightly Run |
| Slack | Slack API | Post Nightly Digest, Alert Data Quality Failure, Alert Backup Failure, Escalate Disk Alert, Alert Engineering (Slack) |

### 5.1 SSH

| Field | Value |
|---|---|
| Host / Port | VPS address / `22` |
| Username | `ops` |
| Private key | the contents of `n8n_ops` |

The workflow file references an **SSH Password** credential. To use the key, open each of the 5 SSH nodes, set **Authentication → Private Key**, and select the key credential.

**Permissions = the Linux rights of `ops`** (set up in §3): `docker` group, `adm` group, write access to `/var/backups/volumes`, ownership of `/opt/ops`, and the single sudo rule. ⚠️ Membership of the `docker` group is effectively root on that host. Use a dedicated server user and protect the key.

### 5.2 Postgres

| Field | Value |
|---|---|
| Host / Port / Database | e.g. VPS address / `5432` / `analytics` |
| User / Password | `n8n_dq` / its password |
| SSL | `require` over the internet |

Privileges: exactly the `GRANT`s in §4 (`SELECT, DELETE` on `events`; `INSERT` on `events_quarantine`).

### 5.3 Google Sheets OAuth2 API

**Scopes** (n8n requests these automatically): `https://www.googleapis.com/auth/spreadsheets` (append) and `https://www.googleapis.com/auth/drive.file`.

1. In <https://console.cloud.google.com>, create a project. Under **APIs & Services → Library**, enable **Google Sheets API** and **Google Drive API**.
2. **Google Auth Platform → Branding / Audience**: choose **Internal** or **External** and add yourself as a Test user. ⚠️ Tokens expire after **7 days** while the app is in Testing, and a nightly job will quietly stop logging. **Publish app.**
3. **Credentials → OAuth client ID → Web application**. Redirect URI: `http://localhost:5678/rest/oauth2-credential/callback`.
4. In n8n: **Credentials → Google Sheets OAuth2 API**, paste the ID and secret, then **Sign in with Google**.

### 5.4 Slack API (bot token)

Bot scopes: `chat:write` (**required**), `chat:write.public` (recommended), `channels:read` / `groups:read` (so the channel picker works).

1. Go to <https://api.slack.com/apps> → **Create New App → From scratch**.
2. Add the scopes, then **Install to Workspace**.
3. Copy the `xoxb-…` token.
4. In n8n: **Credentials → Slack API**.

---

## 6. Prepare Google Sheets and Slack

**Spreadsheet** (its ID goes in `OPS_SHEET_ID`), tab **`Ops Log`**:

```
date, diskUsedPct, memUsedPct, backupArchive, backupBytes, rowsLoaded, dqPassed, status
```

**Slack channels:** `#ops` and `#eng-alerts`.

---

## 7. Import and configure

1. **Import:** open **Workflows → ⋯ → Import from File** and pick `workflow.json`.
2. **Connect credentials** on each node marked ⚠️ (and switch the SSH nodes to *Private Key*, §5.1).
3. **Edit two SSH commands** to match §3:
   - **Reclaim Disk Space**: change `journalctl --vacuum-time=7d` to `sudo -n journalctl --vacuum-time=7d`
   - **Run ETL Load**: prefix the command with the settings file:
     `set -a; . /opt/ops/.env; set +a; python3 /opt/ops/etl_load.py --since-hours 24 --target postgres --json`
   - Optional, for the TLS check: prefix **Run VPS Healthcheck** the same way.
4. **Check failure alerts:** the workflow already names itself as its Error Workflow, so the *Workflow Error → Alert Engineering* branch is live. If you imported it through the editor rather than `n8n import:workflow` or `scripts/n8n_sync.py`, n8n may have given it a new ID, so open **⋯ → Settings → Error Workflow** and make sure *this workflow* is selected.
5. **Activate** it (**Publish** in n8n 2.x).

---

## 8. Node reference

| Node | Type | What it does |
|---|---|---|
| Nightly 02:00 | Schedule (cron `0 2 * * *`) | In `GENERIC_TIMEZONE` |
| Run VPS Healthcheck | SSH `vps_healthcheck.sh --json` | Prints one JSON object |
| Parse Health JSON | Code (JS) | Fails loudly on non-JSON. Flags disk > 85 %, memory > 90 %, certificate < 14 days, unhealthy containers, failed units |
| Disk Under Pressure? | IF | This branch runs in parallel with the backup branch |
| Reclaim Disk Space | SSH `docker system prune -af --filter until=168h` + journal vacuum | ⚠️ Removes all unused images and stopped containers older than 7 days |
| Re-Check Disk → Still Full? | SSH `df` → IF (> 85 %) | |
| Escalate Disk Alert / Disk Recovered | Slack `#ops` / No-Op | |
| Backup Volumes | SSH `backup_volumes.sh --retain 7 --json` | Tarballs in `/var/backups/volumes`, 7 kept |
| Verify Backup → Backup Good? | Code → IF | Archive exists, is over 1 MiB, and `checksum_verified` is true |
| Alert Backup Failure | Slack `#ops` | The ETL is **skipped** |
| Run ETL Load → Parse ETL Result | SSH `etl_load.py` → Code | nginx logs + n8n exports → idempotent upsert into `events` |
| Data Quality Checks | Postgres query | Today's rows, null keys, duplicate keys, the 7-day average |
| Assert Data Quality → Data Quality Passed? | Code → IF | Fails on 0 rows, nulls, duplicates, or a drop of more than 60 % vs. the 7-day average |
| Log Nightly Run → Post Nightly Digest | Sheets `Ops Log` → Slack `#ops` | |
| Quarantine Bad Load → Alert Data Quality Failure | Postgres (move today's rows to `events_quarantine` with a reason) → Slack `#ops` | |
| Workflow Error → Alert Engineering | Error Trigger → Slack `#eng-alerts` | |

---

## 9. Test

1. From your machine:
   ```bash
   ssh -i n8n_ops ops@YOUR_VPS 'bash /opt/ops/vps_healthcheck.sh --json'
   ```
   It must print **only** a JSON object.
2. On the VPS, `sudo -u ops sudo -n journalctl --vacuum-time=7d` must run without a password prompt.
3. In n8n, click **Execute Workflow**. Expected: a row in `Ops Log`, a "Nightly run complete" message in `#ops`, and a new tarball in `/var/backups/volumes`.

---

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `vps_healthcheck.sh returned non-JSON` | Something prints to stdout at login (a `.bashrc` echo or a MOTD script). Make it quiet for non-interactive shells |
| `docker not on PATH` / `permission denied … docker.sock` | `ops` isn't in the `docker` group. Re-login or restart sshd after `usermod` |
| Backup always "failed" | The archive is under 1 MiB (no volumes?) or the checksum failed. Run `backup_volumes.sh --json` by hand |
| ETL connects to the wrong database or has no password | The `/opt/ops/.env` prefix is missing from *Run ETL Load* (§7 step 3) |
| `sudo: a password is required` | The sudoers rule is missing, or the command doesn't match it exactly |
| DQ `permission denied for table events` | Run the `n8n_dq` grants **after** the ETL has created the tables |
| Every night quarantined for `volume_drop` | Traffic really did drop more than 60 %, or a log source moved. Check `ETL_NGINX_GLOB` / `ETL_N8N_GLOB` in `etl_load.py` |
