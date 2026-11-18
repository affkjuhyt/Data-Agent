"""Two runs of the agent eval side by side (evaluate.py --json): per eval set and engine, how many
passed in each, the change, the seconds and tokens an item took, and which questions passed in
one run and not the other. Standard library only, so it runs anywhere a Python 3 does:
    make agent-eval-compare BEFORE=4b AFTER=14b
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import Any


def _load(path: str) -> dict[tuple[str, str], dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        runs = json.load(f)
    return {(s.get("eval_set", ""), s["engine"]): s for s in runs}


def _pct(passed: int, items: int) -> str:
    return f"{100 * passed / items:.1f}%" if items else "-"


def compare(
    before: dict[tuple[str, str], dict[str, Any]], after: dict[tuple[str, str], dict[str, Any]]
) -> str:
    rows = [
        "| set | engine | items | before | after | change | s/item | tokens/item |",
        "|---|---|---|---|---|---|---|---|",
    ]
    totals = [0, 0, 0, 0]  # items before, passed before, items after, passed after
    changed: list[str] = []
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        name, engine = key
        if b and a:
            delta = a["passed"] - b["passed"]
            rows.append(
                f"| {name} | {engine} | {a['items']} | {b['passed']} ({_pct(b['passed'], b['items'])})"
                f" | {a['passed']} ({_pct(a['passed'], a['items'])}) | {delta:+d}"
                f" | {b['mean_seconds']} → {a['mean_seconds']}"
                f" | {b['mean_tokens']} → {a['mean_tokens']} |"
            )
            was = {o["question"]: o["passed"] for o in b["outcomes"]}
            now = {o["question"]: o for o in a["outcomes"]}
            gained = [q for q, o in now.items() if o["passed"] and was.get(q) is False]
            lost = [q for q, o in now.items() if not o["passed"] and was.get(q) is True]
            if gained or lost:
                changed.append(f"\n{name} · {engine}")
                changed += [f"  + {q}" for q in gained]
                changed += [f"  - {q}  → {now[q]['answer'][:100]}" for q in lost]
        else:
            # A set only one run read: its count, nothing to compare it with.
            run = a or b
            assert run is not None
            rows.append(
                f"| {name} | {engine} | {run['items']} | {b['passed'] if b else '-'}"
                f" | {a['passed'] if a else '-'} | | | |"
            )
        if b and a:
            # The total counts the sets both runs read, so a set added since is no change.
            totals[0] += b["items"]
            totals[1] += b["passed"]
            totals[2] += a["items"]
            totals[3] += a["passed"]
    rows.append(
        f"| both runs | | {totals[2]} | {totals[1]}/{totals[0]} ({_pct(totals[1], totals[0])})"
        f" | {totals[3]}/{totals[2]} ({_pct(totals[3], totals[2])}) | {totals[3] - totals[1]:+d}"
        " | | |"
    )
    return "\n".join(
        rows + (["\nPassed only after (+) or only before (-):", *changed] if changed else [])
    )


def main(argv: Sequence[str]) -> int:
    if len(argv) != 2:
        print("usage: eval_compare.py BEFORE.json AFTER.json", file=sys.stderr)
        return 2
    print(compare(_load(argv[0]), _load(argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
