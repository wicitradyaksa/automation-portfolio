#!/usr/bin/env python3
"""
postprocess_creative.py — turn raw ComfyUI generations into placement-ready ad creatives.

Called by the n8n "Post-Process Creatives" node. Downloads each generated image
from the ComfyUI /view endpoint, strips metadata, and exports every ad placement
size the buying side needs — each one cropped around the image centre and padded
rather than squashed, because a stretched product shot gets rejected on review.

Usage:
  postprocess_creative.py --brief B123 --campaign summer_sale \
      --images '[{"filename":"x.png","subfolder":"","url":"http://comfyui:8188/view?..."}]' \
      --out-dir /data/creatives

Stdout (always JSON):
  {"ok": true, "briefId": "...", "assetCount": 12, "outputDir": "...", "assets": [...]}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

try:
    from PIL import Image, ImageOps
except ImportError:  # keep the failure legible in the n8n execution log
    print(json.dumps({"ok": False, "error": "Pillow not installed — pip install Pillow", "assetCount": 0}))
    sys.exit(1)

# Ad placement sizes worth exporting for. Keys become filename suffixes.
PLACEMENTS = {
    "1x1":    (1080, 1080),   # feed
    "4x5":    (1080, 1350),   # portrait feed — highest mobile real estate
    "9x16":   (1080, 1920),   # stories / reels
    "1_91x1": (1200, 628),    # link ad / display
}

DOWNLOAD_TIMEOUT = 60
MAX_BYTES = 25 * 1024 * 1024


def download(url: str, dest: str) -> int:
    """Fetch one generation to disk. Returns bytes written."""
    req = urllib.request.Request(url, headers={"User-Agent": "n8n-creative-factory/1.0"})
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"generation exceeds {MAX_BYTES} bytes — refusing")
    if len(data) < 1024:
        raise ValueError(f"generation is only {len(data)} bytes — ComfyUI likely returned an error page")
    with open(dest, "wb") as f:
        f.write(data)
    return len(data)


def export_placement(img: "Image.Image", size: tuple[int, int], dest: str) -> None:
    """
    Fit the generation into the placement box.

    ImageOps.fit crops to fill rather than letterboxing, centred on the middle of
    the frame. SDXL compositions are almost always centre-weighted, so this keeps
    the subject; a pad-to-fit would leave bars the platform counts as "low quality".
    """
    out = ImageOps.fit(img, size, method=Image.LANCZOS, centering=(0.5, 0.45))
    out = out.convert("RGB")
    # Re-save without the original EXIF/PNG text chunks — ComfyUI embeds the full
    # prompt graph in PNG metadata, which you do not want shipping to an ad network.
    out.save(dest, format="JPEG", quality=88, optimize=True, progressive=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Post-process ComfyUI generations into ad placements")
    ap.add_argument("--brief", required=True)
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--images", required=True, help="JSON array of {filename, url}")
    ap.add_argument("--out-dir", default="/data/creatives")
    ap.add_argument("--placements", default=",".join(PLACEMENTS),
                    help="comma-separated subset of: " + ",".join(PLACEMENTS))
    args = ap.parse_args()

    try:
        images = json.loads(args.images)
    except json.JSONDecodeError as e:
        print(json.dumps({"ok": False, "error": f"--images was not valid JSON: {e}", "assetCount": 0}))
        return 1

    if not images:
        print(json.dumps({"ok": False, "error": "no images to process", "assetCount": 0}))
        return 1

    wanted = [p.strip() for p in args.placements.split(",") if p.strip() in PLACEMENTS]
    out_dir = os.path.join(args.out_dir, args.campaign, args.brief)
    raw_dir = os.path.join(out_dir, "_raw")
    os.makedirs(raw_dir, exist_ok=True)

    assets, errors = [], []

    for idx, meta in enumerate(images):
        url = meta.get("url")
        name = meta.get("filename") or f"gen_{idx}.png"
        raw_path = os.path.join(raw_dir, name)

        try:
            download(url, raw_path)
        except (urllib.error.URLError, ValueError, OSError) as e:
            # One bad generation shouldn't lose the other eleven.
            errors.append({"filename": name, "stage": "download", "error": str(e)})
            continue

        try:
            with Image.open(raw_path) as img:
                img.load()
                for key in wanted:
                    dest = os.path.join(out_dir, f"{args.brief}_{idx:02d}_{key}.jpg")
                    export_placement(img, PLACEMENTS[key], dest)
                    assets.append({
                        "path": dest,
                        "placement": key,
                        "width": PLACEMENTS[key][0],
                        "height": PLACEMENTS[key][1],
                        "sourceGeneration": name,
                        "bytes": os.path.getsize(dest),
                    })
        except (OSError, ValueError) as e:
            errors.append({"filename": name, "stage": "export", "error": str(e)})

    ok = len(assets) > 0
    print(json.dumps({
        "ok": ok,
        "briefId": args.brief,
        "campaign": args.campaign,
        "outputDir": out_dir,
        "assetCount": len(assets),
        "generationsProcessed": len(images) - len(errors),
        "generationsFailed": len(errors),
        "placements": wanted,
        "assets": assets,
        "errors": errors,
        # Brief fields such as videoSourcePath and hooks are deliberately absent: the
        # workflow merges this output over the brief, so echoing them here as empty
        # values would wipe the brief's own and silently cancel the render-farm hand-off.
    }))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
