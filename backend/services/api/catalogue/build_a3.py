"""Build a3_contract.json from tab 4 A3 of the design doc (source/tab4.md, its markdown export).

A3 gives, per endpoint, the request body fields ("*" = required), the query parameters ("?name"),
what is returned and the endpoint's own errors. A2 (a2_endpoints.json) fixes which operations exist;
this file fixes their shape, so a route that renames or drops a field the spec names is caught in CI
(tests/unit/api/test_a3_contract.py), not by the frontend.

    python -m services.api.catalogue.build_a3        (from backend, rewrites a3_contract.json)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "source" / "tab4.md"
OUT = HERE / "a3_contract.json"

# A2 rows decided to be one operation (docs/v3/06-api-v1.md §1), and the body field that tells
# their variants apart. Their A3 bodies are merged: every field of any variant, required only
# where every variant requires it; the handler checks the rest per variant.
MERGED = {"POST /v1/data-products": "layer"}

ROW = re.compile(r"^\|\s*(GET|POST|PUT|PATCH|DELETE)\s+(/v1/\S+)\s*\|")


def _unescape(text: str) -> str:
    return (
        re.sub(r"\\([_*\[\]~|{}<>`#+\-.!()])", r"\1", text)
        .replace("&#91;", "[")
        .replace("&#93;", "]")
    )


def _cells(line: str) -> list[str]:
    """A markdown table row split on unescaped pipes ('\\|' stays inside its cell)."""
    parts, cur, i = [], "", 0
    body = line.strip().strip("|")
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body):
            cur += body[i : i + 2]
            i += 2
            continue
        if body[i] == "|":
            parts.append(cur)
            cur = ""
        else:
            cur += body[i]
        i += 1
    parts.append(cur)
    return [_unescape(p).strip() for p in parts]


def _split_top(text: str, seps: str) -> list[str]:
    out, cur, depth = [], "", 0
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch in seps and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [s.strip() for s in out if s.strip()]


def parse_request(cell: str) -> dict[str, object]:
    body: dict[str, bool] = {}
    query: list[str] = []
    if cell in ("—", "-", ""):
        return {"body": body, "query": query}
    for item in _split_top(cell, ",;"):
        # "record_ids hoặc filter (…)": alternatives, each optional.
        for alt in re.split(r"\s+hoặc\s+", item):
            m = re.match(r"^(\?)?([a-z][a-z0-9_]*)(\*)?", alt.strip())
            if not m:
                continue
            if m.group(1):
                query.append(m.group(2))
            else:
                body[m.group(2)] = bool(m.group(3)) and " hoặc " not in item
    return {"body": body, "query": query}


def parse_errors(cell: str) -> list[list[object]]:
    return [
        [int(m.group(1)), m.group(2)]
        for part in _split_top(cell, ";")
        if (m := re.match(r"^(\d{3})\s+([a-z_]+)", part))
    ]


def build(text: str) -> dict[str, dict[str, Any]]:
    endpoints: dict[str, dict[str, Any]] = {}
    group = None
    for line in text.splitlines():
        if line.startswith("### A3 · "):
            group = _unescape(line[len("### A3 · ") :]).strip()
            continue
        if line.startswith("## A4"):
            break
        if group is None:
            continue
        m = ROW.match(_unescape(line))
        if not m:
            continue
        cells = _cells(line)
        method, path = cells[0].split(None, 1)
        key = f"{method} {path}"
        row: dict[str, Any] = {
            "group": group,
            "groups": [group],
            **parse_request(cells[1]),
            "returns": cells[2],
            "errors": parse_errors(cells[3]) if len(cells) > 3 else [],
            "request_text": cells[1],
        }
        if key in endpoints:
            if key not in MERGED:
                raise ValueError(f"{key} appears twice in A3 and is not a decided merge")
            row = _merge(endpoints[key], row, MERGED[key])
        endpoints[key] = row
    return endpoints


def _merge(first: dict[str, Any], other: dict[str, Any], discriminator: str) -> dict[str, Any]:
    names = list(dict.fromkeys([*first["body"], *other["body"]]))
    body = {n: first["body"].get(n, False) and other["body"].get(n, False) for n in names}
    body[discriminator] = False
    return {
        **first,
        "groups": [*first["groups"], *other["groups"]],
        "body": body,
        "query": list(dict.fromkeys([*first["query"], *other["query"]])),
        "returns": f"{first['returns']} | {other['returns']}",
        "errors": first["errors"] + [e for e in other["errors"] if e not in first["errors"]],
        "request_text": f"{first['request_text']} | {other['request_text']}",
    }


def main() -> int:
    endpoints = build(SOURCE.read_text(encoding="utf-8"))
    OUT.write_text(
        json.dumps(
            {
                "source": "Data Agent V3 — Tài liệu phân tích thiết kế hệ thống, tab 4 · Đặc tả API, A3",
                "endpoints": endpoints,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{len(endpoints)} endpoints")
    return 0


if __name__ == "__main__":
    sys.exit(main())
