"""AL-09: the chart validator on a fixed set of traps, offline, no model.

Each case is a chart and its data (cases/charts.jsonl): a share of a distinct count, a measure
that never moves, a pie of negative values, months asked by quarter, two series that are one, a
crowd of categories, a time axis out of order, a stacked total of a ratio; charts drawn with a note
(a hidden axis, a series that says no unit, a crowded pie); and charts that are right. The REAL
validator (worker-agent answer/chart_check.py, with the agent's own time order) reads each, and two
numbers come out:

- chart_trap_catch_rate: the traps it caught with the code the case expects, a `block` blocked, a
  `warn` drawn with its note (ANALYTICS_SPEC §E: 100 %);
- chart_false_block_rate: the right charts it blocked (0).
"""

from __future__ import annotations

from typing import Any

from model.eval import _paths

_paths.ensure()

from services.worker_agent.agentic.blocks import time_order  # noqa: E402 - after _paths.ensure()
from services.worker_agent.answer import chart_check  # noqa: E402


def _chart(spec: dict[str, Any]) -> chart_check.Chart:
    return chart_check.Chart(
        chart_type=spec["chart_type"],
        x_key="x",
        x_label=spec["x_label"],
        x_kind=spec["x_kind"],
        x_values=spec["x_values"],
        series=[
            chart_check.Series(
                key=s["label"],
                label=s["label"],
                values=[None if v is None else float(v) for v in s["values"]],
                additivity=s.get("additivity"),
                unit=s.get("unit"),
            )
            for s in spec["series"]
        ],
        stack=spec.get("stack"),
        axis_labels_visible=spec.get("axis_labels_visible", True),
        requested_grain=spec.get("requested_grain"),
    )


def score(cases: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for case in cases:
        issues = chart_check.check(_chart(case["chart"]), time_order=time_order)
        blocked = {i.code for i in issues if i.severity == "block"}
        warned = {i.code for i in issues if i.severity == "warn"}
        expect, code = case["expect"], case.get("code")
        if expect == "block":
            passed = code in blocked
        elif expect == "warn":
            passed = not blocked and code in warned
        else:
            passed = not blocked
        results.append(
            {
                "id": case["id"],
                "tag": case["tag"],
                "passed": passed,
                "detail": f"expect {expect} {code or ''}; blocked {sorted(blocked)} warned {sorted(warned)}",
            }
        )
    traps = [r for r, c in zip(results, cases, strict=True) if c["expect"] != "pass"]
    right = [r for r, c in zip(results, cases, strict=True) if c["expect"] == "pass"]
    return {
        "cases": len(cases),
        "results": results,
        "metrics": {
            "chart_trap_catch_rate": sum(r["passed"] for r in traps) / (len(traps) or 1),
            "chart_false_block_rate": sum(not r["passed"] for r in right) / (len(right) or 1),
        },
    }
