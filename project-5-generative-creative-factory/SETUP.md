# Setup Guide: Generative Creative Factory (ComfyUI)

Standalone installation for this one workflow. It works on its own; the hand-off to the Media Render Farm (project 4) is **optional** (see §9).

**What it does:** every morning at 06:00 it reads the `queued` briefs from a Google Sheet. For each one it builds a Stable Diffusion XL graph, queues it on ComfyUI, and polls until the images are ready, giving up after 5 minutes. It then post-processes the images into ad placements, marks the brief `rendered`, and posts a digest to Slack.

---

## 1. What you need

| Item | Why |
|---|---|
| A self-hosted n8n (Docker) with Python 3 + Pillow | *Execute Command* runs `postprocess_creative.py`. The image below adds them |
| A ComfyUI server (GPU) reachable from n8n | Generates the images |
| An SDXL checkpoint in ComfyUI | Default `sd_xl_base_1.0.safetensors` in `ComfyUI/models/checkpoints/` |
| A Google account | The `Creative Briefs` tab is the job queue |
| A Slack workspace where you can install an app | Digests and "GPU queue stuck" alerts |

---

## 2. Run n8n

Folder layout:

```
creative-factory/
├─ workflow.json
├─ .env
├─ n8n.Dockerfile
├─ docker-compose.yml
└─ data/
   ├─ creatives/                  ← finished placements land here
   └─ scripts/
      └─ postprocess_creative.py  ← copy from ./scripts/ in this project
```

**`.env`**
```ini
GENERIC_TIMEZONE=UTC               # the 06:00 schedule runs in this timezone
CREATIVE_SHEET_ID=
# ComfyUI as seen from inside the n8n container:
#   same compose network  -> http://comfyui:8188
#   running on the host   -> http://host.docker.internal:8188  (start ComfyUI with --listen)
COMFYUI_URL=http://host.docker.internal:8188
# Only needed for the optional hand-off to the Render Farm (§9)
N8N_BASE_URL=http://localhost:5678
```

**`n8n.Dockerfile`**
```dockerfile
FROM alpine:3.24 AS tools
RUN apk add --no-cache python3 py3-pillow && mkdir /out && cp /usr/bin/python3* /out/

FROM docker.n8n.io/n8nio/n8n:latest
USER root
COPY --from=tools /usr/lib/ /usr/lib/
COPY --from=tools /lib/ /lib/
COPY --from=tools /out/ /usr/bin/
USER node
```

**`docker-compose.yml`**
```yaml
services:
  n8n:
    build: { context: ., dockerfile: n8n.Dockerfile }
    restart: unless-stopped
    ports: ["5678:5678"]
    env_file: .env
    environment:
      - N8N_BLOCK_ENV_ACCESS_IN_NODE=false   # lets {{ $env.* }} resolve
      - NODES_EXCLUDE=[]                      # re-enables Execute Command
    extra_hosts:
      - "host.docker.internal:host-gateway"   # needed on Linux; built in on Docker Desktop
    volumes:
      - n8n_data:/home/node/.n8n
      - ./data:/data
volumes:
  n8n_data:
```

```bash
docker compose up -d --build
```

Then check that n8n can reach ComfyUI:

```bash
docker compose exec n8n sh -c 'wget -qO- $COMFYUI_URL/system_stats'
```

> Already running the whole portfolio? The repo-root compose already has Python + Pillow and mounts the script. Fill in the variables in the root `.env`.

---

## 3. Credentials and scopes

**Two** n8n credentials. ComfyUI has no authentication.

| Credential | n8n type | Nodes |
|---|---|---|
| Google Sheets | Google Sheets OAuth2 API | Read Creative Briefs, Mark Brief Rendered, Mark Brief Failed |
| Slack | Slack API | Post Creative Digest, Alert GPU Queue Stuck, Alert Engineering (Slack) |

### 3.1 Google Sheets OAuth2 API

**Scopes** (n8n requests these automatically): `https://www.googleapis.com/auth/spreadsheets` (read the queue, update the brief status) and `https://www.googleapis.com/auth/drive.file`.

1. In <https://console.cloud.google.com>, create a project. Under **APIs & Services → Library**, enable **Google Sheets API** and **Google Drive API**.
2. **Google Auth Platform → Branding / Audience**: choose **Internal** or **External** and add yourself as a Test user. ⚠️ Tokens expire after **7 days** while the app is in Testing, and a daily schedule will quietly stop. **Publish app.**
3. **Credentials → OAuth client ID → Web application**. Redirect URI: `http://localhost:5678/rest/oauth2-credential/callback`.
4. In n8n: **Credentials → Google Sheets OAuth2 API**, paste the ID and secret, then **Sign in with Google**.

### 3.2 Slack API (bot token)

Bot scopes: `chat:write` (**required**), `chat:write.public` (recommended), `channels:read` / `groups:read` (so the channel picker works).

1. Go to <https://api.slack.com/apps> → **Create New App → From scratch**.
2. Add the scopes, then **Install to Workspace**.
3. Copy the `xoxb-…` token.
4. In n8n: **Credentials → Slack API**.

### 3.3 ComfyUI security

ComfyUI has no login. Don't expose port 8188 to the internet. Keep it on a private network, or put it behind an auth proxy. In that case, add the auth header to **Queue On ComfyUI** and **Poll Generation History**, and to the image download in `postprocess_creative.py`.

---

## 4. Prepare Google Sheets and Slack

**Spreadsheet** (its ID goes in `CREATIVE_SHEET_ID`), tab **`Creative Briefs`**, with this header row:

```
briefId, campaign, subject, style, lighting, negative, checkpoint, width, height, batchSize, seed, steps, cfg, status, videoSourcePath, hooks, assetCount, outputDir, renderedAt, note
```

| Column | Required | Meaning / default |
|---|---|---|
| `briefId` | yes | Unique. The match key when the status is updated |
| `campaign` | yes | Used in output folders |
| `subject` | yes | The main prompt text |
| `status` | yes | Set to `queued`. The workflow writes `rendered` or `failed_timeout` |
| `style`, `lighting` | no | Appended to the prompt |
| `negative` | no | Default: `text, watermark, logo, lowres, deformed hands, extra limbs` |
| `checkpoint` | no | Default `sd_xl_base_1.0.safetensors` |
| `width` / `height` / `batchSize` / `steps` / `cfg` / `seed` | no | 1024 / 1024 / 4 / 28 / 6.5 / random |
| `videoSourcePath`, `hooks` | no | Only for the Render Farm hand-off (§9). Leave empty otherwise |
| `assetCount`, `outputDir`, `renderedAt`, `note` | written by the workflow | |

**Slack channels:** `#creative-ops` and `#eng-alerts`.

---

## 5. Import and configure

1. **Import:** open **Workflows → ⋯ → Import from File** and pick `workflow.json`.
2. **Connect credentials** on each node marked ⚠️.
3. **Check failure alerts:** the workflow already names itself as its Error Workflow, so the *Workflow Error → Alert Engineering* branch is live. If you imported it through the editor rather than `n8n import:workflow` or `scripts/n8n_sync.py`, n8n may have given it a new ID, so open **⋯ → Settings → Error Workflow** and make sure *this workflow* is selected.
4. **Activate** it (**Publish** in n8n 2.x).

---

## 6. Node reference

| Node | Type | What it does |
|---|---|---|
| Every Morning 06:00 | Schedule (cron `0 6 * * *`) | In `GENERIC_TIMEZONE` |
| Read Creative Briefs | Sheets: read rows where `status = queued` | *Always Output Data* on |
| Any Briefs Queued? → Nothing To Render | IF → No-Op | |
| One Brief At A Time | Split In Batches (size 1) | |
| Build ComfyUI Graph | Code (JS) | Builds the SDXL API-format graph (checkpoint → prompts → latent → KSampler → VAE decode → SaveImage) |
| Queue On ComfyUI | HTTP POST `{COMFYUI_URL}/prompt`, retry 3× / 5 s | Returns `prompt_id` |
| Wait 20s → Poll Generation History | Wait → HTTP GET `/history/<prompt_id>` | |
| Check Completion | Code (JS) | `done` once images exist; `timedOut` after **5 minutes** |
| Generation Done? / Gave Up Waiting? | IF / IF | Not done and not timed out → loops back to *Wait 20s* |
| Post-Process Creatives | Execute Command → `postprocess_creative.py` | Downloads from ComfyUI `/view`, strips metadata, exports placements to `/data/creatives` |
| Parse Creative Output | Code (JS) | Fails loudly if the script didn't return JSON |
| Mark Brief Rendered | Sheets: update matched on `briefId` | `status=rendered`, plus asset count, folder, seed |
| Has Master Video? | IF | Empty `videoSourcePath` → skip the hand-off |
| Hand Off To Render Farm | HTTP POST `{N8N_BASE_URL}/webhook/render-request`, On Error → Continue | Optional (§9) |
| Post Creative Digest | Slack `#creative-ops` → next brief | |
| Mark Brief Failed → Alert GPU Queue Stuck | Sheets `status=failed_timeout` → Slack → next brief | |
| Next Brief | No-Op | The loop is finished |
| Workflow Error → Alert Engineering | Error Trigger → Slack `#eng-alerts` | |

---

## 7. Test

1. Add a row: `briefId=test01`, `campaign=summer`, `subject=a glass bottle of cold brew on a marble counter`, `status=queued`.
2. Click **Execute Workflow**.
3. Expected: images in `./data/creatives/`, the row flips to `rendered` with `assetCount` filled in, and a digest appears in `#creative-ops`.
4. Timeout path: stop ComfyUI (or point `COMFYUI_URL` at a dead port) and queue another brief. After about 5 minutes the brief shows `failed_timeout` and `#creative-ops` gets "The GPU queue may be wedged".

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Every brief times out | n8n can't reach ComfyUI (run the check at the end of §2), or the checkpoint name doesn't exist. Look for errors in ComfyUI's console |
| `Queue On ComfyUI` returns 400 | A node class or checkpoint is missing in ComfyUI. The response body names it |
| `python3: not found` | You're on the stock image. Build `n8n.Dockerfile` |
| `Pillow not installed` in the output | The `py3-pillow` layer is missing. Rebuild |
| Nothing happens at 06:00 | The workflow isn't active, or `GENERIC_TIMEZONE` isn't what you expected |
| Brief status never updates | The `briefId` header is missing, or the value isn't unique |

---

## 9. Optional: hand-off to the Media Render Farm

If the client also runs **project 4 (Media Render Farm)** on the same n8n, briefs can also produce video variants:

1. Install and **activate** project 4 (see its `SETUP.md`). This n8n needs FFmpeg too, so merge both Dockerfiles.
2. Put the master video under `/data/masters/…` and write that path into the brief's `videoSourcePath`. Write `hooks` pipe-separated, e.g. `Try it free|50% off today`.
3. Keep `N8N_BASE_URL=http://localhost:5678`. Both workflows run in the same container.

Without project 4, leave `videoSourcePath` empty. *Has Master Video?* then skips the hand-off. Even if a path is set by mistake, the hand-off node's *On Error → Continue* lets the digest still post.
