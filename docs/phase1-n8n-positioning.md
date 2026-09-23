# Phase 1 — n8n Workflow Automation Engineer: Positioning Pack

Source material: `Ida_Bagus_Wicitra_Dyaksa_CV.docx`, `Linkedin Profile.pdf`, `Readme_Template.md`, and the projects in this repo (node lists checked against each `workflow.json`).

**How the numbers in this document are sourced** (so every one survives an interview question):

| Tag | Meaning |
|---|---|
| *(no tag)* | A real result from employment (LinkedIn/CV), or a fact you can verify in the repo: node counts, test counts, thresholds in the JSON |
| **(benchmark)** | Produced by the Project 10 impact model: the method is real, but the dataset is synthetic. Replace these with real measurements when you have them |
| **(design target)** | What the workflow is built to do. Not measured in production yet |
| **v2** | A planned hardening step that is **not** in the `workflow.json` yet (a candidate for Phase 2) |

---

## 1. Portfolio Hero Section & Headline

### Headline

> **n8n & AI Workflow Automation Engineer — Production-Grade API Integrations & Self-Healing Pipelines**

Shorter variants for LinkedIn/job boards:
- *n8n Workflow Automation Engineer | APIs · Webhooks · Code Nodes · Docker*
- *Workflow Automation Engineer (n8n) — I build the failure path first*

### Summary (3 sentences)

> I design and ship event-driven n8n workflows that connect REST APIs, webhooks, databases and AI services, extending n8n with JavaScript Code Nodes, Bash/Python scripts and HTTP-called microservices wherever the built-in nodes stop. Every workflow I build validates its input, verifies its own output, routes failures through an Error Trigger to Slack, and self-heals before paging a human: 7 importable workflows, 126 nodes, 223 automated tests and a workflow linter back the patterns. Before automation, I ran high-volume operations (24.5 t/day) and paid-media programs where rule-based automation cut wasted ad spend by 60%, so I build for measurable outcomes like hours returned, errors removed and payback in weeks.

---

## 2. Core Competencies & Tech Stack Matrix

| Category | Skills | Proven in |
|---|---|---|
| **Core Automation (n8n)** | n8n self-hosted on Docker Compose (Cloud-compatible exports) · Webhook & Respond-to-Webhook (sync 200/400 and async 202 ack patterns) · Schedule & IMAP triggers · IF / Switch / Merge routing · Split In Batches loops · Wait-based polling · **Error Trigger on 7/7 workflows** · cross-workflow chaining via webhooks | P1–P7 |
| **API & Protocols** | REST (GET/POST, JSON), Bearer/API-key auth · **OAuth2 refresh with single-flight locking** · **HMAC-SHA256 webhook verification + replay window** · **cursor pagination with loop guards** · **429 / `Retry-After` handling, capped exponential backoff + full jitter** · idempotency keys · per-node retry (×3/×4) | P1, P4–P6, **P8** |
| **Custom Code & Logic** | JavaScript Code Nodes (payload normalization, field mapping, regex extraction, metric computation, programmatic graph building) · Python & Bash called from Execute Command / SSH nodes · JSON-in/JSON-out script contracts · statistics (two-proportion z-test, Bonferroni, Wilson intervals) | P1, P2, P4–P7, P10 |
| **Databases & Data** | PostgreSQL (n8n Postgres node, idempotent upserts, SQL data-quality assertions, quarantine) · Google Sheets as an operational store/audit log | P7, P10 |
| **AI & Generative** | ComfyUI / SDXL via HTTP API (graph generated in a Code Node, async polling with hard give-up) · **v2:** OpenAI / LangChain nodes for invoice-field extraction and lead scoring, vector store (Supabase pgvector) for vendor-template retrieval | P5, v2 on P1/P2 |
| **Reliability & Observability** | Error Trigger → Slack alerting · self-heal → re-check → escalate · output verification (ffprobe, checksum, row counts) · Prometheus metrics, 7 alert rules, Grafana, correlation IDs across workflows · on-call runbook | P3, P4, P7, **P9** |
| **Infra & DevOps** | Docker, Docker Compose, Linux/Ubuntu VPS, SSH, systemd/journald · Git/GitHub | P3, P7 |
| **Process & Business** | Process mapping, bottleneck analysis, before/after ROI modelling · paid media: CPA / ROAS / EPC, A/B testing | P6, P10, work history |

---

## 3. Reframed Case Studies

Each case study follows the same shape: **Problem → n8n Architecture → Technical Highlights → Impact → v2 Hardening**. Node names match the `workflow.json` in each folder.

---

### P1 — Real-Time Lead Capture & CRM De-Duplication Pipeline
*`project-1-lead-intake-crm-sync` · 14 nodes*

**The Problem.** Leads arrive from several forms with different field names. Someone copies them into a CRM by hand, replies late, and enters the same person twice.

**n8n Architecture**
1. **Webhook** (`POST /lead-intake`) accepts any form or partner API.
2. **Code Node: Normalize & Validate** maps `first_name`/`firstName`-style variants to one schema and checks the email with a regex.
3. **IF: Valid?** Invalid submissions go to **Google Sheets: Rejected Submissions**, then **Respond to Webhook 400**.
4. **Google Sheets lookup → IF: Already in CRM?** is the de-duplication step, keyed on email.
5. A duplicate goes to **Update Existing Row** (`lastTouchedAt`, `touchCount++`). A new lead goes to **Create New CRM Row**.
6. **Slack** notifies sales, **Email Send** sends the prospect an auto-reply, and **Respond to Webhook 200** returns JSON to the form.
7. **Error Trigger → Slack `#eng-alerts`**.

**Technical Highlights.** Idempotent upsert on a natural key. Validation happens before any write. Every branch ends in a logged sheet, so no lead can disappear silently. The webhook returns a synchronous response the front end can use.

**Business Impact**
- Speed-to-lead goes from hours or days to **under 1 minute** from submit to Slack alert and auto-reply **(design target)**.
- **100% of submissions are accounted for**: valid, invalid and duplicate each land in an auditable sheet (by construction).
- Removes manual inbox triage and double entry across every lead source.

**v2 Hardening.** Replace Sheets with HubSpot/Pipedrive through **OAuth2 credentials**. Wrap the Slack and SMTP calls in an **Execute Workflow retry sub-workflow** with exponential backoff. Add an **OpenAI node for lead scoring and intent classification** before routing. Add a **dead-letter table** (Postgres/Supabase) that stores the raw payload of any failed execution so it can be replayed.

---

### P2 — Invoice Intake, Extraction & Approval Routing Engine
*`project-2-invoice-processing-pipeline` · 14 nodes · measured by P10*

**The Problem.** Accounts payable opens every PDF, types six fields, checks totals by hand, and routes approvals from memory: about 90 minutes for about 40 invoices, five days a week, with transposition errors and duplicate payments.

**n8n Architecture**
1. **Email Trigger (IMAP)** watches the AP inbox.
2. **IF: Has PDF?** Mail without an attachment is ignored.
3. **Extract From File** gets the raw PDF text.
4. **Code Node: Parse Fields** uses regex to extract invoice number, vendor, total and due date, and flags completeness.
5. **IF: Complete?** Low-confidence parses go to **Sheets: Needs Review** and a **Slack** ping to finance ops.
6. **IF: Amount > $1,000?** Large invoices go to **Sheets: Pending Approval** and a **Slack** ping to the manager. The rest go to **Sheets: Invoice Ledger** as auto-approved.
7. **Error Trigger → Slack**.

**Technical Highlights.** "Couldn't parse" is a routed outcome, not a crash, so the system never guesses. The approval policy is one parameter in one IF node. Regex extraction is transparent and auditable, and it can be swapped for an LLM node without changing anything downstream.

**Business Impact** (from the Project 10 model; accounting decisions documented there)
- **5.2× faster per invoice**: median run time **down 81%** (91 → 17 min) **(benchmark)**
- **Error rate down 59.6%** (1.56% → 0.63%) **(benchmark)**
- **7.4 hours saved per week, 340 hours per year**, **4.6-week payback** on a 34-hour build **(benchmark)**

**v2 Hardening.** Add an **OpenAI / LangChain structured-output extractor** with a **confidence gate** (below the threshold, the invoice goes to manual review). Add **vendor-template retrieval from a vector store** (Supabase pgvector). Add duplicate-invoice detection on vendor + invoice number. Archive the original PDF to Drive/S3, linked from the ledger row.

---

### P3 — Self-Healing Uptime Monitor with Tiered Escalation
*`project-3-uptime-monitor-auto-remediation` · 15 nodes*

**The Problem.** Most small-team incidents are "the container hung." Monitors page a human at 3am for something a restart would fix.

**n8n Architecture**
1. **Schedule Trigger** runs every 5 minutes.
2. **Code Node: Set Target Services** holds the service registry in one place.
3. **HTTP Request: Health Check** (`onError: continueRegularOutput`, so a timeout is treated as data).
4. **IF: Unhealthy?** Healthy services go to **Sheets: Heartbeat log**.
5. **Execute Command: `docker restart`**, then **Wait 30s**, then an **HTTP Request re-check**.
6. **IF: Still down?** If yes, **Slack on-call escalation** and **Sheets: Incident log**. If no, **Slack `#incidents` self-heal notice** and **Sheets: Self-heal log**.
7. **Error Trigger → Slack**.

**Technical Highlights.** Self-heal is always verified by a re-check before escalating. Heartbeats are logged so uptime % and MTTR can be computed. Self-heals and real incidents are logged as separate outcomes. There is a documented Docker-socket-proxy hardening path.

**Business Impact**
- Detection within **≤5 minutes**. Hung-container incidents are **resolved in about 30 seconds with no page** **(design target)**.
- On-call is paged only when automated remediation has **provably failed**.

**v2 Hardening.** Add a max-restarts-per-hour guard with backoff. Route restarts through a scoped Docker socket proxy via **HTTP Request**. Add a weekly **uptime % / MTTR digest** workflow.

---

### P4 — Serial FFmpeg Render Farm with Output Verification
*`project-4-media-render-farm` · 18 nodes*

**The Problem.** One ad concept needs 12–16 cuts (formats × hooks). Manual exports take an afternoon, and a render can "succeed" as a 4 KB, 0-second file that then goes live and spends money.

**n8n Architecture**
1. **Webhook** (`POST /render-request`), then **Code Node: Build Render Matrix** (formats × hooks become jobs).
2. **Respond to Webhook 202**: the caller is acknowledged immediately (async pattern).
3. **Split In Batches (size 1)** runs one render at a time.
4. **Execute Command: `render_variant.sh`** (FFmpeg), then **Execute Command: ffprobe**, then **Code Node: Validate Render**.
5. **IF: Render OK?** A bad render goes to **Sheets: Failed Renders** and a **Slack** alert, and the loop continues.
6. A good render gets a **Thumbnail**, then **HTTP Request: Upload to Asset Store (retry ×3)**, then **Sheets: Asset Manifest**.
7. The run ends with a **Slack summary**, and the **Error Trigger → Slack**.

**Technical Highlights.** An async 202 ack means the caller never times out. Throughput is CPU-bound, so it is queued deliberately. Four spec assertions run on every output (duration, bytes, width, height). Writes are atomic (`.partial` then `mv`). Bash exit codes are typed (2–5), so the workflow knows *how* a render failed.

**Business Impact**
- **1 request produces up to 16 verified variants**, replacing an afternoon of manual exporting **(design target)**.
- **0 unverified files can reach the asset manifest** (enforced by the Validate Render gate).

**v2 Hardening.** A Redis job queue with worker pool (n8n queue mode). An **Execute Workflow sub-workflow per render** for isolation and retries. A dead-letter queue for renders that fail three times.

---

### P5 — Spreadsheet-to-Creative Generative AI Factory (ComfyUI / SDXL)
*`project-5-generative-creative-factory` · 23 nodes*

**The Problem.** Marketers can't safely drive the ComfyUI node editor, and raw SDXL output isn't an ad: it's the wrong ratios, and the prompt strategy is embedded in the file's metadata.

**n8n Architecture**
1. **Schedule Trigger (06:00)**, then **Sheets: read queued briefs**, then **IF: Any queued?**
2. **Split In Batches** handles one brief at a time.
3. **Code Node: Build ComfyUI Graph** generates the API-format node JSON from the brief row.
4. **HTTP Request: `POST /prompt` (retry ×3)**, then **Wait 20s**, then **HTTP Request: `GET /history/{id}`**, then **Code Node: Check Completion**.
5. **IF: Done?** If not, **IF: Gave up? (≥ 5 min elapsed)**. If it gave up, **Sheets: mark `failed_timeout`** and a **Slack: GPU queue stuck** alert.
6. When done, **Execute Command: `postprocess_creative.py`** (download, strip metadata, crop ×4 placements), then **Code Node: Parse Output**, then **Sheets: mark rendered**.
7. **HTTP Request** hands off to the P4 Render Farm webhook, then **Slack digest**. **Error Trigger → Slack**.

**Technical Highlights.** Programmatic graph construction in a Code Node. An **async polling loop with a hard give-up** means no hung executions. The seed is written back so a result can be reproduced. Workflow-to-workflow chaining uses webhooks.

**Business Impact**
- Brief to **4 placement-ready, metadata-stripped creatives per generation** with no manual step **(design target)**.
- Worst-case stall is capped at **about 5 minutes** instead of an indefinite hang.

**v2 Hardening.** An **OpenAI node** expands a short brief into positive/negative prompts. A **vector store of past winning prompts** (fed by P6 results) supplies few-shot examples. A retry sub-workflow re-queues timed-out jobs once before failing.

---

### P6 — Statistically-Gated DCO & Budget Automation Engine
*`project-6-dco-engine` · 18 nodes · productizes the Ecomobi rule-based optimization*

**The Problem.** Ad platforms can't see affiliate revenue, and most "winning" creatives are noise. Scaling budget into noise burns money faster than a human can.

**n8n Architecture**
1. **Schedule Trigger (every 6h)** starts two parallel branches: **HTTP Request: Ad Platform Insights (paginated, retry ×4)** and **HTTP Request: Affiliate Conversions (retry ×4)**.
2. **Merge (append): Wait For Both Pulls**, then the Code Node joins `ad_id ↔ sub_id` across every API page.
3. **Code Node: Compute Variant Metrics** (CTR · CVR · CPA · ROAS · EPC), then **IF: Enough data?** (≥1,000 impressions and ≥100 clicks). If not, **Sheets: log still learning**.
4. **Execute Command: `ab_significance.py`** (two-proportion z-test with Bonferroni correction), then **Code Node: Parse Result**.
5. **Switch: Route Decision**:
   - **scale:** **HTTP Request: +20% budget (capped)**
   - **pause:** **HTTP Request: pause ad**, then **Sheets: queue a replacement brief** (which feeds P5)
   - **hold:** no action
6. **Sheets: Decision log**, then **Slack buying report**. **Error Trigger → Slack**.

**Technical Highlights.** Two refusal gates: a volume floor and a multiple-comparison-corrected significance test. Budget steps are capped. The creative loop closes itself (P6 → P5 → P4 → P6). Paginated, retried API pulls.

**Business Impact**
- Built on a real result: at Ecomobi, rule-based placement blacklisting and micro-bidding **cut wasted ad spend by 60%** on **$4K/month** at **180% average ROI**. This workflow turns that manual rule set into a statistically gated, unattended engine.
- **Zero budget changes on statistically insignificant lifts**: a +40% "winner" at p = 0.019 is held against an adjusted α of 0.0167 (reproducible from `fixtures/variants.json`).

**v2 Hardening.** **OAuth2 credentials** for the ad platform (Meta/Google Ads). Rate-limit-aware pagination via a **Split In Batches + Wait** loop driven by `Retry-After`. A Bayesian (Beta-Binomial) decision mode.

---

### P7 — Self-Healing VPS Ops & Verified Nightly ETL to PostgreSQL
*`project-7-vps-ops-data-pipeline` · 24 nodes*

**The Problem.** Self-hosted stacks fail quietly: disks fill up, backups "succeed" and turn out empty, and ETLs exit 0 having loaded nothing, which poisons every dashboard downstream.

**n8n Architecture**
1. **Schedule Trigger (02:00)**, then **SSH: `vps_healthcheck.sh`** (returns JSON), then **Code Node: Parse Health JSON** (throws on non-JSON).
2. **IF: Disk > 85%?** If yes, **SSH: prune Docker images older than 168h + vacuum journald**, then **SSH: re-check**, then **IF: Still full?** If still full, **Slack escalation**.
3. In parallel: **SSH: `backup_volumes.sh`** (tar + sha256), then **Code Node: Verify Backup** (must be over 1 MiB and pass its checksum), then **IF: Backup good?** If not, **Slack alert** and the ETL is skipped.
4. **SSH: `etl_load.py`** (idempotent upsert), then **Code Node: Parse ETL Result**, then **Postgres: SQL data-quality assertions**, then **IF: DQ passed?**
5. If it passed, **Sheets: nightly log** and a **Slack digest**. If it failed, **Postgres: quarantine the load** and a **Slack DQ alert**.
6. **Error Trigger → Slack**.

**Technical Highlights.** Every stage verifies its own output instead of trusting exit code 0. The script contract is JSON-over-SSH. Self-heal is followed by a re-check. The ETL is gated on a verified backup. A bad load is quarantined instead of committed.

**Business Impact**
- **0 unverified backups counted as success** and **0 bad loads committed to reporting tables** (enforced by the gates).
- Clears disk-pressure incidents unattended and pages only if usage stays above 85% after cleanup **(design target)**.

**v2 Hardening.** A monthly **restore-test workflow**. A **dead-letter table** for quarantined rows with a replay sub-workflow. Metrics emitted to P9.

---

### P8 — OAuth2 / HMAC / Pagination Integration Microservice (called from n8n)
*`project-8-integration-kit` · Python stdlib · 83 tests*

**The Problem.** n8n's built-in nodes hide the hardest parts of an integration. When a vendor has no node, or its node gets these edge cases wrong, you need a service that handles them correctly and that n8n can call through an **HTTP Request** node.

**Architecture.** Three connectors with three auth models, normalized into one Contact schema. **CRM: OAuth2** with single-flight refresh and rotating refresh tokens. **Billing: HMAC-signed webhooks** with a ±300 s replay window, secret rotation and claim-once idempotency. **Helpdesk: API key + cursor pagination** with loop detection and `max_pages`. The transport layer honours `Retry-After` (seconds or HTTP-date), uses capped exponential backoff with full jitter, and never retries a POST without an idempotency key.

**Technical Highlights.** An OAuth refresh race is proven with **8 concurrent threads, asserting exactly 1 token call**. Idempotency is proven with **20 threads, exactly 1 winner**. Merges follow a source-trust ranking.

**Business Impact.** **83 tests.** It removes the most common causes of duplicate charges, revoked grants and runaway pagination loops. Runs as a small HTTP service that n8n calls through the HTTP Request node.

**v2.** Publish an n8n **HTTP Request node template** and an Execute Workflow wrapper that calls it.

---

### P9 — Observability Layer for an n8n Workflow Estate
*`project-9-observability-layer` · 105 tests*

**The Problem.** "I have error handling" can't be checked from outside. Worse, a scheduled workflow that stops running produces **zero failures**, so a monitor based on error rate stays green.

**Architecture.** Each workflow adds **three nodes** (start, success, failure event) that send HTTP events to an exporter. The exporter serves **Prometheus `/metrics`**. There are **7 alert rules**, including a **staleness alert on the age of the last success**, which is the one alert that catches a workflow that has stopped running. Also included: a provisioned Grafana dashboard, **correlation IDs** that survive webhook hops (P5 → P4 → P6), and an on-call runbook with an escalation path.

**Business Impact.** **105 tests.** It covers the "succeeded but processed 0 items" and "never ran" blind spots. Exporter verified live. The Docker stack (Prometheus + Grafana) has not been started yet.

**v2.** Wire P1–P7 in for real and publish a Grafana screenshot with live data.

---

### P10 — Automation ROI Case Study: Manual vs. n8n Invoice Processing
*`project-10-migration-case-study` · 35 tests*

**The Problem.** "Saved 12 hours a week" reads the same whether it's measured or made up. Career changers are specifically asked to prove impact.

**Architecture.** It times the manual process step by step and measures the automated one. A script regenerates every figure from the raw CSVs. Tests fail the build if the README's figures drift from the data, if failed automated runs are dropped, or if the annual figure uses 52 weeks instead of 46.

**Business Impact.** **5.2× faster, 7.4 h/week, 4.6-week payback (benchmark)**. The deliverable is the *method*: ready for real measurements, with every accounting choice that makes the number smaller written down.

---

## 4. Tailored n8n CV Copy

> **IDA BAGUS WICITRA DYAKSA**
> n8n & AI Workflow Automation Engineer
> Denpasar, Bali, Indonesia (Remote, open to APAC/AU/EU) · wicitradyaksa@gmail.com · [phone] · github.com/wicitradyaksa · linkedin.com/in/ida-bagus-wicitra-dyaksa-063458129

### Professional Summary
Workflow Automation Engineer specializing in production-grade n8n: event-driven webhook and scheduled pipelines, REST/OAuth2 API integrations, JavaScript Code Nodes, and Python/Bash services orchestrated through HTTP Request, SSH and Execute Command nodes. I design the failure path first. Every workflow validates input, verifies output, alerts through an Error Trigger, and self-heals before escalating. The practice comes from 2+ years running high-volume operations at 24.5 t/day and from building rule-based paid-media automation that cut wasted ad spend by 60%.

### Core Skills
- **n8n:** self-hosted (Docker Compose), Webhooks (sync/async), Schedule/IMAP triggers, IF/Switch/Merge, Split In Batches loops, Wait-based polling, Error Trigger alerting, cross-workflow chaining
- **APIs:** REST, OAuth2 refresh, HMAC webhook verification, cursor pagination, rate limiting (`Retry-After`, backoff + jitter), idempotency
- **Code:** JavaScript (Code Nodes, JSON/regex normalization), Python, Bash, SQL
- **Data & AI:** PostgreSQL, Google Sheets API, ComfyUI/SDXL API. *(In progress: OpenAI/LangChain nodes, pgvector)*
- **Ops:** Docker, Linux VPS, SSH, Prometheus/Grafana, Git
- **Business:** process mapping, bottleneck analysis, ROI/payback modelling, A/B testing, CPA/ROAS

### Automation Projects — github.com/wicitradyaksa/automation-portfolio
*7 importable n8n workflows (126 nodes) · 3 companion Python projects · 223 automated tests · workflow linter*

- **Invoice Intake & Approval Engine (n8n):** Engineered an IMAP → Extract From File → JS Code Node regex parser → rule-based routing pipeline that sends low-confidence parses to human review instead of guessing. The ROI model shows 5.2× faster processing per invoice and a 4.6-week payback (benchmark method, 35 tests).
- **Lead Capture & CRM Sync (n8n):** Architected a webhook pipeline that normalizes multi-source payloads in a Code Node, de-duplicates on email with an idempotent upsert, and triggers Slack and auto-reply email in a single execution, returning synchronous 200/400 responses.
- **Self-Healing Uptime Monitor (n8n + Docker):** Built 5-minute health polling with automated `docker restart`, a 30-second verification re-check, and tiered Slack escalation, logging heartbeats and incidents separately for uptime and MTTR reporting.
- **DCO & Budget Automation Engine (n8n + Python):** Merged paginated, retried ad-platform and affiliate API pulls. Computed CTR/CVR/CPA/ROAS/EPC in a Code Node and gated every budget change behind a volume floor and a Bonferroni-corrected z-test, with capped +20% scaling and auto-queued replacement creative.
- **Verified Nightly ETL & VPS Ops (n8n + SSH + Postgres):** Orchestrated JSON-over-SSH health checks, self-healing disk cleanup, checksum-verified backups, and an idempotent Python ETL into PostgreSQL. Bad loads are quarantined through SQL data-quality assertions.
- **Generative Creative Factory & Render Farm (n8n + ComfyUI + FFmpeg):** Generated ComfyUI API graphs in a JS Code Node, polled async jobs with a hard 15-attempt give-up, then chained to a serial FFmpeg render farm that verifies every output with ffprobe before release.
- **Integration Kit + Observability (Python):** Built an HTTP-callable integration service (OAuth2 single-flight refresh proven under 8 concurrent threads, HMAC webhooks, cursor pagination) and a Prometheus/Grafana observability layer with 7 alert rules and a staleness alert for the n8n estate.
- **Workflow quality tooling:** Wrote a static linter for n8n workflow exports that catches dangling connections, broken `$('Node')` references and `$json` reads after item-replacing nodes. It found and drove fixes for data-flow bugs across all 7 workflows.

### Experience

**Digital Marketing Contractor, Ecomobi PTE** · Denpasar · Jun 2018 – Sep 2020
- Managed **$4K/month** in ad spend across ad networks at an average **180% ROI**, routing **100K+ daily visitors** through custom landing pages.
- **Cut wasted ad spend by 60%** by implementing automated, rule-based blacklisting of non-converting placement zones and micro-bidding adjustments. This is the rule set later productized in the DCO Engine.

**Operations Staff, BAIADA** · Tamworth, NSW, Australia · Jan 2023 – Feb 2025
- Kept a high-volume production and logistics operation of **24.5+ tons/day** on schedule under strict process and quality tolerances.
- Identified recurring workflow bottlenecks and adjusted process steps to hold throughput on target. This is the same map-the-process, find-the-constraint method I now apply to automation design.
- Coordinated cross-functional handoffs between production and logistics and resolved exceptions in real time.

**Manufacturing Operator, Primo Foods** · Strathfield, NSW, Australia · Jun 2022 – Jan 2023
- Ran repeatable, HACCP-compliant high-volume production processes with batch quality inspection under time pressure.

**Business Analyst, PT Baruna Dirga Dharma** · Jakarta · Jan 2018 – Jun 2018
**Officer Development Program, PT Cipta Krida Bahari** · Jakarta · Jan 2017 – Jan 2018

### Education
**B.Eng, Maritime Transportation Engineering**, Institut Teknologi Sepuluh Nopember (ITS), Surabaya. GPA 3.47/4.00. Focus: maritime logistics, system dynamics & simulation (Powersim).

### Languages
English (professional working) · Indonesian (native)

---

## 5. Portfolio Website Structure & Copy

### Hero
**Eyebrow:** n8n · APIs · AI · Docker
**H1:** Automation that tells you when it breaks.
**Sub:** I'm Dyaksa, an n8n & AI Workflow Automation Engineer. I build event-driven workflows that connect your APIs, databases and AI models, verify their own output, and self-heal before they page anyone.
**Proof strip:** `7 n8n workflows` · `126 nodes` · `223 tests` · `workflow linter` · `Error Trigger on every workflow`
**CTAs:** [View workflows] [Download CV] [Book a call]

### Featured n8n Projects (cards)

| Card | Title | One-liner | Chips |
|---|---|---|---|
| 1 | **Invoice Approval Engine** | IMAP to parsed, routed and approved invoices, and it asks when it isn't sure. | `5.2× faster (benchmark)` · IMAP · Code Node · Slack |
| 2 | **Lead Capture & CRM Sync** | Any form to a de-duplicated CRM, a sales alert and an auto-reply in one execution. | Webhook · Idempotent upsert · 200/400 |
| 3 | **Self-Healing Uptime Monitor** | Restarts it, checks it, and only then wakes you up. | Schedule · Docker · Tiered escalation |
| 4 | **DCO Budget Engine** | Scales winners, pauses losers, and refuses to act on noise. | Merge · Switch · z-test · Paginated APIs |
| 5 | **Verified Nightly ETL** | Backups that are verified before they're trusted, and loads that pass SQL checks before they're committed. | SSH · Postgres · DQ quarantine |

Each card links to the project README and `workflow.json` ("Import into n8n").

**Engineering depth strip (P8, P9, P10):** *"The code behind the nodes."* An OAuth2/HMAC/pagination integration service · Prometheus observability for n8n · an ROI case study where every number is regenerated by a script.

### How I Build (3 rules)
1. **Exit code 0 isn't proof.** Every stage verifies its own output: ffprobe the render, checksum the backup, assert row counts after the load.
2. **Every workflow has a failure path.** An Error Trigger, a Slack alert, and a logged payload. Silent failure is worse than no automation.
3. **Refusing to act is a feature.** When the evidence is thin, the workflow holds and asks for a human.

### Tech Stack
Grouped icons/chips mirroring Section 2: **Automation** (n8n, Webhooks, Docker) · **APIs** (REST, OAuth2, HMAC, Pagination) · **Code** (JavaScript, Python, Bash, SQL) · **Data & AI** (PostgreSQL, Google Sheets, ComfyUI, OpenAI*) · **Ops** (Prometheus, Grafana, Docker, Linux). *\*marked "in progress" until the v2 LLM nodes ship.*

### Background (short)
Before automation, I ran a 24.5 t/day production and logistics operation in Australia and optimized paid-media programs where rule-based automation cut wasted spend by 60%. Same discipline, different materials: find the bottleneck, remove it, and make sure you'll know if it comes back.

### Contact
**H2:** Have a manual process that keeps breaking?
**Copy:** Tell me the process. I'll send back a workflow sketch: triggers, nodes, failure paths, and the hours it should return.
**Links:** wicitradyaksa@gmail.com · LinkedIn · GitHub · [Download CV]

---

## Next (Phase 2)
- Rewrite each `project-*/README.md` onto `Readme_Template.md`, using the metrics tags above.
- Build the **v2** items with the highest interview value first: the P2 OpenAI extractor + confidence gate, the Execute Workflow retry sub-workflow + dead-letter table (reusable across P1–P7), and the P1 OAuth2 CRM node.
- Replace the **(benchmark)** figures with real measurements once Project 10 has real data.
