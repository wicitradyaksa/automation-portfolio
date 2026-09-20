#!/usr/bin/env bash
#
# backup_volumes.sh — snapshot Docker named volumes to a verified, dated tarball.
#
# Called over SSH by the n8n "Backup Volumes" node. Emits one JSON object on
# stdout so the workflow can verify the result rather than trusting exit code 0.
#
# Usage: backup_volumes.sh [--retain N] [--dest DIR] [--volumes "a b c"] [--json]
#
# Exit codes: 0 ok · 2 bad args · 3 prerequisite missing · 4 archive failed
#             5 checksum verification failed

set -Eeuo pipefail

RETAIN=7
DEST="/var/backups/volumes"
VOLUMES=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --retain)  RETAIN="${2:-7}";  shift 2 ;;
    --dest)    DEST="${2:-}";     shift 2 ;;
    --volumes) VOLUMES="${2:-}";  shift 2 ;;
    --json)    shift ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

[[ "$RETAIN" =~ ^[0-9]+$ ]] || { printf -- '--retain must be an integer\n' >&2; exit 2; }
command -v docker >/dev/null || { printf 'docker not on PATH\n' >&2; exit 3; }

mkdir -p "$DEST"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="${DEST}/volumes_${STAMP}.tar.gz"
TMP="${ARCHIVE}.partial"

# Default to every named volume. Anonymous volumes (64-hex names) are scratch by
# definition and only bloat the archive.
if [[ -z "$VOLUMES" ]]; then
  VOLUMES="$(docker volume ls --format '{{.Name}}' | grep -Ev '^[0-9a-f]{64}$' || true)"
fi
[[ -n "$VOLUMES" ]] || { printf 'no named volumes to back up\n' >&2; exit 3; }

STAGING="$(mktemp -d "${DEST}/.staging.XXXXXX")"
cleanup() { rm -rf "$STAGING"; [[ -f "$TMP" ]] && rm -f "$TMP"; }
trap cleanup EXIT

# Copy each volume out through a throwaway alpine container — this is the only
# portable way to read a named volume's contents without knowing where the
# Docker storage driver actually put it on disk.
COPIED=0
for vol in $VOLUMES; do
  if docker run --rm \
        -v "${vol}:/from:ro" \
        -v "${STAGING}:/to" \
        alpine:3 sh -c "mkdir -p /to/${vol} && cp -a /from/. /to/${vol}/ 2>/dev/null || true"; then
    COPIED=$(( COPIED + 1 ))
  else
    printf 'warning: could not read volume %s — continuing\n' "$vol" >&2
  fi
done

(( COPIED > 0 )) || { printf 'every volume copy failed\n' >&2; exit 4; }

# Write to .partial first so an interrupted run never leaves a truncated archive
# that a later restore would happily and uselessly unpack.
if ! tar -czf "$TMP" -C "$STAGING" .; then
  printf 'tar failed\n' >&2; exit 4
fi

# Verify the archive is actually readable end to end before we call it a backup
# and before retention deletes an older, working one.
if ! tar -tzf "$TMP" >/dev/null 2>&1; then
  printf 'archive failed verification read\n' >&2; exit 5
fi

mv -f "$TMP" "$ARCHIVE"
CHECKSUM="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
printf '%s  %s\n' "$CHECKSUM" "$(basename "$ARCHIVE")" > "${ARCHIVE}.sha256"
BYTES=$(stat -c%s "$ARCHIVE")

# Retention runs last, and only after the new archive verified — never prune
# first, or a failed backup leaves you with nothing at all.
DELETED=$(
  find "$DEST" -maxdepth 1 -name 'volumes_*.tar.gz' -type f -printf '%T@ %p\n' \
    | sort -rn | tail -n "+$(( RETAIN + 1 ))" | cut -d' ' -f2- \
    | tee >(xargs -r -I{} rm -f {} {}.sha256) | wc -l | tr -d ' '
)

trap - EXIT
rm -rf "$STAGING"

cat <<JSON
{
  "host": "$(hostname)",
  "archive": "${ARCHIVE}",
  "bytes": ${BYTES},
  "checksum": "${CHECKSUM}",
  "checksum_verified": true,
  "volumes_backed_up": ${COPIED},
  "older_archives_pruned": ${DELETED:-0},
  "retain": ${RETAIN},
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
