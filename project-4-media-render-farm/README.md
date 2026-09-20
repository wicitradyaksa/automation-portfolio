# Media Render Farm (FFmpeg)

**One master video in, every ad format out — with every output verified before it's called finished.**

## The problem

A performance campaign doesn't run one video. It runs the same video cut four ways for four placements, each with three or four different opening hooks, refreshed every couple of weeks as creative fatigues. That's 12–16 files per concept, and the usual process is someone in a video editor exporting them one at a time for an afternoon.

The manual version has two failure modes that cost more than the time. The first is inconsistency: one export gets the wrong loudness, another ends up with non-square pixels and gets rejected on upload, a third is missing the faststart flag so previews stall. The second is worse — a render that *looks* like it worked. FFmpeg exits 0, the file exists, and nobody notices it's 4 KB and zero seconds until the ad set has been live for six hours spending money on a broken creative.

## The solution

A webhook takes a render request describing one master file and the variant matrix wanted. The workflow expands that into individual render jobs, runs them through FFmpeg **one at a time**, and then `ffprobe`s every output before accepting it.

```
POST /render-request
  { assetId, sourcePath, campaign, formats: ["9:16","1:1","16:9"], hooks: ["A","B"] }
      │
      ▼
Build Render Matrix ──────────► Ack Request (202, immediately)
  3 formats × 2 hooks = 6 jobs
      │
      ▼
Render One At A Time ◄──────────────────────────────┐
      │ (loop)                                      │
      ▼                                             │
FFmpeg Transcode  →  Probe Output  →  Validate Render
                                            │
                                     Render OK?
                                      │        │
                                    yes        no
                                      │        └─► Log Failed Render → Alert Creative Ops ─┘
                                      ▼
                        Generate Thumbnail
                                      ▼
                        Upload To Asset Store  (retry ×3)
                                      ▼
                        Log To Asset Manifest ──────────────────────────┘
```

When the loop drains, it posts a summary and the manifest is ready for the [DCO engine](../project-6-dco-engine) to start testing against.

## What `Validate Render` actually checks

This node is the reason the project exists. It compares the `ffprobe` output against the spec that was requested:

| Check | Catches |
|---|---|
| `duration < 1s` | Truncated source, bad seek, corrupt input |
| `bytes < 10 KB` | Encoder wrote a header and nothing else |
| `width != requested` | Filter chain silently fell back |
| `height != requested` | Pad/scale math wrong for an unusual source ratio |

Anything that fails goes to a `Failed Renders` sheet with the specific problem string and pings `#creative-ops` — it does **not** go into the asset manifest, so a broken file can never reach a live ad set.

## The FFmpeg script

The encoding lives in [`scripts/render_variant.sh`](./scripts/render_variant.sh), not in the node, so it's reviewable and testable on its own:

```bash
./scripts/render_variant.sh --src master.mp4 --out story.mp4 \
    --w 1080 --h 1920 --crf 23 --maxrate 4M --hook "Try it free"
```

Decisions worth calling out:

- **`scale=…:force_original_aspect_ratio=decrease` then `pad=`** — fit inside the box and letterbox, never stretch. A squashed product shot gets rejected on ad review.
- **`setsar=1`** — square pixels. Non-1 SAR is one of the most common silent upload rejections.
- **`loudnorm=I=-14:TP=-1.5`** — every variant lands at the same perceived loudness, so a hook test isn't accidentally a volume test.
- **`-movflags +faststart`** — moov atom first, so the file plays before it's fully downloaded.
- **Render to a `.partial` temp file, then `mv` into place** — a crash mid-encode leaves nothing rather than a half-written file that looks finished to anything watching the folder.
- **`set -Eeuo pipefail`** and explicit exit codes (2 bad args, 3 missing source, 4 encoder failed, 5 output too small) so the workflow can tell *how* it failed.

One bug this caught during development: the temp file originally had no extension, and FFmpeg picks its muxer from the output filename — every render failed with `Unable to choose an output format`. The temp name now keeps the real extension.

## Tech stack

- **n8n** — Webhook, Code, Split In Batches, Execute Command, IF, HTTP Request, Google Sheets, Slack, Error Trigger
- **FFmpeg / ffprobe** — transcode, filter graph, loudness normalisation, thumbnail extraction, verification
- **Bash** — the render script, with strict mode and real exit codes
- **Docker** — FFmpeg runs in the container alongside n8n; `/data` is a shared volume

## Why it's built this way

**Serial, not parallel.** `Split In Batches` with `batchSize: 1` means one FFmpeg process at a time. Six concurrent x264 encodes on a 2-core VPS don't finish six times faster — they thrash, and the box stops responding to everything else including the n8n UI. Throughput here is bounded by CPU, so queuing is the correct answer, not a limitation.

**The webhook acks before rendering.** A 16-variant batch takes minutes. Responding `202` with the queued count immediately means the calling system isn't holding a connection open, and the render farm can't be knocked over by a client timeout.

**Failures continue the loop.** A failed variant logs, alerts, and returns to the batch node. One bad hook doesn't abandon the other eleven.

## What I'd improve with more time

- Swap the Execute Command nodes for a real job queue (Redis + a worker pool) so renders survive an n8n restart and can be retried independently.
- Hardware encoding (`h264_nvenc`) where a GPU is available — roughly 5–10× faster for the same visual quality at this bitrate.
- Perceptual diffing against the master so a variant that renders *correctly* but looks wrong (bad crop on an off-centre subject) gets flagged too.
- Auto-generated captions via Whisper, burned in per-variant — silent autoplay makes this close to mandatory now.

## Running it

Import [`workflow.json`](./workflow.json) into n8n, mount `scripts/` at `/data/scripts` and a render output directory at `/data/renders`, then:

```bash
curl -X POST http://localhost:5678/webhook/render-request \
  -H 'Content-Type: application/json' \
  -d '{
    "assetId": "summer_hero_01",
    "campaign": "summer_sale",
    "sourcePath": "/data/masters/summer_hero.mp4",
    "formats": ["9:16", "1:1", "16:9"],
    "hooks": ["Try it free", "50% off today"]
  }'
```

The Slack and Google Sheets credentials are placeholders (`… (demo)`) — swap them for your own, or read the JSON to follow the logic without connecting real accounts.
