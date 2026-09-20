# VPS Ops & Nightly Data Pipeline

**The unglamorous workflow that keeps a self-hosted stack alive: healthcheck, self-heal, verified backup, ETL into Postgres — and a data-quality gate that quarantines a bad load instead of letting it poison every dashboard.**

## The problem

Everything else in this portfolio runs on one Ubuntu VPS. That box has the failure modes every small self-hosted stack has, and all of them are quiet:

- **Disk fills up.** Docker never reclaims old image layers on its own, and journald will happily eat several gigabytes. The first symptom is usually Postgres refusing writes at 3am.
- **The backup "succeeds" and is empty.** `tar` exits 0, cron is happy, and you find out the archive is 200 bytes on the day you need it.
- **The ETL runs and loads nothing.** An upstream log path changed, the job exits 0 with zero rows, and every dashboard downstream shows a clean, confident, completely wrong flat line.

The pattern: **exit code 0 is not evidence that a job did its work.** Each stage here verifies its own output rather than trusting its return code.

## The solution

```
02:00 nightly
   │
   ▼
Run VPS Healthcheck  (SSH → bash script returning JSON)
   ▼
Parse Health JSON  ── throws loudly on non-JSON
   │
   ├──────────────────────────────┐
   ▼                              ▼
Disk Under Pressure?         Backup Volumes  (SSH → tar + sha256)
   │ yes        │ no               ▼
   ▼            │            Verify Backup   ── archive must exist,
Reclaim Disk    │                  ▼             exceed 1 MiB, checksum-verify
Space           │            Backup Good? ──no──► Alert Backup Failure
   ▼            │                  │ yes            (ETL skipped)
Re-Check Disk   │                  ▼
   ▼            │            Run ETL Load  (SSH → Python → Postgres)
Still Full? ────┤                  ▼
   │ yes        │            Parse ETL Result
   ▼            ▼                  ▼
Escalate    Disk Recovered   Data Quality Checks  (SQL assertions)
                                   ▼
                             Assert Data Quality
                                   ▼
                        Data Quality Passed?
                          │ yes         │ no
                          ▼             ▼
                   Log Nightly Run   Quarantine Bad Load
                          ▼             ▼
                   Post Digest      Alert Data Quality Failure
```

## Self-healing before escalating

Disk pressure is the one failure here that's usually fixable without a human, so the workflow tries first: `docker system prune -af --filter 'until=168h'` plus `journalctl --vacuum-time=7d`, then **re-checks**. Only if it's still above 85% does anyone get paged.

That re-check is the part that matters. Plenty of "auto-remediation" fires a fix and reports success without ever confirming it worked, which is strictly worse than not trying — you've now got a silent failure *and* a green check mark.

## The healthcheck returns JSON, not prose

[`scripts/vps_healthcheck.sh`](./scripts/vps_healthcheck.sh) emits exactly one JSON object on stdout; warnings go to stderr. The workflow parses it directly instead of regex-scraping human-readable output that changes shape between Ubuntu releases.

It reports disk %, available MB, memory %, 1-minute load, core count, uptime, running container count, unhealthy containers, failed systemd units, and TLS certificate days-to-expiry.

Two details worth the space:

**Memory uses `MemAvailable`, not `free`.** Linux deliberately spends idle RAM on page cache. Alerting on "free memory" means alerting constantly on a perfectly healthy box, and an alert everyone has learned to ignore is worse than no alert.

**Every `docker` pipeline ends in `|| true`.** Under `set -o pipefail`, a single unreachable-daemon hiccup would abort the entire healthcheck — and a healthcheck that dies because one probe failed tells the workflow nothing at all. The script reports `docker_reachable: false` and keeps going.

Its exit code is `0` whenever the check *ran*. "The server is unhealthy" is data for the workflow to act on, not a script error.

## Backups that are verified before they're trusted

[`scripts/backup_volumes.sh`](./scripts/backup_volumes.sh) copies each Docker named volume out through a throwaway `alpine` container — the only portable way to read a named volume without knowing where the storage driver put it — then tars, checksums, and **reads the archive back** (`tar -tzf`) before accepting it.

Two orderings that are deliberate:

- **Write to `.partial`, then `mv`.** An interrupted run leaves nothing rather than a truncated archive that a future restore would unpack happily and uselessly.
- **Retention runs last, and only after verification passes.** Pruning first means a failed backup leaves you with nothing at all — the exact moment you need the old one.

The JSON it returns (`archive`, `bytes`, `checksum`, `checksum_verified`) is what `Verify Backup` checks. A 200-byte tarball fails the 1 MiB floor and the ETL never runs, because loading data on a night you have no backup is the wrong risk to take.

## The ETL is idempotent

[`scripts/etl_load.py`](./scripts/etl_load.py) reads nginx access logs (including rotated `.gz` files) and n8n execution exports, normalises both into one flat `events` table, and upserts.

Logs have no primary key, so the script builds a deterministic one — `sha256(source|ip|timestamp|method|path)[:32]` — and uses `ON CONFLICT (event_id) DO UPDATE`. **A failed nightly run can simply be re-run** without doubling yesterday's traffic numbers.

It also de-duplicates *within* the batch before inserting, because overlapping rotated log files hand you the same line twice and `ON CONFLICT` cannot resolve a duplicate key that appears twice inside a single `INSERT`.

Postgres credentials come from `PGHOST`/`PGUSER`/`PGPASSWORD` in the environment, never from the command line — the full command line of every SSH call is recorded in the n8n execution log.

## The data-quality gate

This is the part most pipelines skip. After loading, four SQL assertions run:

| Assertion | Catches |
|---|---|
| `rows_today = 0` | Source path changed, permissions broke, job silently no-opped |
| `null_keys > 0` | Parser regressed against a changed log format |
| `duplicate_keys > 0` | Idempotency key collision or a broken dedupe |
| `rows < 40% of trailing 7-day average` | Upstream source partially broke — the failure mode that looks *exactly* like a quiet day |

If any fail, today's rows are moved into `events_quarantine` with the reason attached, and `#ops` is told that dashboards are still reading yesterday's data. **Stale-but-correct beats fresh-but-wrong**, every time. A dashboard showing yesterday's number gets a question; a dashboard showing a confident wrong number gets a decision made on it.

## Tech stack

- **n8n** — Schedule Trigger, SSH, Code, IF, Postgres, Google Sheets, Slack, Error Trigger
- **Bash** — healthcheck and backup scripts, `set -Eeuo pipefail`, JSON output, explicit exit codes
- **Python 3** — the ETL (stdlib + `psycopg2`), idempotent hashing, batched `execute_values`
- **PostgreSQL** — target warehouse, DQ assertions, quarantine table
- **Linux / Ubuntu VPS** — `df`, `/proc/meminfo`, `journalctl`, `systemctl`, `openssl s_client`, Docker CLI

## Why it's built this way

**SSH rather than an agent.** No extra daemon to keep alive on the box, no extra thing that can itself fail silently. The scripts live on the server and are version-controlled here.

**Scripts return JSON; the workflow makes decisions.** Keeps the shell honest and the node graph readable — and every script can be run by hand from a terminal, which is how you debug a 2am failure without replaying a workflow.

**One transaction for the whole ETL load.** A partial load is worse than no load, because it would pass the row-count check while being wrong.

## What I'd improve with more time

- Ship metrics to Prometheus + Grafana instead of Google Sheets, so trends are visible rather than reconstructable.
- Restore-testing: monthly, restore the latest archive into a scratch container and assert the row counts match. An unverified restore path is not a backup, it's a hope.
- `great_expectations` (or dbt tests) instead of hand-written SQL assertions once the check count grows past what's readable in one query.
- Off-site backup replication — a verified archive sitting on the same disk that just filled up is not much of a disaster-recovery plan.

## Running it

Import [`workflow.json`](./workflow.json), add an SSH credential for your VPS, and place the scripts in `/opt/ops`:

```bash
scp scripts/*.sh scripts/*.py ops@your-vps:/opt/ops/
ssh ops@your-vps 'chmod +x /opt/ops/*.sh'
```

Try each one by hand first — they're all designed to be run standalone:

```bash
bash /opt/ops/vps_healthcheck.sh --domain example.com | jq .
bash /opt/ops/backup_volumes.sh --retain 7 | jq .
python3 /opt/ops/etl_load.py --since-hours 24 --dry-run | jq .
```
