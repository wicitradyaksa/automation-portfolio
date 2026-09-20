#!/usr/bin/env bash
#
# render_variant.sh — produce one ad-ready video variant from a master file.
#
# Called by the n8n "FFmpeg Transcode" node, once per variant. Everything that can
# reasonably be decided here is decided here, so the workflow stays a control-flow
# graph and the encoding details stay in one reviewable file.
#
# Usage:
#   render_variant.sh --src IN.mp4 --out OUT.mp4 --w 1080 --h 1920 \
#                     [--crf 23] [--maxrate 4M] [--hook "TEXT"] [--font PATH]
#
# Exit codes:
#   0  render completed and the output file is non-trivial
#   2  bad / missing arguments
#   3  source missing or unreadable
#   4  ffmpeg failed
#   5  ffmpeg reported success but the output is empty or unreadably short

set -Eeuo pipefail

# -e alone won't catch a failure inside a pipeline's left-hand side, and an
# unset variable in a path expansion is how you end up with `rm -rf /`.
# -E propagates the ERR trap into functions and subshells.

die() { printf 'render_variant: %s\n' "$*" >&2; exit "${2:-1}"; }

SRC="" OUT="" W="" H=""
CRF=23
MAXRATE="4M"
HOOK=""
FONT="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
LUFS="-14"          # broadcast-ish loudness target; most ad platforms normalise near this
MIN_BYTES=10240

while [[ $# -gt 0 ]]; do
  case "$1" in
    --src)     SRC="${2:-}";     shift 2 ;;
    --out)     OUT="${2:-}";     shift 2 ;;
    --w)       W="${2:-}";       shift 2 ;;
    --h)       H="${2:-}";       shift 2 ;;
    --crf)     CRF="${2:-}";     shift 2 ;;
    --maxrate) MAXRATE="${2:-}"; shift 2 ;;
    --hook)    HOOK="${2:-}";    shift 2 ;;
    --font)    FONT="${2:-}";    shift 2 ;;
    --lufs)    LUFS="${2:-}";    shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *)         die "unknown argument: $1" 2 ;;
  esac
done

[[ -n "$SRC" && -n "$OUT" && -n "$W" && -n "$H" ]] || die "need --src --out --w --h" 2
[[ "$W" =~ ^[0-9]+$ && "$H" =~ ^[0-9]+$ ]]          || die "--w/--h must be integers" 2
[[ -r "$SRC" ]]                                      || die "source not readable: $SRC" 3
command -v ffmpeg >/dev/null                         || die "ffmpeg not on PATH" 3

mkdir -p "$(dirname "$OUT")"

# Render to a temp file in the same directory, then atomically move it into place.
# Without this, a crash mid-encode leaves a half-written .mp4 that looks like a
# finished asset to anything watching the output folder.
#
# The temp name has to keep the real extension — ffmpeg picks its muxer from the
# output filename, and a bare mktemp name fails with "Unable to choose an output
# format". mktemp first (so the name is never guessable), then add the suffix.
OUT_EXT="${OUT##*.}"
TMP_BASE="$(mktemp "$(dirname "$OUT")/.$(basename "${OUT%.*}").XXXXXX")"
TMP="${TMP_BASE}.${OUT_EXT}"
mv -f "$TMP_BASE" "$TMP"

cleanup() { rm -f "$TMP" "$TMP_BASE" 2>/dev/null || true; }
trap cleanup EXIT

# --- video filter chain -------------------------------------------------------
# scale=…:force_original_aspect_ratio=decrease  fit inside the target box
# pad=…                                          letterbox/pillarbox to exact size
# setsar=1                                        square pixels (some platforms reject non-1 SAR)
VF="scale=${W}:${H}:force_original_aspect_ratio=decrease"
VF+=",pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=black"
VF+=",setsar=1"

# Burn the hook line into the top third — only if we were given one AND the font
# exists, because a missing fontfile makes ffmpeg fail the whole render.
if [[ -n "$HOOK" && "$HOOK" != "default" && -r "$FONT" ]]; then
  # escape the characters drawtext treats as syntax
  ESCAPED="${HOOK//\\/\\\\}"
  ESCAPED="${ESCAPED//:/\\:}"
  ESCAPED="${ESCAPED//\'/}"
  FONTSIZE=$(( W / 22 ))
  VF+=",drawtext=fontfile='${FONT}':text='${ESCAPED}'"
  VF+=":fontcolor=white:fontsize=${FONTSIZE}:box=1:boxcolor=black@0.55:boxborderw=18"
  VF+=":x=(w-text_w)/2:y=h*0.12"
fi

# --- encode -------------------------------------------------------------------
# -movflags +faststart puts the moov atom first so the file starts playing before
# it has fully downloaded — required by basically every ad platform's preview.
if ! ffmpeg -hide_banner -loglevel error -y \
      -i "$SRC" \
      -vf "$VF" \
      -c:v libx264 -preset medium -crf "$CRF" \
      -maxrate "$MAXRATE" -bufsize "$(( ${MAXRATE%M} * 2 ))M" \
      -pix_fmt yuv420p -profile:v high -level 4.1 \
      -af "loudnorm=I=${LUFS}:TP=-1.5:LRA=11" \
      -c:a aac -b:a 128k -ar 48000 -ac 2 \
      -movflags +faststart \
      "$TMP" 2> >(tee /dev/stderr >&2); then
  die "ffmpeg failed encoding $SRC -> $OUT" 4
fi

# --- post-flight --------------------------------------------------------------
BYTES=$(stat -c%s "$TMP" 2>/dev/null || stat -f%z "$TMP")
if (( BYTES < MIN_BYTES )); then
  die "output only ${BYTES} bytes — treating as a failed render" 5
fi

mv -f "$TMP" "$OUT"
trap - EXIT

printf '{"out":"%s","bytes":%s,"width":%s,"height":%s,"crf":%s}\n' \
       "$OUT" "$BYTES" "$W" "$H" "$CRF"
