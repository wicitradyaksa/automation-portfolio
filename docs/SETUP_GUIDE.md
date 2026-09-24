# Setup Guides

Every workflow installs **on its own**. Each project folder has a standalone `SETUP.md` with its own Docker setup, `.env`, credentials and scopes, sheet layout, node reference, test steps and troubleshooting. A client who needs only one workflow gets that folder and nothing else.

| # | Workflow | Guide | n8n credentials | Extra services |
|---|---|---|---|---|
| 1 | Lead Intake & CRM Sync | [SETUP.md](../project-1-lead-intake-crm-sync/SETUP.md) | Google Sheets, Slack, SMTP | — |
| 2 | Invoice Intake & Validation | [SETUP.md](../project-2-invoice-processing-pipeline/SETUP.md) | IMAP, Google Sheets, Slack | — |
| 3 | Uptime Monitor & Auto-Remediation | [SETUP.md](../project-3-uptime-monitor-auto-remediation/SETUP.md) | Google Sheets, Slack | Docker socket proxy |
| 4 | Media Render Farm (FFmpeg) | [SETUP.md](../project-4-media-render-farm/SETUP.md) | Google Sheets, Slack | Asset store (token in `.env`) |
| 5 | Generative Creative Factory | [SETUP.md](../project-5-generative-creative-factory/SETUP.md) | Google Sheets, Slack | ComfyUI |
| 6 | DCO Engine | [SETUP.md](../project-6-dco-engine/SETUP.md) | Google Sheets, Slack | Ad platform + affiliate API (keys in `.env`) |
| 7 | VPS Ops & Nightly Data Pipeline | [SETUP.md](../project-7-vps-ops-data-pipeline/SETUP.md) | SSH, Postgres, Google Sheets, Slack | Ubuntu VPS, PostgreSQL |

## Optional links between workflows

None of these are required. Each guide explains how its workflow behaves without the others.

| From → To | How | Without the other workflow |
|---|---|---|
| 5 → 4 | Briefs with a `videoSourcePath` are POSTed to 4's `/webhook/render-request` | Leave `videoSourcePath` empty; the hand-off is skipped |
| 6 → 5 | A paused ad appends a `queued` brief to the `Creative Briefs` tab | The tab becomes a to-do list, or delete that node |

## Running several (or all) on one n8n

The repo-root [`docker-compose.yml`](../docker-compose.yml) runs one n8n with everything workflows 1–7 need (FFmpeg, Python + Pillow, script mounts, `$env` access, Execute Command). Fill in the variables you use in the root `.env` (from [`.env.example`](../.env.example)). Then import everything with:

```bash
py scripts/n8n_sync.py push
```

Create each credential **once** (one Google Sheets and one Slack credential cover every workflow), then follow the *Prepare…*, *Import and configure* and *Test* sections of each guide. Workflow 3's Docker access and workflow 7's VPS setup still apply.

## Checks that apply to every workflow

- **Error alerts are on by default.** Each workflow names itself as its error workflow. After an import through the editor (which can assign a new ID), check **⋯ → Settings → Error Workflow**.
- **Publish the Google OAuth app.** While it's in *Testing*, refresh tokens expire after 7 days and every Sheets node fails with `invalid_grant`.
