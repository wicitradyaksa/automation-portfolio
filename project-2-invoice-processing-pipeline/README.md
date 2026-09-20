# Invoice Intake & Validation Pipeline

**One line:** Watches an accounts-payable inbox, extracts the text from incoming invoice PDFs, parses the key fields, and routes each invoice to auto-approval, manager approval, or manual review — instead of someone opening every attachment by hand.

## The problem

Small finance teams often process invoices the same way for years: someone opens every email with an attachment, reads the PDF, types the numbers into a spreadsheet, and decides by eye whether it needs a manager's sign-off. It's slow, error-prone at 4pm on a Friday, and doesn't scale past a handful of invoices a day.

## The solution

1. **Watch the inbox** (IMAP trigger) for new mail.
2. **Filter to attachments** — anything without a PDF attached is left alone (could be a newsletter, a reply, a question).
3. **Extract text** from the PDF.
4. **Parse the fields** — invoice number, vendor, total amount, due date — with a small regex-based parser (deliberately simple and easy to audit; swap in an LLM/OCR call for handwriting or inconsistent vendor formats).
5. **Branch on completeness** — if the parser couldn't confidently extract all fields, the invoice goes to a `Needs Review` sheet and finance ops gets a Slack ping, instead of a half-filled row silently entering the ledger.
6. **Branch on amount** — anything over $1,000 (configurable) goes to a `Pending Approval` sheet and pings a manager in Slack; anything smaller is logged straight to the `Invoice Ledger` as auto-approved.

## Architecture

```
IMAP Trigger ─▶ Has PDF? ─┬─▶ Extract PDF Text ─▶ Parse Fields ─▶ Complete? ─┬─▶ Amount > $1,000? ─┬─▶ Log Approval Queue ─▶ Alert Manager
                          │                                                  │                      └─▶ Log Approved Ledger
                          │                                                  └─▶ Log Needs Review ─▶ Alert Finance Ops
                          └─▶ (no attachment, ignored)

[errorTrigger] ─▶ Alert Engineering (Slack)
```

## Tech stack

- **n8n** — orchestration
- **IMAP (Email Read)** trigger — polls the AP inbox
- **Extract From File** — pulls raw text out of the PDF attachment
- **Code node** — the field-parsing logic (kept in plain JS/regex so it's easy to read and modify without a separate parsing service)
- **Google Sheets** — approval queue, ledger, and review queue
- **Slack** — approval requests and exception alerts

## Why it's built this way

- **The approval threshold is a single number in one IF node**, not buried in logic elsewhere — finance can tell you in one sentence what triggers manual approval, and it's trivial to change as policy changes.
- **"Couldn't parse it" is a first-class outcome, not a crash.** A pipeline that silently records wrong numbers when parsing fails is worse than one that flags its own uncertainty — this one always chooses the safer failure mode.
- **The regex parser is intentionally simple.** For a portfolio/starting point, a transparent parser you can read in thirty seconds beats a black-box one — production use with varied vendor formats would likely swap this node for an LLM extraction call while keeping every downstream node identical.

## What I'd improve with more time

- Add vendor-specific parsing templates (most AP teams have a small, repeating set of vendors) with the regex parser as a fallback.
- Store a copy of the original PDF (e.g., to Google Drive/S3) linked from the ledger row, for audit trail.
- Add duplicate-invoice detection (same vendor + invoice number seen twice) to catch accidental double-submissions.

## Running it

1. Import `workflow.json` into n8n.
2. Connect your own IMAP, Google Sheets, and Slack credentials.
3. Adjust the `$1,000` threshold in the **Needs Manager Approval?** node to match your policy.
4. Activate the workflow and send yourself a test email with a sample invoice PDF attached to confirm the parse.
