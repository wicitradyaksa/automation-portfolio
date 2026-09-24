"""One HTTP server that plays every external system the seven workflows talk to.

The e2e harness rewrites each SaaS node (Slack, Google Sheets, SMTP, SSH, Postgres) into an
HTTP Request that POSTs its *resolved* parameters here, so a test can assert on exactly what
the workflow would have sent. Real HTTP Request nodes (ad APIs, ComfyUI, asset store, health
checks) call the matching endpoints below unchanged.

Control endpoints (used by run_e2e.py):
  POST /_scenario   replace the scenario state (JSON)
  GET  /_calls      every recorded call, in order
"""
import io
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

LOCK = threading.Lock()
STATE = {}
CALLS = []


def png_bytes(w=256, h=256):
    from PIL import Image
    buf = io.BytesIO()
    Image.effect_noise((w, h), 64).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def unflatten(body):
    """keypair bodies arrive as {"columns.value.email": "..."}; rebuild the nesting."""
    out = {}
    for key, value in body.items():
        cur = out
        parts = key.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    # ---------------------------------------------------------------- helpers
    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        ctype = self.headers.get("Content-Type", "")
        if "json" in ctype and raw:
            try:
                return json.loads(raw), raw
            except ValueError:
                pass
        return None, raw

    def send(self, code, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def record(self, **kw):
        with LOCK:
            CALLS.append(kw)

    # ---------------------------------------------------------------- routes
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path
        self.record(method="GET", path=path, query=q)

        if path == "/_calls":
            with LOCK:
                return self.send(200, CALLS)
        if path.startswith("/health/"):
            seq = STATE.setdefault("health", {}).setdefault(path.split("/")[-1], [200])
            code = seq.pop(0) if len(seq) > 1 else seq[0]
            return self.send(code, {"status": "ok" if code == 200 else "down"})
        if path == "/files/invoice.pdf":
            return self.send(200, bytes.fromhex(STATE["invoice_pdf_hex"]), "application/pdf")
        if path == "/ads/insights":
            page = int(q.get("page", "1"))
            pages = STATE.get("ads_pages", [[]])
            data = {"data": pages[page - 1]}
            if page < len(pages):
                data["paging"] = {"next": f"http://mock:8765/ads/insights?page={page + 1}"}
            return self.send(200, data)
        if path == "/ads/adsets":
            return self.send(200, {"data": STATE.get("adsets", [])})
        if path == "/aff/conversions":
            return self.send(200, {"data": STATE.get("conversions", [])})
        if path.startswith("/comfy/history/"):
            with LOCK:
                STATE["history_polls"] = STATE.get("history_polls", 0) + 1
                polls = STATE["history_polls"]
            pid = path.split("/")[-1]
            if polls < STATE.get("history_ready_after", 2):
                return self.send(200, {})
            return self.send(200, {pid: {"outputs": {"7": {"images": [
                {"filename": "img_0001.png", "subfolder": "", "type": "output"},
                {"filename": "img_0002.png", "subfolder": "", "type": "output"}]}}}})
        if path == "/comfy/view":
            return self.send(200, png_bytes(), "image/png")
        return self.send(404, {"error": "no route", "path": path})

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        js, raw = self.body()

        if path == "/_scenario":
            with LOCK:
                STATE.clear()
                STATE.update(js or {})
                CALLS.clear()
            return self.send(200, {"ok": True})

        m = re.match(r"^/svc/([^/]+)/([^/]+)/(.+)$", path)
        if m:
            kind, op, node = m.groups()
            from urllib.parse import unquote
            node = unquote(node)
            params = unflatten(js or {})
            self.record(method="POST", path=path, kind=kind, op=op, node=node, params=params)
            if node in STATE.get("fail_nodes", []):
                return self.send(503, {"error": "simulated outage"})
            try:
                return self.send(200, self.service_response(kind, op, node, params))
            except Exception as e:  # a mock bug must not look like a network failure
                import traceback
                return self.send(500, {"mock_error": repr(e), "trace": traceback.format_exc()})

        self.record(method="POST", path=path, json=js, bytes=len(raw),
                    has_file=b'filename=' in raw, ctype=self.headers.get("Content-Type", ""))
        m = re.match(r"^/docker/containers/([^/]+)/restart$", path)
        if m:  # Docker Engine API via the socket proxy: 204 for a known container, 404 otherwise
            if m.group(1) in STATE.get("containers", []):
                self.send_response(204)
                self.end_headers()
                return
            return self.send(404, {"message": f"No such container: {m.group(1)}"})
        if path == "/comfy/prompt":
            return self.send(200, {"prompt_id": "p-123", "number": 1})
        if path.startswith("/ads/"):
            return self.send(200, {"success": True})
        if path == "/assets/upload":
            return self.send(200, {"stored": True})
        return self.send(404, {"error": "no route", "path": path})

    # ---------------------------------------------------------------- fake services
    def service_response(self, kind, op, node, p):
        if kind == "slack":
            return {"ok": True, "channel": p.get("channelId", {}).get("value"), "ts": "1700000000.0001"}
        if kind == "emailSend":
            return {"accepted": [p.get("toEmail")]}
        if kind == "googleSheets":
            sheet = p.get("sheetName", {}).get("value")
            if op == "read":
                rows = STATE.get("sheets", {}).get(sheet, [])
                vals = (p.get("filtersUI") or {}).get("values") or []
                vals = vals if isinstance(vals, list) else list(vals.values())
                f = vals[0] if vals else {}
                if f.get("lookupColumn"):
                    rows = [r for r in rows if str(r.get(f["lookupColumn"])) == str(f.get("lookupValue"))]
                return rows
            return (p.get("columns", {}) or {}).get("value", {})
        if kind == "ssh":
            cmd = p.get("command", "")
            for needle, stdout in STATE.get("ssh", {}).items():
                if needle in cmd:
                    return {"stdout": stdout, "stderr": "", "code": 0}
            return {"stdout": "", "stderr": "no fixture", "code": 1}
        if kind == "postgres":
            q = p.get("query", "")
            for needle, rows in STATE.get("pg", {}).items():
                if needle in q:
                    return rows
            return [{"ok": True}]
        return {"ok": True}


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8765), Handler).serve_forever()
