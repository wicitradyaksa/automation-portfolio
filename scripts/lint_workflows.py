"""Static checks for the n8n workflow exports in project-*/workflow.json.

Catches the classes of bug that survive an import but break at run time:

  * connections to nodes that do not exist
  * $('Node Name') references to nodes that do not exist
  * `$json.x` read directly after a node that *replaces* the item (Slack, Sheets,
    HTTP, SSH, Postgres, ...), where the author almost always meant an upstream node
  * node versions whose parameter shape this repo does not use
  * syntax errors in Code-node JavaScript (needs `node` on PATH; skipped otherwise)
  * workflows with no Error Trigger

Usage:  py scripts/lint_workflows.py        (exit code 1 on any finding)
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Nodes whose output is the *service response*, not the incoming item.
REPLACES_ITEM = {
    "slack", "googleSheets", "httpRequest", "emailSend", "executeCommand", "ssh",
    "postgres", "readWriteFile", "extractFromFile",
}
# $json fields that are legitimately produced by the replacing node itself.
RESPONSE_FIELDS = {
    "executeCommand": {"stdout", "stderr", "exitCode"},
    "ssh": {"stdout", "stderr", "code"},
    "httpRequest": {"statusCode", "body", "headers", "prompt_id"},
    "googleSheets": {"touchCount", "email", "row_number", "briefId"},
    "postgres": {"rows_today", "null_keys", "duplicate_keys", "avg_prior_7d"},
    "extractFromFile": {"text"},
}
EXPECTED_VERSIONS = {
    "if": {2}, "slack": {2.2}, "googleSheets": {4.5}, "code": {2}, "httpRequest": {4.2},
    "respondToWebhook": {1.1}, "webhook": {2}, "scheduleTrigger": {1.2}, "wait": {1.1},
}


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def lint(path):
    w = json.loads(path.read_text(encoding="utf-8"))
    names = {n["name"]: n for n in w["nodes"]}
    kind = {n["name"]: n["type"].split(".")[-1] for n in w["nodes"]}
    problems = []

    preds = {}
    for src, c in w["connections"].items():
        if src not in names:
            problems.append(f"connection from missing node {src!r}")
        for outs in c["main"]:
            for o in outs:
                if o["node"] not in names:
                    problems.append(f"{src!r} connects to missing node {o['node']!r}")
                preds.setdefault(o["node"], set()).add(src)

    if not any(k == "errorTrigger" for k in kind.values()):
        problems.append("no Error Trigger node")

    for n in w["nodes"]:
        t = kind[n["name"]]
        exp = EXPECTED_VERSIONS.get(t)
        if exp and n["typeVersion"] not in exp:
            problems.append(f"{n['name']!r}: {t} v{n['typeVersion']} (expected {sorted(exp)})")
        for s in strings(n["parameters"]):
            for ref in re.findall(r"\$\('([^']+)'\)", s):
                if ref not in names:
                    problems.append(f"{n['name']!r} references missing node $('{ref}')")
            if t == "code":
                continue  # Code nodes read $input, not $json
            for field in set(re.findall(r"\$json\.(\w+)", s)):
                for p in preds.get(n["name"], ()):
                    pt = kind[p]
                    if pt in REPLACES_ITEM and field not in RESPONSE_FIELDS.get(pt, set()):
                        problems.append(
                            f"{n['name']!r} reads $json.{field} but its input comes from {p!r} ({pt}),"
                            " which replaces the item")
    problems += check_js(w)
    return problems


def check_js(w):
    node_bin = shutil.which("node")
    if not node_bin:
        return []
    out = []
    for n in w["nodes"]:
        code = n["parameters"].get("jsCode")
        if not code:
            continue
        # Code-node bodies are function bodies (top-level return), so wrap them.
        src = "async function __n8n(){\n" + code + "\n}\n"
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(src)
        r = subprocess.run([node_bin, "--check", f.name], capture_output=True, text=True)
        Path(f.name).unlink(missing_ok=True)
        if r.returncode:
            msg = (r.stderr.strip().splitlines() or ["syntax error"])[-1]
            out.append(f"{n['name']!r}: JavaScript syntax error: {msg}")
    return out


def main():
    total = 0
    for path in sorted(ROOT.glob("project-*/workflow.json")):
        problems = lint(path)
        total += len(problems)
        status = "ok" if not problems else f"{len(problems)} problem(s)"
        print(f"{path.parent.name:45} {status}")
        for p in problems:
            print("    -", p)
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
