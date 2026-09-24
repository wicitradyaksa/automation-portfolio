# ⚡ Uptime Monitor & Auto-Remediation: Restart It, Verify It, and Only Then Wake Someone Up

[![n8n](https://img.shields.io/badge/n8n-v1.0%2B-FF6D5A?logo=n8n)](https://n8n.io)
[![Nodes](https://img.shields.io/badge/Nodes-15-informational)](./workflow.json)
[![Docker](https://img.shields.io/badge/Docker-self--heal-2496ED?logo=docker)](../docker-compose.yml)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)

> **Quick Summary:** A scheduled n8n workflow that health-checks every registered service every 5 minutes. On failure it restarts the container through the Docker Engine API (via a restart-only socket proxy), waits 30 seconds, and re-checks. It posts a self-heal notice if that worked and escalates to on-call if it didn't. Every heartbeat, self-heal and incident is logged separately so uptime % and MTTR can be computed later.

---

## 📷 Workflow Preview

<!-- Add docs/images/workflow-screenshot.png after the first run with live credentials -->
*Download the ready-to-import n8n workflow file: [`workflow.json`](./workflow.json)*

---

## 🎯 Business Problem & Impact

* **The Challenge:** Basic uptime monitors tell you *that* something is down, not how to fix it. In small self-hosted teams, many incidents are simply "the container hung," which a restart fixes. Paging a human at 3am for that is expensive and wears people out.
* **The Solution:** A tiered response of detect → self-heal → verify → escalate. Remediation is always confirmed by a re-check, and it is never looped forever.
* **Impact & ROI:**
  * **Detection latency:** **≤ 5 minutes**, set by the schedule interval.
  * **Hung-container incidents:** Resolved in **about 30 seconds with zero pages** *(design target: restart + 30 s wait + re-check)*.
  * **Pager load:** On-call is paged **only after automated remediation has provably failed**.
  * **Reporting:** Heartbeats, self-heals and incidents are logged as distinct outcomes, so "12 self-heals, 1 real incident this month" can be reported instead of "13 pages".

---

## 🏗️ Workflow Architecture

```mermaid
graph TD
    A[Schedule Trigger: Every 5 Minutes] --> B[Code: Set Target Services]
    B --> C[HTTP Request: Health Check<br/>onError: continue]
    C --> D{Is Unhealthy?<br/>status ≠ 200}
    D -- No --> E[Sheets: Log Heartbeat OK]
    D -- Yes --> F[HTTP Request: Docker API restart<br/>via socket proxy · never errors]
    F --> G[Wait 30s For Recovery]
    G --> H[HTTP Request: Re-Check Health]
    H --> I{Still Down After Restart?}
    I -- Yes --> J[Slack: Escalate To On-Call]
    J --> K[Sheets: Log Incident]
    I -- No --> L[Slack: Notify Self-Healed]
    L --> M[Sheets: Log Self-Heal]
    X[Error Trigger: Workflow Error] --> Y[Slack: #eng-alerts]
```

---

## ⚙️ Key Technical Features

* **Service registry in one Code Node:** `Set Target Services` returns one item per service (`service`, `url`, `container`). To add or remove a service you edit one node, and n8n fans the rest of the workflow out per item.
* **Timeouts as data, not crashes:** Both health checks return the full response with *Never Error* on (so a 503 is data), and the node setting *On Error → Continue* turns a refused connection into an item with no `statusCode`, which the IF treats as `0`, meaning unhealthy.
* **Least-privilege remediation:** `Restart Container` is an HTTP Request to `POST /containers/{name}/restart` on a Docker socket proxy that allows restarts and nothing else. n8n never touches the Docker socket or a shell.
* **Restart failures still escalate:** The restart runs with *Never Error* and *On Error → Continue*, so a 404, a 403 or an unreachable proxy flows into the re-check and pages on-call instead of stopping the workflow.
* **Verified remediation:** A restart is never reported as a fix until the `Re-Check Health` request confirms it.
* **Two different messages for two different situations:** A self-heal is an FYI post in `#incidents`. A failed self-heal is an escalation to on-call.
* **Resilient error handling:** The **Error Trigger** alerts `#eng-alerts` if the monitor itself breaks, because a silent monitor is worse than none.

---

## 🧠 Why It's Built This Way

* **Self-heal before escalate, but escalate.** It removes pages for a class of incident that doesn't need a human, without restart-looping a genuinely broken deployment.
* **Log every check, not just failures.** Without "OK" heartbeats you can't compute real uptime %, and you can't prove the monitor was running during a gap.
* **Keep outcomes distinct.** Self-heal and incident are separate log rows, which is what makes an MTTR report meaningful.

### 🔒 Security note: why a socket proxy
Mounting the Docker socket into n8n would give anyone who can edit a workflow root on the host. Instead, [`tecnativa/docker-socket-proxy`](https://github.com/Tecnativa/docker-socket-proxy) owns the socket with `CONTAINERS=1, POST=1, ALLOW_RESTARTS=1`, and n8n reaches it over the internal Docker network as `DOCKER_API_URL`. Don't publish the proxy's port.

---

## 🔐 Prerequisites & Environment Variables

n8n v1.0+, running on (or with scoped access to) the Docker host. See the repo-root [`docker-compose.yml`](../docker-compose.yml).

| Credential (placeholder name in JSON) | Type | Used by |
| :--- | :--- | :--- |
| `Google Sheets - Ops (demo)` | Google Sheets OAuth2 | Heartbeat, Incident and Self-Heal logs |
| `Slack - Ops Workspace (demo)` | Slack API (`chat:write`) | `#incidents`, `#eng-alerts` |
| `OPS_SHEET_ID` | Environment variable: the ops Google Sheet (`Uptime Log`, `Incidents` tabs) | All Sheets nodes |
| `DOCKER_API_URL` | Environment variable: the restart-only Docker socket proxy, e.g. `http://docker-proxy:2375` | Restart Container |

**Note:** the repo-root compose file runs the `docker-proxy` service next to n8n. See [`SETUP.md`](./SETUP.md) for a standalone setup.

Environment variables reach the workflow as `$env.NAME` through the repo-root [`docker-compose.yml`](../docker-compose.yml) (`env_file: .env`, see [`.env.example`](../.env.example)), which also sets `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`.

> **Error Trigger:** the workflow names itself as its error workflow (`settings.errorWorkflow`), so failures alert Slack out of the box. Importing through the editor can give the workflow a new ID. If so, open **Workflow Settings → Error Workflow** and select this workflow again (or a shared error-handler workflow).

---

## 🚀 Quick Start / How to Import

> **Full standalone installation guide:** [`SETUP.md`](./SETUP.md) covers every credential with its scopes, the sheet layout, a Docker setup for this workflow only, a node-by-node reference and test steps.

1. `docker compose up -d` from the repo root.
2. **Import** [`workflow.json`](./workflow.json) via **`...` → Import from File**.
3. **Edit `Set Target Services`** with your real health URLs and container names.
4. **Map credentials** for Google Sheets and Slack, then **Activate**.
5. **Test the self-heal path:** `docker stop <container>` makes the health check fail. The restart should bring it back, and you should see a self-heal notice.

---

## 🧪 Edge Cases & Testing Strategy

| Scenario | Handled By | Outcome |
| :--- | :--- | :--- |
| Service returns non-200 | `Is Unhealthy?` | Restart → wait 30 s → re-check |
| Service unreachable / request times out | HTTP Request `onError: continueRegularOutput` | Treated as unhealthy, not a workflow crash |
| Restart fixes it | `Still Down After Restart?` = false | `#incidents` FYI + Self-Heal log row, no page |
| Restart doesn't fix it | `Still Down After Restart?` = true | On-call escalation + Incident log row |
| Monitor workflow itself fails | **Error Trigger** → `#eng-alerts` | Engineering alerted |

---

## 🛣️ Roadmap / v2 Hardening (not yet built)

* **Restart budget:** A max-restarts-per-hour guard with backoff, so a crash-looping service escalates quickly instead of being restarted repeatedly.
* **Weekly SLO digest:** A sub-workflow that computes uptime % and MTTR from the logs and posts them to Slack.
* **Metrics export:** Emit run and incident events to the [Project 9](../project-9-observability-layer) Prometheus exporter.

---

## 📄 License
Distributed under the [MIT License](../LICENSE).

[← Back to portfolio](../README.md)
