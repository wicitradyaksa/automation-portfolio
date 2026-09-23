"""The exporter: ``/events`` in, ``/metrics`` out.

Deliberately ``http.server``. It is stdlib, it runs anywhere, and the thing
being demonstrated is the metric design and the exposition format -- not a web
framework. The limits of this choice are stated in the README rather than left
for a reviewer to find.

Endpoints:

* ``POST /events``  -- an n8n workflow reports its outcome here
* ``GET  /metrics`` -- Prometheus scrapes this
* ``GET  /healthz`` -- liveness, cheap, no metric work
* ``GET  /demo``    -- generate synthetic traffic so the dashboard has shape
"""

from __future__ import annotations

import json
import random
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .collector import Collector
from .correlation import context, from_headers
from .logging_setup import configure
from .metrics import Registry

WORKFLOWS = (
    "lead-intake-crm-sync",
    "invoice-pipeline",
    "uptime-monitor",
    "media-render-farm",
    "generative-creative-factory",
    "dco-engine",
    "vps-ops-nightly",
)

# Bodies bigger than this are refused without reading them. An unbounded read
# on a public-ish endpoint is a free out-of-memory for anyone who finds it.
MAX_BODY = 256 * 1024


class Handler(BaseHTTPRequestHandler):
    registry: Registry = None
    collector: Collector = None
    logger = None

    protocol_version = "HTTP/1.1"

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/metrics":
            # text/plain; version=0.0.4 is what Prometheus expects. Serving
            # application/json here is a scrape that fails with a parse error
            # rather than a connection error, which is much harder to spot.
            self._respond(200, self.registry.render(), "text/plain; version=0.0.4; charset=utf-8")
        elif path == "/healthz":
            self._respond(200, '{"ok":true}\n', "application/json")
        elif path == "/demo":
            self._respond(200, json.dumps(self._demo(), indent=2) + "\n", "application/json")
        else:
            self._respond(404, '{"error":"not found"}\n', "application/json")

    def do_POST(self):
        if self.path.split("?")[0] != "/events":
            return self._respond(404, '{"error":"not found"}\n', "application/json")

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._respond(413, '{"error":"body too large"}\n', "application/json")

        raw = self.rfile.read(length) if length else b"{}"

        # The correlation id arrives from the calling workflow, so the log
        # line written here can be joined to the log lines written three hops
        # upstream. That join is the entire point of the project.
        with context(from_headers(dict(self.headers))) as cid:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self.collector.rejected_events.inc(reason="unparseable")
                self.logger.warning("event body was not valid JSON", extra={"bytes": len(raw)})
                return self._respond(400, '{"error":"invalid json"}\n', "application/json")

            event = self.collector.record_json(payload)
            if event is None:
                self.logger.warning("event rejected", extra={"payload_keys": sorted(payload)})
                # 400, not 500: the sender is wrong, and a 5xx would make n8n
                # retry a malformed event forever.
                return self._respond(400, '{"error":"invalid event"}\n', "application/json")

            self.logger.info(
                "recorded execution",
                extra={
                    "workflow": event.workflow,
                    "status": event.status,
                    "duration_seconds": round(event.duration_seconds, 3),
                    "items": event.items,
                    "retries": event.retries,
                },
            )
            return self._respond(202, json.dumps({"ok": True, "correlation_id": cid}) + "\n", "application/json")

    def _demo(self) -> dict:
        """Synthetic traffic, so /metrics and the dashboard have shape."""
        rng = random.Random()
        recorded = 0
        for workflow in WORKFLOWS:
            for _ in range(rng.randint(8, 25)):
                failed = rng.random() < 0.08
                self.collector.record_json(
                    {
                        "workflow": workflow,
                        "status": "error" if failed else "success",
                        "duration_seconds": round(abs(rng.gauss(4 if "render" not in workflow else 70, 3)), 3),
                        "retries": rng.choice([0, 0, 0, 1, 2]),
                        "items": 0 if failed else rng.randint(1, 40),
                        "node": rng.choice(["HTTP Request", "Postgres", "Execute Command"]) if failed else "",
                    }
                )
                recorded += 1
        return {"recorded": recorded, "workflows": len(WORKFLOWS), "series": len(self.registry.names())}

    def _respond(self, status: int, body: str, content_type: str):
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, fmt, *args):
        # Silence the default stderr access log. Everything goes through the
        # JSON logger or it is not a log.
        pass


def build(registry: Registry | None = None) -> tuple[Registry, Collector]:
    registry = registry or Registry()
    collector = Collector(registry=registry)
    collector.seed(WORKFLOWS)
    return registry, collector


def serve(port: int = 9109, *, registry=None, collector=None, block: bool = True):
    logger = configure(service="n8n-observability")
    if registry is None or collector is None:
        registry, collector = build(registry)

    Handler.registry = registry
    Handler.collector = collector
    Handler.logger = logger

    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    logger.info("exporter listening", extra={"port": port, "endpoints": ["/metrics", "/events", "/healthz", "/demo"]})
    if not block:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down")
    finally:
        httpd.server_close()
    return httpd


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="n8n observability exporter")
    parser.add_argument("--port", type=int, default=9109)
    args = parser.parse_args()
    serve(args.port)
