# Setup Guide: Uptime Monitor & Auto-Remediation

Standalone installation for this one workflow. It doesn't depend on any other workflow in this repo.

**What it does:** every 5 minutes it calls each service's health endpoint. If a service is down, it restarts that service's container through the Docker Engine API, waits 30 s and checks again. If the service recovered, it posts a "self-healed" note; otherwise it escalates to on-call in Slack. Every check and every incident is logged to Google Sheets.

---

## 1. What you need

| Item | Why |
|---|---|
| A self-hosted n8n (Docker) on the **same Docker host** as the services it restarts | Restarts go to that host's Docker API |
| A restart-only Docker socket proxy | Gives n8n the one Docker action it needs, and nothing else (§2) |
| A Google account | `Uptime Log` and `Incidents` tabs |
| A Slack workspace where you can install an app | Incident notices and escalation |

---

## 2. Run n8n with restart rights

*Restart Container* is an HTTP Request node that calls `POST {DOCKER_API_URL}/containers/<name>/restart`. It doesn't talk to the Docker socket directly. A **socket proxy** does, and it only allows container restarts. Don't mount `/var/run/docker.sock` straight into n8n: that gives every workflow editor root on the host.

Create a folder containing this `workflow.json` and the two files below.

**`.env`**
```ini
GENERIC_TIMEZONE=UTC
# The long ID in https://docs.google.com/spreadsheets/d/<THIS_PART>/edit
OPS_SHEET_ID=
# The restart-only Docker API proxy defined in docker-compose.yml
DOCKER_API_URL=http://docker-proxy:2375
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
      - N8N_BLOCK_ENV_ACCESS_IN_NODE=false     # lets {{ $env.* }} resolve
    volumes:
      - n8n_data:/home/node/.n8n
    networks: [default, monitored]

  docker-proxy:
    image: tecnativa/docker-socket-proxy
    restart: unless-stopped
    environment: [CONTAINERS=1, POST=1, ALLOW_RESTARTS=1]   # restart only: no create, exec, delete
    volumes: ["/var/run/docker.sock:/var/run/docker.sock:ro"]

volumes:
  n8n_data:

networks:
  monitored:            # attach the services you monitor to this network
    name: monitored
```

```bash
docker compose up -d
```

For n8n to reach `http://api-gateway:8080/health` by container name, the monitored containers must share a network with n8n:

```bash
docker network connect monitored api-gateway
```

You can also give n8n URLs that go through the host, e.g. `http://host.docker.internal:8080/health`.

Check that the proxy answers (it should print `OK`):

```bash
docker compose exec n8n wget -qO- http://docker-proxy:2375/_ping
```

> Already running the whole portfolio? The repo-root `docker-compose.yml` already includes the `docker-proxy` service, and `.env.example` sets `DOCKER_API_URL`.

---

## 3. Credentials and scopes

This workflow needs **two** credentials. The health checks are unauthenticated `GET`s. The Docker proxy is authorized by its own settings (`CONTAINERS=1, POST=1, ALLOW_RESTARTS=1`) and is reachable only on the internal Docker network. Don't publish its port.

| Credential | n8n type | Nodes |
|---|---|---|
| Google Sheets | Google Sheets OAuth2 API | Log Heartbeat (OK), Log Incident, Log Self-Heal |
| Slack | Slack API | Escalate To On-Call, Notify Self-Healed, Alert Engineering (Slack) |

### 3.1 Google Sheets OAuth2 API

**Scopes** (n8n requests these automatically): `https://www.googleapis.com/auth/spreadsheets` (append rows) and `https://www.googleapis.com/auth/drive.file`.

1. In <https://console.cloud.google.com>, create a project. Under **APIs & Services → Library**, enable **Google Sheets API** and **Google Drive API**.
2. **Google Auth Platform → Branding / Audience**: choose **Internal** (Workspace) or **External**, and add yourself as a Test user.
   ⚠️ While the app is in **Testing**, refresh tokens expire after **7 days**. A monitor running every 5 minutes will stop logging. Click **Publish app**.
3. **Credentials → OAuth client ID → Web application**. Redirect URI: `http://localhost:5678/rest/oauth2-credential/callback`.
4. In n8n: **Credentials → Google Sheets OAuth2 API**, paste the ID and secret, then **Sign in with Google**.

### 3.2 Slack API (bot token)

| Bot token scope | Required? | Why |
|---|---|---|
| `chat:write` | **Required** | Post messages |
| `chat:write.public` | Recommended | Post without inviting the bot |
| `channels:read` / `groups:read` | Recommended / private channels only | The channel picker in the editor |

1. Go to <https://api.slack.com/apps> → **Create New App → From scratch**.
2. Add the bot scopes above, then **Install to Workspace**.
3. Copy the `xoxb-…` token.
4. In n8n: **Credentials → Slack API** → **Access Token**.

---

## 4. Prepare Google Sheets and Slack

**Spreadsheet** (its ID goes in `OPS_SHEET_ID`):

| Tab | Header row |
|---|---|
| `Uptime Log` | `service, status, checkedAt` |
| `Incidents` | `service, outcome, detectedAt` |

**Slack channels:** `#incidents` and `#eng-alerts`.

---

## 5. Import and configure

1. **Import:** open **Workflows → ⋯ → Import from File** and pick `workflow.json`.
2. **Connect credentials** on each node marked ⚠️.
3. **List your services:** edit the Code node **Set Target Services**. There's one line per service:
   ```js
   { json: { service: 'api-gateway', url: 'http://api-gateway:8080/health', container: 'api-gateway' } },
   ```
   `container` must be the exact Docker container name (or ID).
4. **Set the on-call mention:** in **Escalate To On-Call**, replace `<!subteam^ONCALL>` with your user-group ID, e.g. `<!subteam^S04ABCD1234>`. Copy it from Slack → *People → User groups → ⋯ → Copy group ID*, or delete the mention.
5. **Check failure alerts:** the workflow already names itself as its Error Workflow, so the *Workflow Error → Alert Engineering* branch is live. If you imported it through the editor rather than `n8n import:workflow` or `scripts/n8n_sync.py`, n8n may have given it a new ID, so open **⋯ → Settings → Error Workflow** and make sure *this workflow* is selected.
6. **Activate** it (**Publish** in n8n 2.x).

---

## 6. Node reference

| Node | Type | What it does |
|---|---|---|
| Every 5 Minutes | Schedule Trigger | |
| Set Target Services | Code (JS) | One item per monitored service |
| Health Check | HTTP GET, 5 s timeout, full response, *Never Error*, On Error → Continue | Timeouts and 5xx become data (status 0 or 5xx) instead of failing the run |
| Is Unhealthy? | IF | `statusCode != 200` |
| Log Heartbeat (OK) | Sheets `Uptime Log` | ⚠️ ~288 rows per service per day. Disable it or prune the tab |
| Restart Container | HTTP POST `{DOCKER_API_URL}/containers/<container>/restart?t=10`, *Never Error*, On Error → Continue | Docker returns `204` on success. A `404` (unknown container) or an unreachable proxy doesn't stop the run: the re-check still fails and the service is escalated |
| Wait 30s For Recovery | Wait | |
| Re-Check Health | HTTP GET (same settings) | |
| Still Down After Restart? | IF | |
| Escalate To On-Call → Log Incident | Slack `#incidents` → Sheets `Incidents` (`escalated`) | |
| Notify Self-Healed → Log Self-Heal | Slack `#incidents` → Sheets `Incidents` (`self_healed`) | |
| Workflow Error → Alert Engineering | Error Trigger → Slack `#eng-alerts` | Live by default (§5 step 5) |

---

## 7. Test

1. **Healthy path:** click *Execute Workflow*. Each service gets a row in `Uptime Log`.
2. **Self-heal path:** stop a monitored container with `docker stop api-gateway`, then execute. *Restart Container* shows `statusCode: 204`, the re-check passes, and `#incidents` gets "recovered automatically".
3. **Escalation path:** point one service at a dead URL, e.g. `http://localhost:9/health`, then execute. You get an escalation in `#incidents` and a row in `Incidents` with `escalated`.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Every outage escalates and none self-heal | Open *Restart Container* in the execution. `403` means the proxy is missing `POST=1` / `ALLOW_RESTARTS=1`. `404 No such container` means the `container` value is wrong. An error or empty URL means `DOCKER_API_URL` isn't set or the proxy isn't on n8n's network |
| `wget: bad address 'docker-proxy'` | The proxy isn't running, or isn't on the same compose network as n8n |
| Healthy services reported as down | n8n can't resolve the hostname. Put the containers on the `monitored` network, or use `host.docker.internal` |
| On-call isn't pinged | The `<!subteam^…>` ID is still the placeholder |
| Sheets `invalid_grant` after about a week | Publish the Google OAuth app and reconnect |
