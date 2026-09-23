"""A deliberately tiny YAML reader, for the test suite only.

PyYAML is not a dependency of this repo and the alert file is generated from
``obs/alerts.py``, so the only thing a parser is needed for is *verifying* that
the generated file is well-formed and says what the source says. This handles
the subset the generator emits -- nested maps, a list of maps, double-quoted
scalars and ``|-`` block scalars -- and raises on anything else rather than
guessing.

If the rule file ever needs anchors, flow sequences or multi-document files,
this should be replaced by PyYAML rather than extended. A half-correct YAML
parser is worse than an honest dependency.
"""

from __future__ import annotations


def parse(text: str):
    lines = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        lines.append(raw)
    value, index = _parse_block(lines, 0, _indent(lines[0]) if lines else 0)
    if index != len(lines):
        raise ValueError("unconsumed input at line " + str(index))
    return value


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_block(lines, index, indent):
    if index >= len(lines):
        return None, index
    if lines[index].lstrip().startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_map(lines, index, indent)


def _parse_list(lines, index, indent):
    items = []
    while index < len(lines):
        line = lines[index]
        if _indent(line) != indent or not line.lstrip().startswith("- "):
            break
        rest = line.lstrip()[2:]
        # "- alert: X" starts a map whose first key sits at indent + 2.
        inner = [(" " * (indent + 2)) + rest]
        index += 1
        while index < len(lines) and _indent(lines[index]) > indent:
            inner.append(lines[index])
            index += 1
        value, consumed = _parse_block(inner, 0, indent + 2)
        if consumed != len(inner):
            raise ValueError("could not fully parse list item near: " + rest[:40])
        items.append(value)
    return items, index


def _parse_map(lines, index, indent):
    result = {}
    while index < len(lines):
        line = lines[index]
        if _indent(line) < indent:
            break
        if _indent(line) > indent:
            raise ValueError("unexpected indent at: " + line[:60])
        if line.lstrip().startswith("- "):
            break

        key, _, rest = line.strip().partition(":")
        rest = rest.strip()
        index += 1

        if rest in ("|-", "|", ">-"):
            block = []
            while index < len(lines) and _indent(lines[index]) > indent:
                block.append(lines[index][indent + 2 :])
                index += 1
            result[key] = "\n".join(block)
        elif rest == "":
            child_lines = []
            while index < len(lines) and _indent(lines[index]) > indent:
                child_lines.append(lines[index])
                index += 1
            if child_lines:
                value, consumed = _parse_block(child_lines, 0, _indent(child_lines[0]))
                if consumed != len(child_lines):
                    raise ValueError("could not fully parse value for key " + key)
                result[key] = value
            else:
                result[key] = None
        else:
            result[key] = _scalar(rest)
    return result, index


def _scalar(text: str):
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if text in ("true", "false"):
        return text == "true"
    try:
        return int(text)
    except ValueError:
        return text
