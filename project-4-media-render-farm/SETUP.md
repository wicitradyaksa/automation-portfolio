# Setup Guide: Media Render Farm (FFmpeg)

Standalone installation for this one workflow. It doesn't need any other workflow in this repo. (The Generative Creative Factory *can* call it, but that's optional; see §9.)

**What it does:** you `POST` one master video plus the formats and hook lines you want. The workflow replies `202` immediately, then renders every format × hook variant one at a time with FFmpeg. It checks each output with `ffprobe` (duration, size, dimensions) and makes a thumbnail. Passing renders are uploaded to your asset store and logged to a manifest; failed renders are logged and reported in Slack.

---

## 1. What you need

| Item | Why |
|---|---|
| A self-hosted n8n (Docker) with FFmpeg, bash and a font | *Execute Command* runs FFmpeg. The image below adds them |
| An asset store that accepts `POST /upload` (multipart) with a Bearer token | Where the finished renders go. For S3, GCS or Cloudinary, see §8 |
| A Google account | `Asset Manifest` and `Failed Renders` tabs |
| A Slack workspace where you can install an app | Render failures and batch summaries |

---

## 2. Run n8n

Create this folder layout:

```
render-farm/
├─ workflow.json
├─ .env
├─ n8n.Dockerfile
├─ docker-compose.yml
└─ data/
   ├─ masters/          ← put source videos here
   ├─ renders/          ← outputs land here
   └─ scripts/
      └─ render_variant.sh   ← copy from ./scripts/ in this project
```

**`.env`**
```ini
GENERIC_TIMEZONE=UTC
CREATIVE_SHEET_ID=
ASSET_STORE_URL=https://assets.yourcompany.com
ASSET_STORE_TOKEN=
```

**`n8n.Dockerfile`**: the official n8n image is hardened Alpine with no package manager, so the tools are built in a matching Alpine stage and copied across:
```dockerfile
FROM alpine:3.24 AS tools
RUN apk add --no-cache bash ffmpeg font-dejavu && mkdir /out && cp /usr/bin/ffmpeg /usr/bin/ffprobe /out/

FROM docker.n8n.io/n8nio/n8n:latest
USER root
COPY --from=tools /usr/lib/ /usr/lib/
COPY --from=tools /lib/ /lib/
COPY --from=tools /usr/share/fonts/ /usr/share/fonts/
COPY --from=tools /out/ /usr/bin/
COPY --from=tools /bin/bash /bin/bash
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
      - N8N_BLOCK_ENV_ACCESS_IN_NODE=false    # lets {{ $env.* }} resolve
      - NODES_EXCLUDE=[]                       # re-enables Execute Command
      - N8N_RESTRICT_FILE_ACCESS_TO=/data      # Read Render File may only read /data
    volumes:
      - n8n_data:/home/node/.n8n
      - ./data:/data
volumes:
  n8n_data:
```

```bash
docker compose up -d --build
```

Check the tools are there:

```bash
docker compose exec n8n ffprobe -version
```

> Already running the whole portfolio? The repo-root compose already has FFmpeg and mounts the script. Fill in the three variables in the root `.env`.

---

## 3. Credentials and scopes

**Two** n8n credentials, plus one API token in `.env`.

| Credential | Type | Nodes |
|---|---|---|
| Google Sheets | Google Sheets OAuth2 API | Log To Asset Manifest, Log Failed Render |
| Slack | Slack API | Alert Creative Ops, Post Render Summary, Alert Engineering (Slack) |
| Asset store token | `ASSET_STORE_TOKEN` in `.env` (sent as `Authorization: Bearer …`) | Upload To Asset Store |

### 3.1 Asset store token

| Permission | Why |
|---|---|
| **Write/upload** to the `renders/` prefix | The node uploads `renders/<campaign>/<variantId>.mp4` |
| No read, list or delete | The workflow never needs them. Keep the token narrow |

The endpoint contract: `POST {ASSET_STORE_URL}/upload`, `multipart/form-data` with fields `file` (the MP4) and `key` (the destination path). Retries 3× with 5 s between attempts, and a 120 s timeout.

For production, consider moving the token into an n8n **Header Auth** credential (node → *Authentication → Generic → Header Auth*) so it's stored encrypted and hidden in execution logs.

### 3.2 Google Sheets OAuth2 API

**Scopes** (n8n requests these automatically): `https://www.googleapis.com/auth/spreadsheets` and `https://www.googleapis.com/auth/drive.file`.

1. In <https://console.cloud.google.com>, create a project. Under **APIs & Services → Library**, enable **Google Sheets API** and **Google Drive API**.
2. **Google Auth Platform → Branding / Audience**: choose **Internal** or **External** and add yourself as a Test user. ⚠️ Tokens expire after **7 days** while the app is in Testing, so **Publish app** once it works.
3. **Credentials → OAuth client ID → Web application**. Redirect URI: `http://localhost:5678/rest/oauth2-credential/callback`.
4. In n8n: **Credentials → Google Sheets OAuth2 API**, paste the ID and secret, then **Sign in with Google**.

### 3.3 Slack API (bot token)

Bot scopes: `chat:write` (**required**), `chat:write.public` (recommended), `channels:read` / `groups:read` (so the channel picker works).

1. Go to <https://api.slack.com/apps> → **Create New App → From scratch**.
2. Add the scopes, then **Install to Workspace**.
3. Copy the `xoxb-…` token.
4. In n8n: **Credentials → Slack API**.

---

## 4. Prepare Google Sheets and Slack

**Spreadsheet** (its ID goes in `CREATIVE_SHEET_ID`):

| Tab | Header row |
|---|---|
| `Asset Manifest` | `variantId, assetId, campaign, ratio, hook, durationSec, bytes, codec, status, renderedAt` |
| `Failed Renders` | `variantId, assetId, problems, actualWidth, actualHeight, failedAt` |

**Slack channels:** `#creative-ops` and `#eng-alerts`.

---

## 5. Import and configure

1. **Import:** open **Workflows → ⋯ → Import from File** and pick `workflow.json`.
2. **Connect credentials** on each node marked ⚠️.
3. **Check failure alerts:** the workflow already names itself as its Error Workflow, so the *Workflow Error → Alert Engineering* branch is live. If you imported it through the editor rather than `n8n import:workflow` or `scripts/n8n_sync.py`, n8n may have given it a new ID, so open **⋯ → Settings → Error Workflow** and make sure *this workflow* is selected.
4. **Activate** it (**Publish** in n8n 2.x).

**Request body:**

| Field | Required | Notes |
|---|---|---|
| `assetId` | yes | Used in the variant IDs and file names |
| `sourcePath` | yes | A path **inside the container**, e.g. `/data/masters/hero.mp4` (= `./data/masters/hero.mp4` on the host) |
| `campaign` | no | Defaults to `untagged` |
| `formats` | no | Any of `9:16` (1080×1920), `1:1` (1080×1080), `16:9` (1920×1080), `4:5` (1080×1350). Defaults to `9:16`, `1:1`, `16:9` |
| `hooks` | no | Text burned into the top third, one variant per hook. Defaults to no overlay |

---

## 6. Node reference

| Node | Type | What it does |
|---|---|---|
| Render Request Webhook | Webhook `POST /render-request` | Entry point |
| Build Render Matrix | Code (JS) | Expands formats × hooks into one job per variant. Unknown formats are skipped; if none are valid, the run fails |
| Ack Request | Respond to Webhook (202) | `{"queued": N, "assetId": …}`, sent before rendering starts |
| Render One At A Time | Split In Batches (size 1) | A serial loop, so the CPU isn't overloaded |
| FFmpeg Transcode | Execute Command → `render_variant.sh` | Writes `/data/renders/<variantId>.mp4` |
| Probe Output | Execute Command `ffprobe` | |
| Validate Render | Code (JS) | Fails a render that's under 1 s, under 10 KB, or has the wrong width/height |
| Render OK? | IF | |
| Generate Thumbnail | Execute Command `ffmpeg -frames:v 1` | Writes a `.jpg` next to the MP4 |
| Read Render File | Read/Write Files From Disk | Loads the MP4 as binary |
| Upload To Asset Store | HTTP POST multipart, Bearer, retry 3× | |
| Log To Asset Manifest | Sheets append → back to the loop | `status=ready` |
| Log Failed Render → Alert Creative Ops | Sheets → Slack → back to the loop | A failure doesn't stop the batch |
| Batch Complete? → Post Render Summary | No-Op → Slack `#creative-ops` | Runs once, after the last variant |
| Workflow Error → Alert Engineering | Error Trigger → Slack `#eng-alerts` | Includes the failing node and execution URL |

---

## 7. Test

Put a video at `./data/masters/hero.mp4`, then:

```bash
curl -X POST http://localhost:5678/webhook/render-request -H "Content-Type: application/json" -d "{\"assetId\":\"hero01\",\"campaign\":\"summer\",\"sourcePath\":\"/data/masters/hero.mp4\",\"formats\":[\"9:16\",\"1:1\"],\"hooks\":[\"Try it free\"]}"
```

Expected: an instant `202 {"queued":2,…}`, then two MP4s and two JPGs in `./data/renders/`, two `Asset Manifest` rows, and a summary in `#creative-ops`.
Point `sourcePath` at a non-existent file to see the failure path: rows in `Failed Renders` and alerts in `#creative-ops`.

---

## 8. Using S3, GCS or Cloudinary instead

Replace **Upload To Asset Store** with n8n's native **AWS S3**, **Google Cloud Storage** or **Cloudinary** node. The input binary property is `data`, and the key expression is `renders/{{ $('Validate Render').item.json.campaign }}/{{ $('Validate Render').item.json.variantId }}.mp4`. The permission needed: S3 `s3:PutObject` on `arn:aws:s3:::<bucket>/renders/*`; GCS *Storage Object Creator* on the bucket. Then delete `ASSET_STORE_URL` and `ASSET_STORE_TOKEN` from `.env`.

---

## 9. Optional: connecting it to the Generative Creative Factory

The Creative Factory (project 5) can `POST` to this webhook automatically for briefs that include a master video. That needs nothing here beyond this workflow being **active** on the same n8n. If you don't use project 5, ignore this.

---

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `ffmpeg: not found` / `bash: not found` | You're on the stock image. Build `n8n.Dockerfile` |
| `No such file` for `render_variant.sh` | Copy it to `./data/scripts/` |
| `Access to the file is not allowed` | The path is outside `/data`. Keep sources and outputs under `/data` |
| Hook text missing | The DejaVu font isn't installed (the script skips the overlay rather than failing) |
| Upload 401/403 | The token is wrong, or lacks write access to `renders/` |
| Webhook 404 | The workflow isn't active |
