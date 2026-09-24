"""Keep the workflows in your running n8n and the repo's project-*/workflow.json in step.

    py scripts/n8n_sync.py status          what differs, and in which direction
    py scripts/n8n_sync.py push            repo -> n8n   (after editing JSON / pulling from git)
    py scripts/n8n_sync.py pull            n8n  -> repo  (after editing in the n8n editor)
    py scripts/n8n_sync.py push --only 3   just project 3 (works for pull too)

How it avoids a mess:
  * Workflows are matched by id. A repo file without an id adopts the id of the n8n workflow
    with the same name, and the id is written back to the file, so a push never creates a
    duplicate.
  * Credentials never cross over. A push keeps the credentials you connected in the n8n editor;
    a pull keeps the repo's placeholder credentials, so your real account names never reach git.
  * A push backs up the current n8n version of every workflow it replaces to data/sync-backups/.
  * A pull runs the workflow linter on the result, so a broken edit is caught before you commit.

Talks to n8n through `docker exec` and the n8n CLI: no API key, and no running editor session.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTAINER = os.environ.get("N8N_CONTAINER", "N8N_Workflows")
SYNC_DIR = ROOT / "data" / "sync"          # ./data is mounted at /data in the container
BACKUP_DIR = ROOT / "data" / "sync-backups"
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}

# Fields n8n adds or bumps on its own; they carry no workflow logic.
VOLATILE = {"versionId", "activeVersionId", "updatedAt", "createdAt", "triggerCount", "shared", "tags",
            "isArchived", "staticData", "pinData", "meta", "parentFolderId", "versionCounter", "activeVersion",
            "description", "active"}


def cli(*args):
    cmd = ["docker", "exec", "-e", "N8N_RUNNERS_BROKER_PORT=5690", CONTAINER, "n8n", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
    if r.returncode:
        sys.exit(f"n8n CLI failed ({' '.join(args)}):\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return r.stdout


def live_workflows():
    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    cli("export:workflow", "--all", "--output=/data/sync/_export.json")
    path = SYNC_DIR / "_export.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    path.unlink()
    return data if isinstance(data, list) else [data]


def repo_workflows(only):
    out = []
    for f in sorted(ROOT.glob("project-[1-7]-*/workflow.json")):
        num = f.parent.name.split("-")[1]
        if only and num not in only:
            continue
        out.append((num, f, json.loads(f.read_text(encoding="utf-8"))))
    return out


# What the n8n editor changes on every save without changing behaviour.
EDITOR_SETTINGS = {"errorWorkflow", "binaryMode", "callerPolicy", "availableInMCP"}


def _strip_editor_noise(obj):
    if isinstance(obj, dict):
        return {k: _strip_editor_noise(v) for k, v in obj.items()
                if not (k == "version" and "typeValidation" in obj)}   # filter-schema version stamp
    if isinstance(obj, list):
        return [_strip_editor_noise(v) for v in obj]
    return obj


def node_logic(n):
    """A node minus canvas position, credentials and editor-assigned ids."""
    keep = {k: v for k, v in n.items() if k not in ("credentials", "position", "id")}
    if not n["type"].endswith((".webhook", "Trigger")):
        keep.pop("webhookId", None)       # the editor stamps these on non-webhook nodes too
    return _strip_editor_noise(keep)


def comparable(w):
    """The part of a workflow that is logic: nodes, connections and behaviour-changing settings."""
    nodes = [node_logic(n) for n in sorted(w["nodes"], key=lambda n: n["name"])]
    return json.dumps({"name": w["name"], "nodes": nodes, "connections": w["connections"],
                       "settings": {k: v for k, v in (w.get("settings") or {}).items() if k not in EDITOR_SETTINGS}},
                      sort_keys=True)


def differences(rw, lw):
    """Human-readable list of logic differences, parameter by parameter."""
    out = []
    rn = {n["name"]: node_logic(n) for n in rw["nodes"]}
    ln = {n["name"]: node_logic(n) for n in lw["nodes"]}
    for name in sorted(set(rn) | set(ln)):
        a, b = rn.get(name), ln.get(name)
        if a is None or b is None:
            out.append(f"node '{name}' only in {'n8n' if a is None else 'repo'}")
            continue

        def walk(x, y, path):
            if isinstance(x, dict) and isinstance(y, dict):
                for k in sorted(set(x) | set(y)):
                    walk(x.get(k), y.get(k), f"{path}.{k}")
            elif x != y:
                out.append(f"'{name}'{path}: repo={json.dumps(x)[:90]}  n8n={json.dumps(y)[:90]}")
        walk(a, b, "")
    if rw["connections"] != lw["connections"]:
        out.append("connections differ")
    return out


def match(repo_w, live):
    if repo_w.get("id"):
        for lw in live:
            if lw["id"] == repo_w["id"]:
                return lw
    named = [lw for lw in live if lw["name"] == repo_w["name"]]
    return named[0] if len(named) == 1 else None


def with_credentials_from(target, source):
    """Copy each node's credentials from `source` onto the same-named node in `target`."""
    creds = {n["name"]: n["credentials"] for n in source["nodes"] if n.get("credentials")}
    for n in target["nodes"]:
        if n["name"] in creds:
            n["credentials"] = creds[n["name"]]
    return target


def status(only, verbose=True):
    live = live_workflows()
    print(f"n8n container: {CONTAINER}   ({len(live)} workflows)\n")
    matched = set()
    for num, f, rw in repo_workflows(only):
        lw = match(rw, live)
        if not lw:
            state = "only in repo      -> push to add it"
        else:
            matched.add(lw["id"])
            same = comparable(rw) == comparable(lw)
            state = "in sync" if same else "differs           -> push (repo wins) or pull (n8n wins)"
            if not same and verbose:
                state += "".join("\n        " + d for d in differences(rw, lw))
            if not rw.get("id"):
                state += "  [repo file has no id yet; push or pull records it]"
        print(f"  P{num:<2} {rw['name'][:44]:44} {state}")
    if not only:
        for lw in live:
            if lw["id"] not in matched:
                print(f"  --  {lw['name'][:44]:44} only in n8n (not tracked by the repo)")


def push(only):
    live = live_workflows()
    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staged = []
    for num, f, rw in repo_workflows(only):
        lw = match(rw, live)
        w = json.loads(json.dumps(rw))
        if lw:
            if comparable(rw) == comparable(lw):
                if not rw.get("id"):            # record the id so later pushes match exactly
                    f.write_text(json.dumps({"id": lw["id"], **rw}, indent=2) + "\n", encoding="utf-8", newline="\n")
                    print(f"  P{num} in sync, id {lw['id']} recorded in {f.relative_to(ROOT)}")
                else:
                    print(f"  P{num} in sync, skipped")
                continue
            (BACKUP_DIR / f"{stamp}-P{num}-{lw['id']}.json").write_text(json.dumps(lw, indent=2), encoding="utf-8")
            w["id"] = lw["id"]
            with_credentials_from(w, lw)                # keep the credentials connected in n8n
            w["active"] = lw.get("active", False)
            w["settings"] = {**(w.get("settings") or {}), **{k: v for k, v in (lw.get("settings") or {}).items()
                                                             if k == "errorWorkflow"}}
        w.pop("pinData", None)
        (SYNC_DIR / f"P{num}.json").write_text(json.dumps(w, indent=2), encoding="utf-8")
        staged.append((num, f, rw, w))
    if not staged:
        print("Nothing to push.")
        return
    cli("import:workflow", "--separate", "--input=/data/sync")
    for p in SYNC_DIR.glob("P*.json"):
        p.unlink()
    # record ids for files that did not have one, so the next push updates in place
    after = {lw["name"]: lw["id"] for lw in live_workflows()}
    for num, f, rw, w in staged:
        if not rw.get("id"):
            rw = {"id": w.get("id") or after.get(rw["name"]), **rw}
            f.write_text(json.dumps(rw, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"  P{num} pushed -> {w.get('id') or after.get(rw['name'])}")
    print(f"\nPrevious n8n versions backed up to {BACKUP_DIR.relative_to(ROOT)}. If a workflow was active, "
          "restart n8n (docker compose restart n8n) for the new version's triggers to take effect.")


def pull(only):
    live = live_workflows()
    pulled = 0
    for num, f, rw in repo_workflows(only):
        lw = match(rw, live)
        if not lw:
            print(f"  P{num} not in n8n, skipped")
            continue
        if comparable(rw) == comparable(lw) and rw.get("id") == lw["id"]:
            print(f"  P{num} in sync, skipped")
            continue
        w = {k: v for k, v in lw.items() if k not in VOLATILE}
        w["id"] = lw["id"]
        with_credentials_from(w, rw)                    # keep the repo's placeholder credentials
        for n in w["nodes"]:                            # never write a real credential for a node the repo has none for
            if n.get("credentials") and not any(r["name"] == n["name"] and r.get("credentials") for r in rw["nodes"]):
                n["credentials"] = {k: {"id": "set_in_n8n", "name": f"{k} (demo)"} for k in n["credentials"]}
        w["settings"] = {k: v for k, v in (w.get("settings") or {}).items() if k != "errorWorkflow"}
        w["active"] = False
        f.write_text(json.dumps(w, indent=2) + "\n", encoding="utf-8", newline="\n")
        pulled += 1
        print(f"  P{num} pulled into {f.relative_to(ROOT)}")
    if pulled:
        print("\nLinting the pulled workflows ...")
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "lint_workflows.py")])
        print("Review with `git diff`, then commit." if r.returncode == 0 else
              "The linter found problems: fix them in n8n and pull again, or fix the JSON and push.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["status", "push", "pull"])
    ap.add_argument("--only", nargs="*", default=[], help="project numbers, e.g. --only 1 3")
    a = ap.parse_args()
    {"status": status, "push": push, "pull": pull}[a.command](set(a.only))


if __name__ == "__main__":
    main()
