# Lead Intake & CRM Sync

**One line:** A webhook catches leads from any source, validates and de-duplicates them against a CRM, alerts sales in Slack, and emails the prospect back — all in one automated pass instead of someone manually copying rows from a form-notification inbox.

## The problem

Small sales teams often collect leads from several places (website contact form, a landing page for an ad campaign, a partner referral form) that don't talk to each other. Someone ends up manually checking multiple inboxes, copying details into a spreadsheet or CRM, and — inevitably — sometimes forgetting to reply to a lead for a day or two, or entering the same person twice from two different forms.

## The solution

A single webhook endpoint any form can POST to. From there:

1. **Normalize & Validate** — different forms send different field names (`first_name` vs `firstName`); this step maps them to one shape and checks the email is well-formed.
2. **Branch on validity** — invalid submissions are logged to a `Rejected Submissions` sheet (so nothing silently disappears) instead of polluting the CRM.
3. **Dedupe check** — looks up the email against the existing CRM sheet.
4. **Update or create** — an existing contact gets `lastTouchedAt` and `touchCount` bumped; a new one gets a fresh row.
5. **Notify + confirm** — sales gets a Slack ping with the lead's details, and the prospect gets an automatic confirmation email, both within the same second.
6. **Respond to the form** — the webhook returns a JSON response so the front-end form can show a "thanks, we got it" message.

A separate `errorTrigger` node catches any failure in the workflow itself and posts to `#eng-alerts`, so a broken workflow doesn't fail silently — a lead that gets dropped without anyone noticing is the whole problem this workflow exists to prevent.

## Architecture

```
                                  ┌─▶ Log Invalid Submission ─▶ Respond 400
Webhook ─▶ Normalize & Validate ─┤
 (POST)                          └─▶ Check For Duplicate ─▶ Already In CRM? ─┬─▶ Update Existing Row ─┐
                                                                              └─▶ Create New CRM Row ──┤
                                                                                                        ▼
                                                                        Notify Sales (Slack) ─▶ Send Confirmation Email ─▶ Respond 200

[errorTrigger] ─▶ Alert Engineering (Slack)
```

## Tech stack

- **n8n** — orchestration
- **Webhook** trigger (any form/landing page tool can POST to it — Webflow, Typeform, a custom HTML form, a partner's API)
- **Google Sheets** as a lightweight CRM (swap for HubSpot/Pipedrive/Airtable nodes with the same logic — the branching doesn't change)
- **Slack** for the sales notification and the error alert
- **SMTP** (via the Email Send node) for the prospect confirmation

## Why it's built this way

- **Validation happens before anything touches the CRM.** Garbage-in-garbage-out is the #1 way "automated" CRMs become untrustworthy — reps stop believing the data and go back to spreadsheets.
- **Dedupe by email, not by name.** Names collide; emails are a much safer key for a first pass (a production version would also fuzzy-match on company + name for leads submitted without email, and route those to manual review instead of silently dropping them).
- **Every branch ends somewhere logged.** Nothing in this workflow just disappears — valid, invalid, new, and duplicate leads all land in a sheet you can audit.

## What I'd improve with more time

- Add rate limiting / a CAPTCHA check upstream to stop bot-submitted spam from reaching the CRM at all.
- Replace the Google Sheets "CRM" with a real CRM node (HubSpot/Pipedrive) and add OAuth refresh handling.
- Add a retry-with-backoff wrapper around the Slack/email calls instead of a single error alert, so a transient API hiccup doesn't require rerunning the whole execution.

## Running it

1. Import `workflow.json` into n8n (**Workflows → Import from File**).
2. Connect your own Google Sheets, Slack, and SMTP credentials (the imported nodes reference placeholder credential names — click each node and select/create your own).
3. Activate the workflow and copy the production webhook URL from the **Lead Form Webhook** node into your form's submit action.
4. Test with `curl`:

```bash
curl -X POST https://<your-n8n-host>/webhook/lead-intake \
  -H "Content-Type: application/json" \
  -d '{"firstName":"Ada","lastName":"Lovelace","email":"ada@example.com","company":"Analytical Engines Ltd","source":"landing_page_q4"}'
```
