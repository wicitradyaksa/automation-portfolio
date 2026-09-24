# Setup Guide: Lead Intake & CRM Sync

Standalone installation for this one workflow. It doesn't depend on any other workflow in this repo.

**What it does:** any web form posts a lead to a webhook. The workflow normalizes and validates it, de-duplicates it against a Google Sheets CRM, alerts sales in Slack, emails the prospect, and replies to the form with `200` (accepted) or `400` (invalid email).

---

## 1. What you need

| Item | Why |
|---|---|
| A self-hosted n8n (Docker) | The workflow reads its sheet ID from `$env`, which n8n Cloud blocks |
| A Google account | The CRM lives in a Google Sheet |
| A Slack workspace where you can install an app | Sales alerts and failure alerts |
| An SMTP mailbox (Gmail, Workspace, SendGrid, SES, …) | The auto-reply to the prospect |
| A public URL for n8n (reverse proxy or tunnel) | Only if real forms on the internet will post to it |

---

## 2. Run n8n

Create a folder containing this `workflow.json` and the two files below.

**`.env`**
```ini
GENERIC_TIMEZONE=UTC
# The long ID in https://docs.google.com/spreadsheets/d/<THIS_PART>/edit
CRM_SHEET_ID=
# Only when n8n is reachable publicly, e.g. https://n8n.yourcompany.com/
# WEBHOOK_URL=
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
      - N8N_BLOCK_ENV_ACCESS_IN_NODE=false   # lets {{ $env.CRM_SHEET_ID }} resolve
    volumes:
      - n8n_data:/home/node/.n8n
volumes:
  n8n_data:
```

```bash
docker compose up -d
```

Open **http://localhost:5678** and create the owner account. n8n reads `.env` only at startup, so run `docker compose up -d` again after you edit it.

> Already running the whole portfolio? The repo-root `docker-compose.yml` covers this too. Just fill in `CRM_SHEET_ID` in the root `.env`.

---

## 3. Credentials and scopes

This workflow needs **three** credentials.

| Credential | n8n type | Nodes |
|---|---|---|
| Google Sheets | Google Sheets OAuth2 API | Check For Duplicate, Update Existing Row, Create New CRM Row, Log Invalid Submission |
| Slack | Slack API | Notify Sales (Slack), Alert Engineering (Slack) |
| SMTP | SMTP | Send Confirmation Email |

### 3.1 Google Sheets OAuth2 API

**Scopes** (n8n requests these automatically):

| Scope | Why |
|---|---|
| `https://www.googleapis.com/auth/spreadsheets` | Read (the duplicate check), append (new lead, rejected submission) and update (touch count) |
| `https://www.googleapis.com/auth/drive.file` | Used by n8n for file-level operations |

1. Go to <https://console.cloud.google.com> and create a project.
2. **APIs & Services → Library**: enable **Google Sheets API** and **Google Drive API**.
3. **Google Auth Platform → Branding / Audience** (the *OAuth consent screen*): choose **Internal** on Google Workspace, otherwise **External**, and add yourself as a **Test user**.
   ⚠️ While an External app is in **Testing**, Google expires its refresh tokens after **7 days**. Click **Publish app** once it works. For an app only you use, the "unverified app" warning is harmless.
4. **Credentials → Create credentials → OAuth client ID** → **Web application**. Authorized redirect URI: `http://localhost:5678/rest/oauth2-credential/callback`. n8n shows the exact value in the credential dialog; use that one if your host differs.
5. In n8n: **Credentials → Add credential → Google Sheets OAuth2 API**, paste the Client ID and secret, click **Sign in with Google**, and approve.

The Google account needs **Editor** access to the CRM spreadsheet.

### 3.2 Slack API (bot token)

| Bot token scope | Required? | Why |
|---|---|---|
| `chat:write` | **Required** | Post messages |
| `chat:write.public` | Recommended | Post to public channels without inviting the bot |
| `channels:read` | Recommended | The n8n channel picker can list channels |
| `groups:read` | Private channels only | Same, for private channels. The bot must also be invited |

1. Go to <https://api.slack.com/apps> → **Create New App → From scratch**.
2. **OAuth & Permissions → Bot Token Scopes**: add the scopes above.
3. **Install to Workspace**, then copy the **Bot User OAuth Token** (`xoxb-…`).
4. In n8n: **Credentials → Add credential → Slack API** → paste it into **Access Token**.

### 3.3 SMTP

SMTP has no scopes. Access comes from the account or key you use.

| Field | Gmail / Workspace | SendGrid | Amazon SES |
|---|---|---|---|
| Host | `smtp.gmail.com` | `smtp.sendgrid.net` | `email-smtp.<region>.amazonaws.com` |
| Port | `465` with SSL/TLS **on** (or `587` with it off for STARTTLS) | `465` / `587` | `465` / `587` |
| User | your address | `apikey` (literally) | SMTP username |
| Password | an **App Password** (needs 2-Step Verification) | an API key with **Mail Send** permission only | an SMTP password from an IAM user allowed `ses:SendRawEmail` |

---

## 4. Prepare Google Sheets and Slack

**Spreadsheet** (its ID goes in `CRM_SHEET_ID`). Tab names and row-1 headers must match exactly:

| Tab | Header row |
|---|---|
| `Leads` | `firstName, lastName, email, company, source, createdAt, status, touchCount, lastTouchedAt` |
| `Rejected Submissions` | `raw, reason, receivedAt` |

**Slack channels:** `#sales-alerts` (lead alerts) and `#eng-alerts` (workflow failures). For private channels, run `/invite @YourBot`.

---

## 5. Import and configure

1. **Import:** open **Workflows → ⋯ → Import from File** and pick `workflow.json`.
2. **Connect credentials:** open each node with a ⚠️ and select your credential (3.1–3.3).
3. **Set the sender:** in **Send Confirmation Email → From Email**, replace `hello@yourcompany.com` with an address your SMTP provider lets you send from.
4. **Check failure alerts:** the workflow already names itself as its Error Workflow, so the *Workflow Error → Alert Engineering* branch is live. If you imported it through the editor rather than `n8n import:workflow` or `scripts/n8n_sync.py`, n8n may have given it a new ID, so open **⋯ → Settings → Error Workflow** and make sure *this workflow* is selected.
5. **Activate** it with the toggle, top right (**Publish** in n8n 2.x).
6. Put the **Production URL** from *Lead Form Webhook* into your form's submit action: `https://<host>/webhook/lead-intake`.

---

## 6. Node reference

| Node | Type | What it does |
|---|---|---|
| Lead Form Webhook | Webhook `POST /lead-intake`, responds via node | Entry point |
| Normalize & Validate | Code (JS) | Accepts `firstName`/`first_name`, `lastName`/`last_name`, `email`, `company`, `source`. Lower-cases the email and checks its format |
| Valid Lead? | IF | `valid == true` |
| Check For Duplicate | Sheets: read `Leads` where `email` matches | *Always Output Data* is on, so "not found" still continues |
| Already In CRM? | IF | Did the lookup return a row? |
| Update Existing Row | Sheets: update `Leads`, matched on `email` | `touchCount + 1`, `lastTouchedAt` |
| Create New CRM Row | Sheets: append `Leads` | `status=new`, `touchCount=1` |
| Notify Sales (Slack) | Slack → `#sales-alerts` | "New lead" or "Returning lead (touch #N)" |
| Send Confirmation Email | Send Email (SMTP) | Plain-text auto-reply |
| Respond 200 OK | Respond to Webhook | `{"status":"received","email":…}` |
| Log Invalid Submission | Sheets: append `Rejected Submissions` | Stores the raw payload |
| Respond 400 Bad Request | Respond to Webhook (400) | `{"status":"rejected","reason":"invalid_email"}` |
| Workflow Error → Alert Engineering | Error Trigger → Slack `#eng-alerts` | Needs step 4 of §5 |

---

## 7. Test

Click **Listen for test event** on the webhook node, then send (or activate the workflow and drop `-test` from the URL):

```bash
curl -X POST http://localhost:5678/webhook-test/lead-intake -H "Content-Type: application/json" -d "{\"firstName\":\"Ada\",\"lastName\":\"Lovelace\",\"email\":\"ada@example.com\",\"company\":\"Analytical Engines\",\"source\":\"landing_page\"}"
```

| Check | Expected |
|---|---|
| First send | `200`, a new `Leads` row, a Slack "New lead" message, an email to the address |
| Same payload again | `touchCount` becomes 2, and Slack says "Returning lead (touch #2)" |
| `"email":"not-an-email"` | `400` and a row in `Rejected Submissions` |

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `access to env vars denied` | `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` is missing. Add it and restart |
| Webhook 404 | Activate the workflow, or click *Listen for test event* before using `/webhook-test/` |
| Sheets `invalid_grant` after about a week | The Google app is still in *Testing*. Publish it and reconnect |
| "Sheet with name … not found" | Tab names are case- and space-sensitive |
| Returning leads create duplicate rows | The `Leads` header row is missing, or has no `email` column |
| Slack `not_in_channel` / `channel_not_found` | Invite the bot or add `chat:write.public`, and check the channel exists |
| SMTP `553` / "sender not allowed" | The From address isn't verified with your provider |
