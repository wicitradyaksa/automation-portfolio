# Setup Guide: Invoice Intake & Validation Pipeline

Standalone installation for this one workflow. It doesn't depend on any other workflow in this repo.

**What it does:** it watches an Accounts Payable inbox, extracts the text of each PDF invoice, and parses out the invoice number, vendor, amount and due date. It then routes each invoice one of three ways: **auto-approved** into the ledger, **manager approval** in Slack for anything over $1,000, or **manual review** when the parse is incomplete.

---

## 1. What you need

| Item | Why |
|---|---|
| A self-hosted n8n (Docker) | The workflow reads its sheet ID from `$env`, which n8n Cloud blocks |
| A dedicated AP mailbox with IMAP access | The trigger. It marks **every** unread message as read |
| A Google account | The approval queue, ledger and review queue are Google Sheets tabs |
| A Slack workspace where you can install an app | Approval and review alerts |

---

## 2. Run n8n

Create a folder containing this `workflow.json` and the two files below.

**`.env`**
```ini
GENERIC_TIMEZONE=UTC
# The long ID in https://docs.google.com/spreadsheets/d/<THIS_PART>/edit
FINANCE_SHEET_ID=
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
      - N8N_BLOCK_ENV_ACCESS_IN_NODE=false   # lets {{ $env.FINANCE_SHEET_ID }} resolve
    volumes:
      - n8n_data:/home/node/.n8n
volumes:
  n8n_data:
```

```bash
docker compose up -d
```

Open **http://localhost:5678** and create the owner account.

> Already running the whole portfolio? The repo-root `docker-compose.yml` covers this too. Just fill in `FINANCE_SHEET_ID` in the root `.env`.

---

## 3. Credentials and scopes

This workflow needs **three** credentials.

| Credential | n8n type | Nodes |
|---|---|---|
| IMAP | IMAP | Watch Invoices Inbox |
| Google Sheets | Google Sheets OAuth2 API | Log To Approval Queue, Log To Approved Ledger, Log To Needs Review |
| Slack | Slack API | Alert Finance Manager, Alert Finance Ops, Alert Engineering (Slack) |

### 3.1 IMAP

| Field | Value |
|---|---|
| Host / Port / SSL | e.g. `imap.gmail.com` / `993` / SSL **on** |
| User | the AP mailbox address |
| Password | an **App Password** on Gmail/Workspace (needs 2-Step Verification), or the mailbox password on hosts that allow it |

**Permission needed:** read **and write** access to `INBOX`. The trigger marks processed mail as read (`\Seen`); with read-only access it would re-process the same invoices on every poll.

- Gmail: **Settings → Forwarding and POP/IMAP → Enable IMAP**.
- Microsoft 365 / Outlook.com no longer allow password IMAP. Replace the trigger with n8n's **Microsoft Outlook Trigger** (OAuth2, Graph scopes `Mail.ReadWrite` + `offline_access`).

### 3.2 Google Sheets OAuth2 API

**Scopes** (n8n requests these automatically): `https://www.googleapis.com/auth/spreadsheets` (append rows) and `https://www.googleapis.com/auth/drive.file`.

1. Go to <https://console.cloud.google.com> and create a project.
2. **APIs & Services → Library**: enable **Google Sheets API** and **Google Drive API**.
3. **Google Auth Platform → Branding / Audience**: choose **Internal** on Workspace, otherwise **External**, and add yourself as a **Test user**.
   ⚠️ While the app is in **Testing**, refresh tokens expire after **7 days**. Click **Publish app** once it works.
4. **Credentials → Create credentials → OAuth client ID** → **Web application**. Redirect URI: `http://localhost:5678/rest/oauth2-credential/callback` (copy the exact value from the n8n credential dialog).
5. In n8n: **Credentials → Add credential → Google Sheets OAuth2 API**, paste the ID and secret, then **Sign in with Google**.

The Google account needs **Editor** access to the finance spreadsheet.

### 3.3 Slack API (bot token)

| Bot token scope | Required? | Why |
|---|---|---|
| `chat:write` | **Required** | Post messages |
| `chat:write.public` | Recommended | Post to public channels without inviting the bot |
| `channels:read` / `groups:read` | Recommended / private channels only | The n8n channel picker can list channels |

1. Go to <https://api.slack.com/apps> → **Create New App → From scratch**.
2. **OAuth & Permissions → Bot Token Scopes**: add the scopes above.
3. **Install to Workspace**, then copy the `xoxb-…` token.
4. In n8n: **Credentials → Slack API** → paste it into **Access Token**.

Finance channels are often private. Run `/invite @YourBot` in each one.

---

## 4. Prepare Google Sheets and Slack

**Spreadsheet** (its ID goes in `FINANCE_SHEET_ID`):

| Tab | Header row |
|---|---|
| `Pending Approval` | `invoiceNumber, vendor, amount, dueDate, status` |
| `Invoice Ledger` | `invoiceNumber, vendor, amount, dueDate, status` |
| `Needs Review` | `vendorGuess, extractedText, reason, receivedAt` |

**Slack channels:** `#finance-approval` (over-threshold invoices), `#finance-ops` (couldn't parse), and `#eng-alerts` (workflow failures).

---

## 5. Import and configure

1. **Import:** open **Workflows → ⋯ → Import from File** and pick `workflow.json`.
2. **Connect credentials** on each node marked ⚠️.
3. **Set your approval policy:** the threshold is the `1000` in **Needs Manager Approval?**.
4. **Check failure alerts:** the workflow already names itself as its Error Workflow, so the *Workflow Error → Alert Engineering* branch is live. If you imported it through the editor rather than `n8n import:workflow` or `scripts/n8n_sync.py`, n8n may have given it a new ID, so open **⋯ → Settings → Error Workflow** and make sure *this workflow* is selected.
5. **Activate** it (**Publish** in n8n 2.x).

---

## 6. Node reference

| Node | Type | What it does |
|---|---|---|
| Watch Invoices Inbox | Email Trigger (IMAP), `INBOX`, mark as read, download attachments | Polls for unread mail |
| Has PDF Attachment? | IF | Checks the **first** attachment (`attachment_0`) for a PDF MIME type |
| Extract PDF Text | Extract From File (PDF) | Needs a text-based PDF. Scans produce empty text and go to review |
| Parse Invoice Fields | Code (JS, regex) | Looks for `Invoice #…`, `Total (due) …1,234.56`, `Due date: MM/DD/YYYY`, and takes the vendor from the first line |
| Fields Complete? | IF | Invoice number, amount and due date were all found |
| Needs Manager Approval? | IF | `amount > 1000` |
| Log To Approval Queue → Alert Finance Manager | Sheets `Pending Approval` → Slack `#finance-approval` | |
| Log To Approved Ledger | Sheets `Invoice Ledger` | `status=auto_approved` |
| Log To Needs Review → Alert Finance Ops | Sheets `Needs Review` → Slack `#finance-ops` | Keeps up to 5,000 characters of the text |
| Log Non-Invoice Email | No-Op | Ends the run for mail without a PDF |
| Workflow Error → Alert Engineering | Error Trigger → Slack `#eng-alerts` | Needs step 4 of §5 |

---

## 7. Test

Email a **text** PDF to the mailbox containing, for example:

```
Acme Supplies Ltd
Invoice # INV-20931
Due Date: 10/15/2026
Total Due: $1,250.00
```

Click **Execute Workflow** to fetch once. Expected: a row in `Pending Approval` and an alert in `#finance-approval`.
A version with `Total Due: $250.00` lands in `Invoice Ledger`. A PDF without a due date lands in `Needs Review` and alerts `#finance-ops`.

---

## 8. Known limits and troubleshooting

| Symptom | Cause / fix |
|---|---|
| Invoices re-processed every poll | The mailbox is read-only. The credential needs write access to set `\Seen` |
| Invoice ignored | It wasn't the first attachment, or it isn't a PDF. Only `attachment_0` is checked |
| Always lands in *Needs Review* | A scanned PDF (no text layer), or a layout the regexes don't match. Adapt *Parse Invoice Fields* or add OCR |
| `access to env vars denied` | Add `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` and restart |
| Sheets `invalid_grant` after about a week | Publish the Google OAuth app and reconnect |
| Slack `not_in_channel` | `/invite @YourBot` in the private channel |
