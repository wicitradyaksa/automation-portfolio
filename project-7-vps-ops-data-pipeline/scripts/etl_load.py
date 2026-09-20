#!/usr/bin/env python3
"""
etl_load.py — nightly extract/transform/load from server logs into Postgres.

Called over SSH by the n8n "Run ETL Load" node. Reads the last N hours of nginx
access logs and n8n execution exports, normalises both into one flat `events`
schema, and upserts them idempotently so a re-run after a failure duplicates
nothing.

Usage:
  etl_load.py --since-hours 24 --target postgres --json
  etl_load.py --since-hours 6 --dry-run

Connection comes from PGHOST/PGDATABASE/PGUSER/PGPASSWORD in the environment —
credentials never appear in the command line, because the full command line of
every SSH call shows up in the n8n execution log.

Stdout (always JSON):
  {"ok": true, "rows_extracted": N, "rows_loaded": N, "sources": {...}}
"""
from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

NGINX_GLOB = os.environ.get("ETL_NGINX_GLOB", "/var/log/nginx/access.log*")
N8N_EXPORT_GLOB = os.environ.get("ETL_N8N_GLOB", "/opt/ops/exports/executions-*.json")

# Combined log format, plus the optional $request_time many configs append.
NGINX_RE = re.compile(
    r'(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] '
    r'"(?P<method>[A-Z]+) (?P<path>[^ "]+)[^"]*" '
    r'(?P<status>\d{3}) (?P<bytes>\d+|-)'
    r'(?: "(?P<referer>[^"]*)" "(?P<ua>[^"]*)")?'
    r'(?: (?P<rt>[\d.]+))?'
)

DDL = """
CREATE TABLE IF NOT EXISTS events (
    event_id      text PRIMARY KEY,
    source        text        NOT NULL,
    occurred_at   timestamptz NOT NULL,
    event_type    text,
    status        text,
    duration_ms   integer,
    path          text,
    client_ip     inet,
    bytes         bigint,
    attributes    jsonb       DEFAULT '{}'::jsonb,
    loaded_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_occurred_at_idx ON events (occurred_at);
CREATE INDEX IF NOT EXISTS events_loaded_at_idx   ON events (loaded_at);

CREATE TABLE IF NOT EXISTS events_quarantine (LIKE events INCLUDING ALL);
ALTER TABLE events_quarantine ADD COLUMN IF NOT EXISTS reason text;
"""

UPSERT = """
INSERT INTO events (event_id, source, occurred_at, event_type, status,
                    duration_ms, path, client_ip, bytes, attributes)
VALUES %s
ON CONFLICT (event_id) DO UPDATE SET
    status      = EXCLUDED.status,
    duration_ms = EXCLUDED.duration_ms,
    attributes  = EXCLUDED.attributes,
    loaded_at   = now();
"""


def open_maybe_gzip(path: str):
    return gzip.open(path, "rt", errors="replace") if path.endswith(".gz") \
        else open(path, "r", errors="replace")


def stable_id(*parts: object) -> str:
    """
    Deterministic surrogate key.

    Logs carry no primary key, so re-running the same window would otherwise
    insert every row twice. Hashing the natural key makes the load idempotent —
    which is the whole reason a failed nightly run can just be re-run.
    """
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:32]


def extract_nginx(since: datetime) -> list[tuple]:
    rows, skipped = [], 0
    for path in sorted(glob.glob(NGINX_GLOB)):
        try:
            with open_maybe_gzip(path) as fh:
                for line in fh:
                    m = NGINX_RE.match(line)
                    if not m:
                        skipped += 1
                        continue
                    try:
                        ts = datetime.strptime(m["ts"], "%d/%b/%Y:%H:%M:%S %z")
                    except ValueError:
                        skipped += 1
                        continue
                    if ts < since:
                        continue
                    nbytes = 0 if m["bytes"] == "-" else int(m["bytes"])
                    rt_ms = int(float(m["rt"]) * 1000) if m["rt"] else None
                    rows.append((
                        stable_id("nginx", m["ip"], m["ts"], m["method"], m["path"]),
                        "nginx", ts, f"http_{m['method'].lower()}", m["status"],
                        rt_ms, m["path"][:2000], m["ip"], nbytes,
                        json.dumps({"ua": (m["ua"] or "")[:500],
                                    "referer": (m["referer"] or "")[:500]}),
                    ))
        except OSError as e:
            print(f"warning: could not read {path}: {e}", file=sys.stderr)
    if skipped:
        print(f"warning: skipped {skipped} unparseable nginx lines", file=sys.stderr)
    return rows


def extract_n8n(since: datetime) -> list[tuple]:
    rows = []
    for path in sorted(glob.glob(N8N_EXPORT_GLOB)):
        try:
            with open(path) as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            print(f"warning: could not read {path}: {e}", file=sys.stderr)
            continue

        for ex in payload if isinstance(payload, list) else payload.get("data", []):
            started = ex.get("startedAt") or ex.get("createdAt")
            if not started:
                continue
            try:
                ts = datetime.fromisoformat(started.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts < since:
                continue

            stopped = ex.get("stoppedAt")
            duration = None
            if stopped:
                try:
                    duration = int(
                        (datetime.fromisoformat(stopped.replace("Z", "+00:00")) - ts)
                        .total_seconds() * 1000
                    )
                except ValueError:
                    duration = None

            rows.append((
                stable_id("n8n", ex.get("id"), started),
                "n8n", ts, "workflow_execution",
                ex.get("status", "unknown"), duration,
                None, None, None,
                json.dumps({"workflowId": ex.get("workflowId"),
                            "workflowName": (ex.get("workflowData") or {}).get("name"),
                            "mode": ex.get("mode")}),
            ))
    return rows


def load(rows: list[tuple], batch_size: int = 1000) -> int:
    try:
        import psycopg2
        from psycopg2.extras import execute_values
    except ImportError:
        raise RuntimeError("psycopg2 not installed — pip install psycopg2-binary")

    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        dbname=os.environ.get("PGDATABASE", "analytics"),
        user=os.environ.get("PGUSER", "etl"),
        password=os.environ.get("PGPASSWORD", ""),
        port=int(os.environ.get("PGPORT", 5432)),
        connect_timeout=15,
    )
    loaded = 0
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
                # One transaction for the whole load: a partial nightly load is
                # worse than none, because the DQ checks downstream would pass it.
                for i in range(0, len(rows), batch_size):
                    chunk = rows[i:i + batch_size]
                    execute_values(cur, UPSERT, chunk, page_size=batch_size)
                    loaded += len(chunk)
    finally:
        conn.close()
    return loaded


def main() -> int:
    ap = argparse.ArgumentParser(description="Nightly log -> Postgres ETL")
    ap.add_argument("--since-hours", type=int, default=24)
    ap.add_argument("--target", default="postgres", choices=["postgres"])
    ap.add_argument("--dry-run", action="store_true", help="extract and report, load nothing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    since = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)

    try:
        nginx_rows = extract_nginx(since)
        n8n_rows = extract_n8n(since)
        rows = nginx_rows + n8n_rows

        # De-duplicate within the batch itself — overlapping rotated log files
        # will hand us the same line twice, and ON CONFLICT can't resolve a
        # duplicate key that appears twice inside one INSERT statement.
        seen, deduped = set(), []
        for r in rows:
            if r[0] in seen:
                continue
            seen.add(r[0])
            deduped.append(r)

        loaded = 0 if args.dry_run else load(deduped)

        print(json.dumps({
            "ok": True,
            "dry_run": args.dry_run,
            "since": since.isoformat(),
            "rows_extracted": len(rows),
            "rows_after_dedupe": len(deduped),
            "rows_loaded": loaded,
            "duplicates_dropped": len(rows) - len(deduped),
            "sources": {"nginx": len(nginx_rows), "n8n": len(n8n_rows)},
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }))
        return 0

    except Exception as e:  # noqa: BLE001 — the workflow needs JSON, not a traceback
        print(json.dumps({
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "rows_loaded": 0,
        }))
        return 1


if __name__ == "__main__":
    sys.exit(main())
