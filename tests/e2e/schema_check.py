"""Check every workflow.json against the node schemas exported by a real n8n.

    n8n export:nodes --output=nodes.json
    py tests/e2e/schema_check.py nodes.json

For each node it verifies that
  * the node type exists at that typeVersion,
  * every parameter name (and every key inside collections) exists in that version's schema,
  * fixed enum values (`options` properties) are ones the schema allows.

Expressions (values starting with "=") are not checked: n8n only resolves them at run time.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IGNORED_PARAMS = {"notice"}


def versions(desc):
    v = desc.get("version", 1)
    return set(v) if isinstance(v, list) else {v}


def index(nodes):
    by_type = {}
    for d in nodes:
        by_type.setdefault(d["name"], []).append(d)
    return by_type


def props_named(props, name):
    return [p for p in props if p.get("name") == name]


def check_value(prop_list, value, path, problems):
    """Validate one parameter value against every schema property sharing its name."""
    if isinstance(value, str) and value.startswith("="):
        return
    types = {p.get("type") for p in prop_list}
    if "options" in types and not isinstance(value, (dict, list)):
        allowed = {o.get("value") for p in prop_list if p.get("type") == "options" for o in p.get("options", [])}
        if allowed and value not in allowed:
            problems.append(f"{path} = {value!r} not in {sorted(map(str, allowed))[:12]}")
    if types & {"collection", "fixedCollection"} and isinstance(value, dict):
        children = [o for p in prop_list if p.get("type") in ("collection", "fixedCollection")
                    for o in p.get("options", [])]
        for key, sub in value.items():
            match = props_named(children, key)
            if not match:
                # fixedCollection groups hold a list of their child values
                problems.append(f"{path}.{key} is not a known option")
                continue
            groups = [m for m in match if "values" in m]
            if groups and isinstance(sub, (list, dict)):
                items = sub if isinstance(sub, list) else [sub]
                inner = [v for g in groups for v in g["values"]]
                for item in items:
                    if isinstance(item, dict):
                        for k2, v2 in item.items():
                            m2 = props_named(inner, k2)
                            if not m2:
                                problems.append(f"{path}.{key}.{k2} is not a known field")
                            else:
                                check_value(m2, v2, f"{path}.{key}.{k2}", problems)
            else:
                check_value(match, sub, f"{path}.{key}", problems)


def check(workflow_path, by_type):
    w = json.loads(workflow_path.read_text(encoding="utf-8"))
    problems = []
    for n in w["nodes"]:
        descs = [d for d in by_type.get(n["type"], []) if n["typeVersion"] in versions(d)]
        where = f"{n['name']!r} ({n['type'].split('.')[-1]} v{n['typeVersion']})"
        if not descs:
            known = sorted({v for d in by_type.get(n["type"], []) for v in versions(d)})
            problems.append(f"{where}: no such node version (known: {known})")
            continue
        props = [p for d in descs for p in d["properties"]]
        for key, value in n["parameters"].items():
            if key in IGNORED_PARAMS:
                continue
            match = props_named(props, key)
            if not match:
                problems.append(f"{where}: unknown parameter {key!r}")
                continue
            check_value(match, value, f"{where}: {key}", problems)
    return problems


def main():
    by_type = index(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
    total = 0
    for wf in sorted(ROOT.glob("project-*/workflow.json")):
        problems = check(wf, by_type)
        total += len(problems)
        print(f"{wf.parent.name:45} {'ok' if not problems else str(len(problems)) + ' problem(s)'}")
        for p in problems:
            print("    -", p)
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()
