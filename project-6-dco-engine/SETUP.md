# Setup Guide: DCO Engine (Creative Performance Loop)

Standalone installation for this one workflow. It works on its own; feeding replacement briefs to the Generative Creative Factory (project 5) is **optional** (see §9).

**What it does:** every 6 hours it pulls ad spend and current ad-set budgets from your ad platform (paginated) and conversions from your affiliate network, and joins them per creative variant. It computes CTR, CVR, CPA, ROAS and EPC, then runs a Bonferroni-corrected significance test. Clear winners get **+20 % budget**, clear losers are **paused**, and everything else is **held**. Variants with too little data are left alone.

> ⚠️ This workflow **changes live ad spend**. Follow the safe test in §7 before you give it a real token.

---

## 1. What you need

| Item | Why |
|---|---|
| A self-hosted n8n (Docker) with Python 3 | *Execute Command* runs `ab_significance.py` (standard library only) |
| An ad platform API | Reads insights, pauses ads, changes ad-set budgets. The request/response shape follows the Meta Marketing API |
| An affiliate network API | Reads conversions and revenue grouped by `sub_id` |
| A Google account | `DCO Decision Log` (and optionally `Creative Briefs`) |
| A Slack workspace where you can install an app | The buying report after each cycle |

---

## 2. Run n8n

Folder layout:

```
dco-engine/
├─ workflow.json
├─ .env
├─ n8n.Dockerfile
├─ docker-compose.yml
└─ data/scripts/ab_significance.py   ← copy from ./scripts/ in this project
```

**`.env`**
```ini
GENERIC_TIMEZONE=UTC
CREATIVE_SHEET_ID=
ADS_API_URL=https://ads.example.com/v1
ADS_API_TOKEN=
AFFILIATE_API_URL=https://affiliate.example.com/v1
AFFILIATE_API_KEY=
DCO_MIN_IMPRESSIONS=1000   # below this (or below 100 clicks) nothing is decided
DCO_TARGET_ROAS=1.3
DCO_SCALE_STEP=1.2         # winners get budget x 1.2
```

**`n8n.Dockerfile`**
```dockerfile
FROM alpine:3.24 AS tools
RUN apk add --no-cache python3 && mkdir /out && cp /usr/bin/python3* /out/

FROM docker.n8n.io/n8nio/n8n:latest
USER root
COPY --from=tools /usr/lib/ /usr/lib/
COPY --from=tools /lib/ /lib/
COPY --from=tools /out/ /usr/bin/
USER node
```

**`docker-compose.yml`**
```yaml
services:
  n8n:
    build: { context: ., dockerfile: n8n.Dockerfile }
    restart: unless-stopped
    ports: ["5678:5678"]
    env_file: .env
    environment:
      - N8N_BLOCK_ENV_ACCESS_IN_NODE=false   # lets {{ $env.* }} resolve
      - NODES_EXCLUDE=[]                      # re-enables Execute Command
    volumes:
      - n8n_data:/home/node/.n8n
      - ./data:/data
volumes:
  n8n_data:
```

```bash
docker compose up -d --build
```

> Already running the whole portfolio? The repo-root compose already has Python and mounts the script. Fill in the variables in the root `.env`.

---

## 3. Credentials and scopes

**Two** n8n credentials, plus two API secrets in `.env`.

| Credential | Type | Nodes |
|---|---|---|
| Ad platform token | `ADS_API_TOKEN` in `.env` (`Authorization: Bearer …`) | Fetch Ad Platform Insights, Fetch Ad Set Budgets, Scale Winner Budget, Pause Losing Ad |
| Affiliate API key | `AFFILIATE_API_KEY` in `.env` (`X-Api-Key` header) | Fetch Affiliate Conversions |
| Google Sheets | Google Sheets OAuth2 API | Log Decision, Log Still Learning, Request Replacement Creative |
| Slack | Slack API | Post Buying Report, Alert Engineering (Slack) |

### 3.1 Ad platform token

| Permission | Needed by | On Meta |
|---|---|---|
| Read ad-level insights | Fetch Ad Platform Insights | `ads_read` |
| Read ad-set budgets | Fetch Ad Set Budgets | `ads_read` |
| Update ad status (pause) | Pause Losing Ad | `ads_management` |
| Update ad-set daily budget | Scale Winner Budget | `ads_management` |

**On Meta:** go to *Business Settings → Users → System Users*, add a system user, and assign it to the ad account with *Manage campaigns*. Then **Generate token** with `ads_read` + `ads_management`. System-user tokens don't expire.

To run the engine **report-only**, issue a read-only token (`ads_read`) and disable the two write nodes (§7).

### 3.2 Affiliate API key

A **read-only reporting** key. The workflow only calls `GET /conversions`.

### 3.3 Google Sheets OAuth2 API

**Scopes** (n8n requests these automatically): `https://www.googleapis.com/auth/spreadsheets` (append rows) and `https://www.googleapis.com/auth/drive.file`.

1. In <https://console.cloud.google.com>, create a project. Under **APIs & Services → Library**, enable **Google Sheets API** and **Google Drive API**.
2. **Google Auth Platform → Branding / Audience**: choose **Internal** or **External** and add yourself as a Test user. ⚠️ Tokens expire after **7 days** while the app is in Testing. **Publish app.**
3. **Credentials → OAuth client ID → Web application**. Redirect URI: `http://localhost:5678/rest/oauth2-credential/callback`.
4. In n8n: **Credentials → Google Sheets OAuth2 API**, paste the ID and secret, then **Sign in with Google**.

### 3.4 Slack API (bot token)

Bot scopes: `chat:write` (**required**), `chat:write.public` (recommended), `channels:read` / `groups:read` (so the channel picker works).

1. Go to <https://api.slack.com/apps> → **Create New App → From scratch**.
2. Add the scopes, then **Install to Workspace**.
3. Copy the `xoxb-…` token.
4. In n8n: **Credentials → Slack API**.

---

## 4. Data conventions (the Code nodes depend on these)

| Convention | Why |
|---|---|
| **Ad name** = `campaign__variantId__placement` (double underscores) | Variants are grouped by the middle part |
| The control variant's ID ends in **`_control`** | The significance test compares every variant against it |
| The affiliate **`sub_id` = the ad platform's `ad_id`** | The revenue join. On Meta, add `sub_id={{ad.id}}` to the tracking URL |
| Insights: `GET {ADS_API_URL}/insights?level=ad&date_preset=last_3d&fields=…` returns `{ data: [...], paging: { next } }` | Pagination follows `paging.next` |
| Budgets: `GET {ADS_API_URL}/adsets?fields=id,daily_budget` returns `{ data: [{ id, daily_budget }], paging: { next } }` | Paginated the same way. An ad set missing from the list means its variant is held rather than scaled |
| Writes: `POST {ADS_API_URL}/adsets/{id}` with `{daily_budget}` and `POST {ADS_API_URL}/ads/{id}` with `{status:"PAUSED"}` | |
| Affiliate: `GET {AFFILIATE_API_URL}/conversions?since=…&group_by=sub_id` returns `{ data \| conversions: [{ sub_id, conversions, revenue \| payout }] }` | |

**Meta specifically:** set `ADS_API_URL=https://graph.facebook.com/v21.0/act_<AD_ACCOUNT_ID>` for the insights and ad-set budget reads. Meta's ad-set and ad write endpoints live at the Graph root (`https://graph.facebook.com/v21.0/<id>`), so change the URLs in **Scale Winner Budget** and **Pause Losing Ad** to that form.

---

## 5. Prepare Google Sheets and Slack

**Spreadsheet** (its ID goes in `CREATIVE_SHEET_ID`):

| Tab | Header row |
|---|---|
| `DCO Decision Log` | `variantId, decision, ctr, cvr, cpa, roas, epc, pValue, lift, decidedAt, reason, impressions, clicks` |
| `Creative Briefs` | `briefId, campaign, subject, status, requestedBy, reason` (a replacement-brief queue; see §9) |

**Slack channels:** `#media-buying` and `#eng-alerts`.

---

## 6. Import and configure

1. **Import:** open **Workflows → ⋯ → Import from File** and pick `workflow.json`.
2. **Connect credentials** on each node marked ⚠️.
3. **Check failure alerts:** the workflow already names itself as its Error Workflow, so the *Workflow Error → Alert Engineering* branch is live. If you imported it through the editor rather than `n8n import:workflow` or `scripts/n8n_sync.py`, n8n may have given it a new ID, so open **⋯ → Settings → Error Workflow** and make sure *this workflow* is selected.
4. Do the safe test in §7, **then** activate it (**Publish** in n8n 2.x).

---

## 7. Safe test first

1. Select **Scale Winner Budget** and **Pause Losing Ad**, then press **D** to disable them.
2. Click **Execute Workflow**.
3. Review `DCO Decision Log` and the report in `#media-buying`. Every variant should show `scale`, `pause`, `hold` or `insufficient_data`, with its p-value and reason.
4. When the decisions match what a human buyer would do, re-enable both nodes and activate.

---

## 8. Node reference

| Node | Type | What it does |
|---|---|---|
| Every 6 Hours | Schedule | Runs all three fetches in parallel |
| Fetch Ad Platform Insights | HTTP GET, Bearer, paginated on `paging.next`, retry 4× / 10 s | Last 3 days, ad level |
| Fetch Ad Set Budgets | HTTP GET `/adsets?fields=id,daily_budget`, Bearer, paginated, retry 4× / 10 s | Current daily budget per ad set, so a winner's budget can be scaled |
| Fetch Affiliate Conversions | HTTP GET, `X-Api-Key`, retry 4× / 10 s | Last 3 days, by `sub_id` |
| Wait For All Pulls | Merge (append, 3 inputs) | Waits for all three pulls |
| Compute Variant Metrics | Code (JS) | Joins spend, budgets and revenue, rolls up per variant, and sets `hasEnoughData` (≥ `DCO_MIN_IMPRESSIONS` impressions **and** ≥ 100 clicks) |
| Enough Data To Decide? → Log Still Learning | IF → Sheets `insufficient_data` | |
| Run Significance Test | Execute Command → `ab_significance.py` (runs once for all variants) | CVR vs. control, α = 0.05, Bonferroni |
| Parse Test Result | Code (JS) | Blocks `scale` when an ad set's budget is unknown (it becomes `hold`) |
| Route Decision | Switch → `scale` / `pause` / `hold` | |
| Scale Winner Budget → Log Decision | HTTP POST `daily_budget × DCO_SCALE_STEP`, retry 3× → Sheets | ⚠️ changes spend |
| Pause Losing Ad → Request Replacement Creative → Log Decision | HTTP POST `PAUSED` → Sheets `Creative Briefs` (`queued`) → Sheets | |
| Hold – No Action → Log Decision | No-Op → Sheets | |
| Post Buying Report | Slack `#media-buying` | One message per cycle |
| Workflow Error → Alert Engineering | Error Trigger → Slack `#eng-alerts` | |

---

## 9. Optional: feeding the Generative Creative Factory

When an ad is paused, **Request Replacement Creative** appends a `queued` brief to `Creative Briefs`. If the client also runs **project 5** on the same spreadsheet, it picks that brief up at 06:00 and generates a replacement, which closes the loop.

Without project 5, either:
- keep the tab as a to-do list of creatives to replace for the design team, or
- delete **Request Replacement Creative** and connect **Pause Losing Ad** straight to **Log Decision**.

---

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| Nothing is ever scaled | *Fetch Ad Set Budgets* returns no `daily_budget` (campaign-level budgets, or a wrong `ADS_API_URL`). Variants without a known budget are held on purpose |
| Every variant `insufficient_data` | The volume floor isn't met. Lower `DCO_MIN_IMPRESSIONS` (the 100-click floor is in the code) |
| Revenue is always 0 | `sub_id` doesn't equal `ad_id`. Fix the tracking URL |
| All variants lumped into one | Ad names don't follow `campaign__variantId__placement` |
| 401/403 on the write nodes | The token lacks `ads_management`, or the system user isn't assigned to the ad account |
| `python3: not found` | Build `n8n.Dockerfile` |
