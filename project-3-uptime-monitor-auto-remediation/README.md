# Uptime Monitor & Auto-Remediation

**One line:** Polls each service's health endpoint every 5 minutes; on failure it tries an automated fix (`docker restart`) before waking a human, and logs every check and every incident for later MTTR reporting.

## The problem

Basic uptime monitors are good at telling you *that* something is down — they're not good at fixing it, and a lot of production incidents (especially in small teams that self-host their own services) are "the container hung and needed a restart," something a human doesn't need to be paged for at 3am to do.

## The solution

1. **Schedule Trigger** runs every 5 minutes.
2. **Set Target Services** defines the list of monitored services in one place (add/remove a service by editing one node, not the whole workflow).
3. **Health Check** — a GET request to each service's `/health` endpoint.
4. **Branch on status** — a non-200 response triggers remediation; a healthy response just logs a heartbeat.
5. **Attempt self-heal** — `docker restart <container>` via the Execute Command node, then wait 30 seconds and check again.
6. **Branch again** — if the re-check is healthy, log it as "self-healed" and notify `#incidents` (visibility without waking anyone); if it's still down, escalate to on-call and log it as an actual incident.

## Architecture

```
Every 5 min ─▶ Set Target Services ─▶ Health Check ─▶ Unhealthy? ─┬─▶ Restart Container ─▶ Wait 30s ─▶ Re-Check ─▶ Still Down? ─┬─▶ Escalate On-Call ─▶ Log Incident
                                                                    │                                                          └─▶ Notify Self-Healed ─▶ Log Self-Heal
                                                                    └─▶ Log Heartbeat (OK)

[errorTrigger] ─▶ Alert Engineering (Slack)
```

## Tech stack

- **n8n** — orchestration, running in Docker (`docker-compose.yml` at the repo root)
- **Schedule Trigger** — cron-style polling
- **HTTP Request** — health checks (`onError: continueRegularOutput` so a timeout is treated as data, not a workflow crash)
- **Execute Command** — runs `docker restart` on the host n8n is deployed on (or via the Docker socket/API in a hardened version — see below)
- **Google Sheets** — uptime log + incident log
- **Slack** — self-heal notice vs. on-call escalation, deliberately two different channels/tones

## Why it's built this way

- **Self-heal before escalate.** The whole point of this workflow is reducing pages for a class of incident that doesn't need a human — but it still escalates rather than retrying forever, so a genuinely broken deployment doesn't get silently restart-looped.
- **Every check is logged, not just failures.** Without the "OK" heartbeats, you can't calculate real uptime % or prove the monitor itself was running during a gap.
- **Self-heals and real incidents are logged as distinct outcomes.** This is what makes an MTTR/incident report meaningful later — "12 self-heals, 1 real incident this month" is a very different story than "13 pages."

## Security note (read before using `Execute Command` for real)

Running `docker restart` via the **Execute Command** node assumes n8n has shell access to the Docker host, which is a meaningful trust boundary — in a real deployment you'd scope this tightly: run n8n's container with a narrowly-permissioned Docker socket proxy (e.g. `tecnativa/docker-socket-proxy` restricted to `POST /containers/{id}/restart` only) rather than a full Docker socket mount, so a compromised or buggy workflow can't do anything else to the host.

## What I'd improve with more time

- Swap the raw `docker restart` command for a scoped Docker Socket Proxy + HTTP Request call (see security note above).
- Add exponential backoff / a max-restarts-per-hour guard so a genuinely crash-looping service doesn't get restarted indefinitely before escalating.
- Compute and post a weekly uptime % / MTTR summary from the logged sheet data instead of only reacting to individual incidents.

## Running it

1. `docker compose up -d` from the repo root to get a local n8n instance.
2. Import `workflow.json`.
3. Edit the **Set Target Services** node with your real service URLs and container names.
4. Connect Google Sheets and Slack credentials, then activate the workflow.
