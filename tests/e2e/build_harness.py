"""Turn each project workflow.json into an executable e2e copy.

What changes, and why:
  * Slack / Google Sheets / Email Send / SSH / Postgres nodes become HTTP Requests that POST
    their parameters to the mock API. n8n still resolves every expression, so the mock sees
    exactly what the real node would have sent. Node name, position, connections and settings
    (Always Output Data, Execute Once, On Error) are kept, so the data flow is the real one.
  * Schedule and IMAP triggers become Manual Triggers so the CLI can run them. Webhook
    triggers stay real and are called over HTTP.
  * Hard-coded demo hosts are pointed at the mock.

Everything else (Code, IF, Switch, Merge, Split In Batches, Wait, Execute Command, real HTTP
Request nodes, Respond to Webhook) runs unchanged.
"""
import json
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / ".work" / "workflows"
MOCK = "http://mock:8765"

WORKFLOW_IDS = {f"project-{i}": f"PortfolioE2E{i:04d}" for i in range(1, 8)}
MOCKED = {"slack", "googleSheets", "emailSend", "ssh", "postgres"}
KEEP_FLAGS = ("alwaysOutputData", "executeOnce", "onError", "retryOnFail", "maxTries", "waitBetweenTries")

HOST_PATCHES = {
    "http://api-gateway:8080/health": f"{MOCK}/health/api-gateway",
    "http://worker-queue:8081/health": f"{MOCK}/health/worker-queue",
    "https://example.com/health": f"{MOCK}/health/public-website",
    # P5: give up after 30 s instead of 5 minutes so the wedged-queue scenario runs quickly
    "const MAX_WAIT_MS = 5 * 60 * 1000": "const MAX_WAIT_MS = 30 * 1000",
}


def flatten(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from flatten(v, f"{prefix}{k}.")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from flatten(v, f"{prefix}{i}.")
    elif obj is not None and not (isinstance(obj, (dict, list))):
        yield prefix[:-1], obj if isinstance(obj, str) else json.dumps(obj)


def mock_node(n, kind):
    params = n["parameters"]
    op = params.get("operation", "post" if kind == "slack" else "execute")
    body = [{"name": k, "value": v} for k, v in flatten(params)
            if not k.startswith("columns.schema") and k != "operation"]
    new = {
        "id": n["id"], "name": n["name"], "position": n["position"],
        "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
        "parameters": {
            "method": "POST",
            "url": f"{MOCK}/svc/{kind}/{op}/{quote(n['name'], safe='')}",
            "sendBody": True, "contentType": "json", "specifyBody": "keypair",
            "bodyParameters": {"parameters": body},
            "options": {},
        },
    }
    for f in KEEP_FLAGS:
        if f in n:
            new[f] = n[f]
    return new


def build(folder):
    w = json.loads((folder / "workflow.json").read_text(encoding="utf-8"))
    key = "-".join(folder.name.split("-")[:2])
    wid = WORKFLOW_IDS[key]
    w["id"] = wid
    w["name"] = "[e2e] " + w["name"]
    w["settings"] = {**w.get("settings", {}), "errorWorkflow": wid}
    w.pop("pinData", None)
    nodes = []
    for n in w["nodes"]:
        t = n["type"].split(".")[-1]
        if t in MOCKED:
            nodes.append(mock_node(n, t))
            continue
        if t == "scheduleTrigger":
            n = {**n, "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "parameters": {}}
        if t == "emailReadImap":
            # Manual Trigger -> fetch the fixture PDF as binary `attachment_0`, like IMAP would.
            trigger = {"id": n["id"] + "-t", "name": "Manual Trigger", "type": "n8n-nodes-base.manualTrigger",
                       "typeVersion": 1, "position": [n["position"][0] - 200, n["position"][1]], "parameters": {}}
            nodes.append(trigger)
            w["connections"]["Manual Trigger"] = {"main": [[{"node": n["name"], "type": "main", "index": 0}]]}
            n = {"id": n["id"], "name": n["name"], "position": n["position"],
                 "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
                 "parameters": {"method": "GET", "url": f"{MOCK}/files/invoice.pdf", "options": {
                     "response": {"response": {"responseFormat": "file", "outputPropertyName": "attachment_0"}}}}}
        if t == "code":
            code = n["parameters"]["jsCode"]
            for old, new in HOST_PATCHES.items():
                code = code.replace(old, new)
            n = {**n, "parameters": {**n["parameters"], "jsCode": code}}
        n.pop("credentials", None)
        nodes.append(n)
    w["nodes"] = nodes
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{key}.json").write_text(json.dumps(w, indent=2), encoding="utf-8")
    return key, wid


if __name__ == "__main__":
    for folder in sorted(ROOT.glob("project-[1-7]-*")):
        print(*build(folder))
    sys.exit(0)
