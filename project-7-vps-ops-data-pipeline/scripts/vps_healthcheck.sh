#!/usr/bin/env bash
#
# vps_healthcheck.sh — one JSON object describing whether an Ubuntu VPS is healthy.
#
# Called over SSH by the n8n "Run VPS Healthcheck" node. Deliberately emits JSON
# and nothing else on stdout, so the workflow can parse it without regex-scraping
# human-readable output. Warnings and diagnostics go to stderr.
#
# Usage: vps_healthcheck.sh [--json] [--domain example.com] [--mount /]
#
# Exit code is always 0 when the check itself ran — "the server is unhealthy" is
# data for the workflow to act on, not an error in this script. A non-zero exit
# here means the check could not be performed at all.

set -Eeuo pipefail

DOMAIN="${HEALTHCHECK_DOMAIN:-}"
MOUNT="/"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json)   shift ;;                       # accepted for readability at the call site
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --mount)  MOUNT="${2:-/}"; shift 2 ;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

# --- helpers ------------------------------------------------------------------

# Emit a JSON array from newline-separated input, with nothing = []
json_array() {
  local first=1 line
  printf '['
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    line="${line//\\/\\\\}"; line="${line//\"/\\\"}"
    (( first )) || printf ','
    printf '"%s"' "$line"
    first=0
  done
  printf ']'
}

# --- disk ---------------------------------------------------------------------
DISK_PCT=$(df --output=pcent "$MOUNT" 2>/dev/null | tail -1 | tr -dc '0-9')
DISK_PCT="${DISK_PCT:-0}"
DISK_AVAIL_MB=$(df --output=avail -BM "$MOUNT" 2>/dev/null | tail -1 | tr -dc '0-9')
DISK_AVAIL_MB="${DISK_AVAIL_MB:-0}"

# --- memory -------------------------------------------------------------------
# Use "available" rather than "free": Linux deliberately spends free memory on
# page cache, so `free` looks alarming on a perfectly healthy box.
read -r MEM_TOTAL MEM_AVAIL < <(
  awk '/^MemTotal:/ {t=$2} /^MemAvailable:/ {a=$2} END {print t, a}' /proc/meminfo
)
MEM_TOTAL="${MEM_TOTAL:-1}"; MEM_AVAIL="${MEM_AVAIL:-0}"
MEM_PCT=$(( (MEM_TOTAL - MEM_AVAIL) * 100 / MEM_TOTAL ))

# --- load / uptime ------------------------------------------------------------
LOAD1=$(awk '{print $1}' /proc/loadavg)
CORES=$(nproc 2>/dev/null || echo 1)
UPTIME_S=$(awk '{printf "%d", $1}' /proc/uptime)

# --- docker -------------------------------------------------------------------
UNHEALTHY=""
CONTAINERS_RUNNING=0
DOCKER_REACHABLE=false
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  DOCKER_REACHABLE=true
  # Note the `|| true` on every docker pipeline: `set -o pipefail` turns a
  # transient daemon hiccup into a hard exit, and a healthcheck that dies
  # because one probe failed tells the workflow nothing at all.
  CONTAINERS_RUNNING=$({ docker ps -q 2>/dev/null || true; } | wc -l | tr -d ' ')
  # Anything not in the "Up" state, plus anything explicitly reporting unhealthy.
  UNHEALTHY=$(
    docker ps -a --format '{{.Names}}\t{{.Status}}' 2>/dev/null \
      | awk -F'\t' '$2 !~ /^Up/ || $2 ~ /unhealthy/ {print $1}' || true
  )
elif command -v docker >/dev/null 2>&1; then
  printf 'warning: docker is installed but the daemon is unreachable\n' >&2
fi

# --- systemd ------------------------------------------------------------------
FAILED_UNITS=""
if command -v systemctl >/dev/null 2>&1 && systemctl is-system-running >/dev/null 2>&1; then
  FAILED_UNITS=$(
    systemctl list-units --state=failed --no-legend --plain 2>/dev/null \
      | awk '{print $1}' || true
  )
fi

# --- TLS certificate ----------------------------------------------------------
CERT_DAYS=999
if [[ -n "$DOMAIN" ]] && command -v openssl >/dev/null 2>&1; then
  # `|| true` so a timeout or an unreachable host degrades to "unknown" instead
  # of killing the whole healthcheck under set -e.
  END_DATE=$(
    echo | timeout 10 openssl s_client -servername "$DOMAIN" -connect "$DOMAIN:443" 2>/dev/null \
      | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2 || true
  )
  if [[ -n "$END_DATE" ]]; then
    END_EPOCH=$(date -d "$END_DATE" +%s 2>/dev/null || echo 0)
    if (( END_EPOCH > 0 )); then
      CERT_DAYS=$(( (END_EPOCH - $(date +%s)) / 86400 ))
    fi
  else
    printf 'warning: could not read TLS certificate for %s\n' "$DOMAIN" >&2
  fi
fi

# --- emit ---------------------------------------------------------------------
cat <<JSON
{
  "host": "$(hostname)",
  "checked_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "uptime_seconds": ${UPTIME_S},
  "disk_used_pct": ${DISK_PCT},
  "disk_available_mb": ${DISK_AVAIL_MB},
  "mem_used_pct": ${MEM_PCT},
  "load_1m": ${LOAD1},
  "cores": ${CORES},
  "docker_reachable": ${DOCKER_REACHABLE},
  "containers_running": ${CONTAINERS_RUNNING},
  "unhealthy_containers": $(printf '%s\n' "$UNHEALTHY" | json_array),
  "failed_units": $(printf '%s\n' "$FAILED_UNITS" | json_array),
  "cert_domain": "${DOMAIN}",
  "cert_days_left": ${CERT_DAYS}
}
JSON
