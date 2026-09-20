# Automation Engineering Portfolio

Hi, I'm **Ida Bagus Wicitra Dyaksa** (Dyaksa) — I'm pivoting into automation/workflow engineering, and this repo is my starter portfolio: three self-contained n8n workflows that solve real operational problems, plus the setup to run them yourself in under five minutes.

Before this, I spent two years in operations at **BAIADA** (Tamworth, Australia) keeping a high-volume production and logistics operation running to schedule, and six months before that in production at **Primo Foods** (Sydney). Both roles were built on the same thing I now build with n8n: repeatable processes, tight tolerances, and no patience for a step that only works if someone remembers to do it by hand. I hold a degree in Maritime Transportation Engineering from Institut Teknologi Sepuluh Nopember (ITS), where I worked with process simulation and system dynamics modeling (Powersim) — the same systems-thinking I now apply to workflow design in n8n. Moving into automation engineering is me doing full-time what I kept doing informally on the floor: turning a manual, error-prone step into something that just runs.

## What's here

| # | Project | Problem it solves | Core skills shown |
|---|---------|--------------------|--------------------|
| 1 | [Lead Intake & CRM Sync](./project-1-lead-intake-crm-sync) | Leads land in a webhook from any form/landing page, get validated, de-duplicated against a CRM, and routed to sales — with a confirmation email sent automatically. | Webhooks, data validation, idempotency/dedupe logic, Slack + email integration, REST API design |
| 2 | [Invoice Intake & Validation Pipeline](./project-2-invoice-processing-pipeline) | Watches an inbox for incoming invoice PDFs, extracts and parses the key fields, and routes anything over a dollar threshold to a manager for approval. | Document/text processing, conditional business logic, approval workflows, structured logging |
| 3 | [Uptime Monitor & Auto-Remediation](./project-3-uptime-monitor-auto-remediation) | Polls service health endpoints on a schedule, attempts a self-heal (container restart) before paging a human, and logs every incident. | Scheduled jobs, Docker orchestration, escalation logic, observability/MTTR logging |

Each project folder has its own `README.md` (the case study: problem, architecture, trade-offs) and a `workflow.json` you can import straight into n8n.

## Why these three

Automation engineering isn't just "connect app A to app B" — it's handling the branch where the data is missing, the API is down, or the human needs to be looped in. These three were picked to cover different failure-handling patterns end to end:

- **Project 1** handles *bad input* (validation, dedupe) and *dual audiences* (sales gets an alert, the prospect gets an email).
- **Project 2** handles *uncertain extraction* (can the fields even be parsed?) and *human-in-the-loop approval* for anything above a risk threshold.
- **Project 3** handles *infrastructure failure* directly — attempting an automated fix before escalating, which is the difference between a workflow that pages someone at 3am for a problem it could have fixed itself, and one that doesn't.

Every workflow also has its own `errorTrigger` node wired to a Slack alert, because a workflow that fails silently is worse than no workflow at all.

## Run it yourself

You said you already have Docker and n8n — if you want a clean, disposable instance to import these into instead of your existing one:

```bash
git clone https://github.com/wicitradyaksa/automation-portfolio.git
cd automation-portfolio
cp .env.example .env        # fill in a login you'll remember
docker compose up -d
```

Open `http://localhost:5678`, log in, then for each project: **Workflows → Import from File** → select that project's `workflow.json`.

Every workflow references placeholder credentials (`... (demo)` in the credential name) for Google Sheets / Slack / SMTP / IMAP — swap those for your own, or just read the JSON and the README to see the logic without connecting real accounts.

## Repo structure

```
automation-portfolio/
├── docker-compose.yml          # spin up a local n8n instance
├── .env.example
├── project-1-lead-intake-crm-sync/
│   ├── README.md               # case study
│   └── workflow.json           # importable n8n workflow
├── project-2-invoice-processing-pipeline/
│   ├── README.md
│   └── workflow.json
└── project-3-uptime-monitor-auto-remediation/
    ├── README.md
    └── workflow.json
```

## What's next on this portfolio

- [ ] Swap placeholder credentials for a real (sandboxed) Google Sheet + Slack workspace and record a 60-second Loom of each workflow actually running
- [ ] Add real "before/after" numbers once used on a real (even personal) use case — e.g. hours saved per week, error rate, turnaround time
- [ ] Add a 4th project in a category you have real experience in (e.g. e-commerce order ops, HR onboarding, support ticket triage)

## Contact

**Ida Bagus Wicitra Dyaksa (Dyaksa)** — [wicitradyaksa@gmail.com](mailto:wicitradyaksa@gmail.com) · [linkedin.com/in/ida-bagus-wicitra-dyaksa-063458129](https://www.linkedin.com/in/ida-bagus-wicitra-dyaksa-063458129/) · [github.com/wicitradyaksa](https://github.com/wicitradyaksa)

---
*This repo was scaffolded with Claude's help as a starting point — the workflow logic, case studies, and code are meant to be reviewed, tested, and adapted by you before you present them as your own work in an application.*
