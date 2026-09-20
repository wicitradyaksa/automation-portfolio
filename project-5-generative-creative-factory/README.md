# Generative Creative Factory (ComfyUI / SDXL)

**Marketing fills in a spreadsheet row. A GPU box turns it into a full set of placement-ready ad creatives. Nobody opens the ComfyUI node editor.**

## The problem

ComfyUI is a superb generation tool and a terrible production interface. The node graph is where the power is, and it's also where a non-technical marketer will reliably break something — change a sampler, drop a connection, forget the seed, or quietly generate 400 images at the wrong resolution.

The other half of the problem is what happens *after* generation. A raw SDXL output is a 1024×1024 PNG with the entire prompt graph embedded in its metadata. That is not an ad. It needs cropping to four placement ratios, the metadata stripped (you really do not want your prompt strategy shipping to an ad network), and converting to a format the upload flow accepts.

## The solution

A scheduled workflow reads queued briefs from a Google Sheet, **builds the ComfyUI API-format node graph programmatically** from the brief fields, queues it, polls until the images land, and post-processes everything in Python.

```
06:00 daily
   │
   ▼
Read Creative Briefs  (status = "queued")
   │
Any Briefs Queued? ──no──► Nothing To Render
   │ yes
   ▼
One Brief At A Time ◄─────────────────────────────────┐
   │ (loop)                                           │
   ▼                                                  │
Build ComfyUI Graph      ← JS builds the API-format JSON
   ▼
Queue On ComfyUI  POST /prompt  (retry ×3)
   ▼
Wait 20s ◄──────────────────┐
   ▼                        │
Poll /history/{prompt_id}   │
   ▼                        │
Check Completion            │
   │                        │
Generation Done? ──no──► Gave Up Waiting? ──no──┘  (max 15 polls ≈ 5 min)
   │ yes                        │ yes
   │                            ▼
   │                    Mark Brief Failed → Alert GPU Queue Stuck ─┐
   ▼                                                              │
Post-Process Creatives  (Python: download, strip, crop ×4)        │
   ▼                                                              │
Parse Creative Output                                             │
   ▼                                                              │
Mark Brief Rendered → Hand Off To Render Farm → Post Digest ──────┘
```

## Building the node graph in code

The `Build ComfyUI Graph` node constructs ComfyUI's API-format JSON from the brief row — checkpoint loader, two CLIP text encoders (positive and negative), empty latent, KSampler, VAE decode, save image — wiring the node references (`['1', 0]`) by hand:

```js
'5': { class_type: 'KSampler',
       inputs: { seed, steps, cfg, sampler_name: 'dpmpp_2m', scheduler: 'karras',
                 denoise: 1, model: ['1', 0], positive: ['2', 0],
                 negative: ['3', 0], latent_image: ['4', 0] } },
```

The seed is captured and written back to the sheet on success. That single field is the difference between "we got a great image once" and "we can regenerate that exact look at a different aspect ratio next month."

## The polling loop, and why it gives up

ComfyUI's `/prompt` endpoint is fire-and-forget — it returns a `prompt_id` immediately and the job may take anything from 20 seconds to several minutes depending on batch size and what else is on the GPU. `/history/{prompt_id}` returns `{}` until the job completes.

The naive version of this loop polls forever. When the GPU queue wedges — which it does, usually on an OOM from too large a batch — that workflow execution hangs indefinitely, holds a worker slot, and nobody finds out until someone asks why yesterday's creatives never arrived.

So the loop counts its own attempts and hard-stops at 15 polls (~5 minutes), marks the brief `failed_timeout` with the poll count, and alerts. **A workflow that gives up loudly is worth more than one that waits politely forever.**

## Post-processing in Python

[`scripts/postprocess_creative.py`](./scripts/postprocess_creative.py) handles everything between "there are PNGs on the GPU box" and "there are ad creatives on disk":

- Downloads each generation from `/view`, with a size floor — a sub-1 KB response means ComfyUI returned an error page, not an image, and that gets raised rather than saved.
- Exports four placements (1:1 feed, 4:5 portrait, 9:16 story, 1.91:1 link) using `ImageOps.fit` with centering `(0.5, 0.45)` — crop to fill, biased slightly above centre, because SDXL compositions are centre-weighted and letterboxing gets scored as low-quality creative.
- Re-saves as progressive JPEG, which **drops the PNG text chunks** where ComfyUI stores the full prompt graph.
- Per-image error isolation: one failed download doesn't lose the other eleven, and the failures come back in the JSON response rather than vanishing.

Everything it prints is JSON, including its errors — the downstream Code node parses stdout, and a Python traceback there produces a far worse error message than a structured `{"ok": false, "error": "..."}`.

## Tech stack

- **n8n** — Schedule Trigger, Google Sheets, Split In Batches, Code, HTTP Request, Wait, IF, Execute Command, Slack, Error Trigger
- **ComfyUI / SDXL** — API-format graph submission, async job polling, `/view` retrieval
- **Python 3 + Pillow** — download, placement export, metadata stripping
- **Google Sheets** — the brief queue and the status ledger, because it's the interface marketing already has

## Why it's built this way

**A spreadsheet is the UI.** Not a custom app, not the ComfyUI editor. It's already open on their second monitor, it supports comments, and it gives you the status ledger for free.

**One brief at a time.** GPU inference is serial anyway; batching briefs concurrently just means several of them fail together when VRAM runs out.

**It hands off rather than doing everything.** Briefs with a master video get POSTed to the [render farm's](../project-4-media-render-farm) webhook. Two workflows with a clean contract between them beat one workflow that does both jobs badly — and it means the render farm can be tested, restarted, or replaced on its own.

## What I'd improve with more time

- Replace polling with ComfyUI's WebSocket progress endpoint — same result, no 20-second granularity, and real progress reporting.
- An automated brand-safety gate before anything reaches the manifest: a CLIP classifier pass for unintended text, watermarks, or malformed hands, which SDXL still produces often enough to matter.
- Prompt versioning, so a performance change can be traced back to the prompt revision that caused it rather than guessed at.
- ControlNet conditioning off the product photo, so generated scenes keep the actual product geometry instead of a plausible-looking approximation of it.

## Running it

Import [`workflow.json`](./workflow.json), point `COMFYUI_URL` at your instance, and create a `Creative Briefs` sheet with these columns:

| briefId | campaign | subject | style | lighting | negative | checkpoint | width | height | batchSize | seed | steps | cfg | status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `b_001` | `summer_sale` | `iced coffee can on marble` | `editorial product photography` | `soft morning window light` | | | `1024` | `1024` | `4` | | `28` | `6.5` | `queued` |

Set `status` to `queued` and the next run picks it up. The script can also be driven directly:

```bash
python3 scripts/postprocess_creative.py \
  --brief b_001 --campaign summer_sale --out-dir ./out \
  --images '[{"filename":"x.png","url":"http://comfyui:8188/view?filename=x.png&type=output"}]'
```
