# Automation Engineering Portfolio

Hi, I'm **Ida Bagus Wicitra Dyaksa** (Dyaksa) — I build automation that survives contact with reality. This repo is seven self-contained n8n workflows covering operations, media production, generative AI, and paid-media optimisation, plus the Bash and Python that does the actual work behind them.

Before this, I spent two years in operations at **BAIADA** (Tamworth, Australia) keeping a high-volume production and logistics operation running to schedule, and six months before that in production at **Primo Foods** (Sydney). Both roles were built on the same thing I now build with n8n: repeatable processes, tight tolerances, and no patience for a step that only works if someone remembers to do it by hand. I hold a degree in Maritime Transportation Engineering from Institut Teknologi Sepuluh Nopember (ITS), where I worked with process simulation and system dynamics modelling (Powersim) — the same systems thinking I now apply to workflow design.

## The thread running through all seven

Connecting app A to app B is the easy part, and it's not what breaks at 3am. Every workflow here is built around the branch where something goes wrong: the data is malformed, the API is rate-limited, the GPU queue is wedged, the render produced a 0-byte file, the backup "succeeded" and is empty, the metric that looks like a winner is sampling noise.

So there are three rules the whole repo follows:

1. **Exit code 0 is not evidence that a job did its work.** Every stage verifies its own output — `ffprobe` the render, checksum-read the archive, assert row counts after the load.
2. **Every workflow has a defined failure path and an `errorTrigger`.** A workflow that fails silently is worse than no workflow at all.
3. **Refusing to act is a feature.** Two of these deliberately do nothing when the evidence is thin, because automation that acts on noise destroys things faster than a human can.

## The projects

### Operations & integration

| # | Project | What it does | Core skills |
|---|---------|--------------|-------------|
| 1 | **[Lead Intake & CRM Sync](./project-1-lead-intake-crm-sync)** | Webhook accepts leads from any form or landing page, validates and de-duplicates them against a CRM, alerts sales in Slack and auto-replies to the prospect. | Webhooks, REST APIs, data validation, idempotency, system integration |
| 2 | **[Invoice Intake & Validation Pipeline](./project-2-invoice-processing-pipeline)** | Watches an inbox, extracts and parses invoice PDFs, routes each to auto-approval, manager approval, or manual review by rule. | Document parsing, conditional business logic, approval workflows, structured logging |
| 3 | **[Uptime Monitor & Auto-Remediation](./project-3-uptime-monitor-auto-remediation)** | Polls health endpoints, attempts a container restart before paging on-call, logs every incident. | Docker, scheduled jobs, escalation logic, observability |

### Media & content automation

| # | Project | What it does | Core skills |
|---|---------|--------------|-------------|
| 4 | **[Media Render Farm (FFmpeg)](./project-4-media-render-farm)** | Expands one master video into every ad format × hook variant, transcodes serially through FFmpeg, then `ffprobe`s each output and rejects anything that doesn't match spec. | FFmpeg, video processing, Bash, Docker, task automation, media automation |
| 5 | **[Generative Creative Factory (ComfyUI)](./project-5-generative-creative-factory)** | Builds the ComfyUI API-format node graph from a spreadsheet row, queues it on the GPU, polls with a hard give-up, post-processes every output into ad placements in Python. | Generative AI workflows, ComfyUI/SDXL, Python, Pillow, async job polling, automated content workflows |

### Growth & performance

| # | Project | What it does | Core skills |
|---|---------|--------------|-------------|
| 6 | **[DCO Engine](./project-6-dco-engine)** | Joins ad spend to affiliate revenue, computes per-variant CTR/CVR/CPA/ROAS/EPC, runs a significance test, then scales, pauses, or holds — and queues replacement creative for anything it pauses. | DCO, A/B testing, media buying, performance marketing, programmatic & affiliate APIs, Python statistics |

### Infrastructure & data

| # | Project | What it does | Core skills |
|---|---------|--------------|-------------|
| 7 | **[VPS Ops & Nightly Data Pipeline](./project-7-vps-ops-data-pipeline)** | SSHes into an Ubuntu VPS, runs a Bash healthcheck returning JSON, self-heals disk pressure, verifies the backup actually contains bytes, runs a Python ETL into Postgres, then asserts data quality and quarantines a bad load. | Linux/Ubuntu VPS, Bash, Python, data pipelines, PostgreSQL, scripting, system integration |

Each project folder holds its own `README.md` (the case study: problem, architecture, trade-offs, what I'd improve) and a `workflow.json` you can import straight into n8n.

## They connect to each other

Projects 4, 5 and 6 are one loop, not three demos:

```
   Creative brief (spreadsheet row)
              │
              ▼
   [5] Generative Creative Factory ──► static placements
              │
              ▼  (briefs with a master video)
   [4] Media Render Farm ──────────► asset manifest
              │
              ▼
   [6] DCO Engine ──► test ──► scale winners / pause losers
              │
              └──────► queues a replacement brief back into [5]
```

A paused creative writes a new row into the same sheet Project 5 reads at 06:00. The pipeline refills itself, and the human's job moves from exporting files and checking dashboards to deciding what's worth testing.

## Skills coverage

Where to look for each thing, and what actually demonstrates it:

| Skill | Where | What demonstrates it |
|---|---|---|
| Workflow Automation | 1–7 | Seven complete n8n workflows, each with explicit failure branches |
| Process Automation | 1, 2, 7 | Manual approval and ops processes mapped and replaced end to end |
| API Integration | 1, 5, 6 | Webhooks, Bearer/API-key auth, pagination, retry with backoff |
| System Integration | 4↔5↔6, 7 | Workflows calling each other's webhooks; SSH into a remote host |
| Scripting | 4, 6, 7 | Six standalone Bash/Python scripts, each runnable from a terminal |
| Data Pipelines | 7 | Log → normalise → idempotent upsert → Postgres → DQ assertions |
| Task Automation | 3, 4, 7 | Scheduled, queued and batched jobs with self-healing |
| **Python** | 5, 6, 7 | `postprocess_creative.py`, `ab_significance.py`, `etl_load.py` |
| **Bash / Shell** | 4, 7 | `render_variant.sh`, `vps_healthcheck.sh`, `backup_volumes.sh` — strict mode, real exit codes |
| **Docker** | 3, 4, 7 | Compose stack, container restart remediation, volume backup via throwaway container |
| **Linux / Ubuntu VPS** | 7 | `df`, `/proc/meminfo`, `journalctl`, `systemctl`, `openssl s_client` |
| **REST APIs** | 1, 5, 6 | Consuming and exposing; pagination, retries, idempotency |
| **Git** | repo | Everything here is version-controlled and reviewable |
| Media Automation | 4, 5 | Master video → variant matrix; generation → placement export |
| Video Processing | 4 | Scale/pad/SAR, loudness normalisation, faststart, thumbnails |
| **FFmpeg** | 4 | The filter chain and encode settings in `render_variant.sh` |
| Automated Content Workflows | 4, 5 | Spreadsheet brief → finished, verified creative with no manual step |
| Generative AI Workflows | 5 | ComfyUI / Stable Diffusion (SDXL) API-format graph built in code; async polling |
| **Dynamic Creative Optimization (DCO)** | 4 → 6 | Variant matrix generation feeding an automated, measured performance loop |
| Media Buying | 6 | Budget scaling, ad pausing, ROAS floors via platform API |
| Performance Marketing | 6 | CPA/ROAS/EPC as the decision metrics, not impressions |
| Programmatic Advertising | 6 | Programmatic budget and status changes through the ads API |
| Affiliate Marketing | 6 | `sub_id` join between click and payout; EPC tracking |
| Traffic Acquisition | 1, 6 | Lead capture at intake; paid acquisition optimisation at the top |
| **A/B Testing** | 6 | Two-proportion z-test with Bonferroni correction, volume floors |
| Campaign Optimization | 6 | Capped +20% scale steps, hard ROAS floor, auto creative refresh |

## Run it yourself

```bash
git clone https://github.com/wicitradyaksa/automation-portfolio.git
cd automation-portfolio
cp .env.example .env        # fill in a login you'll remember
docker compose up -d
```

Open `http://localhost:5678`, log in, then for each project: **Workflows → Import from File** → that project's `workflow.json`.

Every workflow references placeholder credentials (`… (demo)` in the credential name) for Google Sheets / Slack / SMTP / IMAP / SSH / Postgres — swap those for your own, or just read the JSON and the README to follow the logic without connecting real accounts.

The scripts all run standalone, no n8n required:

```bash
# render one variant and verify it
project-4-media-render-farm/scripts/render_variant.sh \
    --src master.mp4 --out story.mp4 --w 1080 --h 1920 --hook "Try it free"

# see the A/B engine hold a 40% "winner" that isn't significant yet
python3 project-6-dco-engine/scripts/ab_significance.py \
    --metric cvr --variants "$(cat project-6-dco-engine/fixtures/variants.json)"

# health-check whatever box you're on
bash project-7-vps-ops-data-pipeline/scripts/vps_healthcheck.sh
```

## Live site

`index.html` at the repo root is the portfolio site. Enable **GitHub Pages** (Settings → Pages → Source: Deploy from a branch → `main` → `/ (root)`) and it's live at `https://wicitradyaksa.github.io/automation-portfolio/` within a couple of minutes — no build step, it's a single static file.

## Repo structure

```
automation-portfolio/
├── index.html                              # the portfolio site (GitHub Pages serves this)
├── docker-compose.yml                      # spin up a local n8n instance
├── requirements.txt                        # Python deps for the scripts
├── .env.example
├── .gitignore
├── project-1-lead-intake-crm-sync/
│   ├── README.md                           # case study
│   └── workflow.json                       # importable n8n workflow
├── project-2-invoice-processing-pipeline/
│   ├── README.md
│   └── workflow.json
├── project-3-uptime-monitor-auto-remediation/
│   ├── README.md
│   └── workflow.json
├── project-4-media-render-farm/
│   ├── README.md
│   ├── workflow.json
│   └── scripts/
│       └── render_variant.sh               # FFmpeg encode + verify
├── project-5-generative-creative-factory/
│   ├── README.md
│   ├── workflow.json
│   └── scripts/
│       └── postprocess_creative.py         # download, strip metadata, export placements
├── project-6-dco-engine/
│   ├── README.md
│   ├── workflow.json
│   ├── fixtures/
│   │   └── variants.json                   # sample data for the significance test
│   └── scripts/
│       └── ab_significance.py              # z-test + scale/pause/hold decisions
└── project-7-vps-ops-data-pipeline/
    ├── README.md
    ├── workflow.json
    └── scripts/
        ├── vps_healthcheck.sh              # JSON-emitting server healthcheck
        ├── backup_volumes.sh               # verified Docker volume backup
        └── etl_load.py                     # idempotent log → Postgres ETL
```

## What's next on this portfolio

- [ ] Swap placeholder credentials for a real (sandboxed) Google Sheet + Slack workspace and record a 60-second Loom of each workflow actually running
- [ ] Add real before/after numbers from a live use case — hours saved per week, error rate, turnaround time
- [ ] Restore-test the Project 7 backups monthly and publish the result, because an untested restore path isn't a backup
- [ ] Replace the frequentist test in Project 6 with a Beta-Binomial posterior and compare the decisions the two would have made

## License

[MIT](./LICENSE) — use these workflows and scripts freely, including for commercial purposes.

## Contact

**Ida Bagus Wicitra Dyaksa (Dyaksa)** — [wicitradyaksa@gmail.com](mailto:wicitradyaksa@gmail.com) · [linkedin.com/in/ida-bagus-wicitra-dyaksa-063458129](https://www.linkedin.com/in/ida-bagus-wicitra-dyaksa-063458129/) · [github.com/wicitradyaksa](https://github.com/wicitradyaksa)

---
*These workflows and scripts were built with Claude's help as a working starting point. The architecture, failure handling, and trade-offs documented here are real and the code runs — but read it, run it, and break it yourself before you discuss it in an interview. Every case study has a "why it's built this way" section for exactly that reason.*
