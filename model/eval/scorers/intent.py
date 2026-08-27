"""Intent accuracy — online tier, needs a model.

The classifier under test is the real planner node, not a prompt copied into
this file. A copy would drift from the one that ships, and then the number
reported here would be about something nobody runs.

`classify` is injected so the metric arithmetic is testable without a model, and
so an operator can point the same corpus at a different endpoint (a candidate
model, a staging LiteLLM) without editing the scorer.

Two things are scored per case, and only what the case states is checked:

  * `analysis_type` — the closed taxonomy in nodes/model_harness/intent.py.
  * `intent` — question vs task.

They are orthogonal. A case that pins one and leaves the other unstated is not
an incomplete case; it is one that has an opinion about one axis only ("vẽ biểu
đồ so sánh 2 quý" is a task AND a comparison, but "doanh thu tháng 3" is a
question whose task-ness nobody should be graded on).

`unsupported_recall` is reported separately because it is the case that costs
something real: FORECAST and ANOMALY are what this platform cannot do, and a
misclassification there is what lets an invented number reach an answer. It is
low-frequency, so it disappears into an overall accuracy that a corpus of easy
lookups can carry.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from model.eval import _paths

# The analysis types the platform has no implementation for. Kept in sync with
# nodes/model_harness/intent.py's capability table by importing it below when
# the workflow-runtime tree is importable; the literal is the fallback for a
# scorer running from the ragas image, which does not carry that service.
UNSUPPORTED = ("forecast", "anomaly")

Classifier = Callable[[str], Awaitable[dict[str, Any]]]


async def planner_classifier(question: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Classify one question with the REAL planner node.

    Requires LiteLLM reachable (LITELLM_BASE_URL). Everything the planner reads
    beyond `query` and `config` is optional state it defaults, so a bare state is
    a legitimate call rather than a special evaluation path through it.
    """
    _paths.ensure(_paths.WORKFLOW_RUNTIME)
    from nodes.model_harness.planner import planner_node

    result = await planner_node({"query": question, "config": config or {}})  # type: ignore[arg-type]
    return {
        "intent": result.get("intent", ""),
        "analysis_type": result.get("analysis_type", ""),
        "clarifications": result.get("clarifications", []),
    }


async def score(cases: list[dict[str, Any]], classify: Classifier) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    correct = 0
    unsupported_total = unsupported_hit = 0

    for case in cases:
        expect = case["expect"]
        try:
            actual = await classify(case["question"])
            error = ""
        except Exception as e:  # noqa: BLE001 - a classifier that fell over is a wrong answer, not a skip
            actual = {}
            error = f"{type(e).__name__}: {e}"

        checked = {k: v for k, v in expect.items() if k in ("intent", "analysis_type")}
        passed = not error and all(actual.get(k) == v for k, v in checked.items())
        correct += 1 if passed else 0

        if expect.get("analysis_type") in UNSUPPORTED:
            unsupported_total += 1
            unsupported_hit += 1 if actual.get("analysis_type") == expect["analysis_type"] else 0

        results.append({
            "id": case["id"],
            "tag": case.get("tag", ""),
            "question": case["question"],
            "expected": checked,
            "actual": {k: actual.get(k, "") for k in checked},
            "passed": passed,
            "error": error,
        })

    total = len(cases)
    return {
        "metrics": {
            "intent_accuracy": (correct / total) if total else 0.0,
            "unsupported_recall": (unsupported_hit / unsupported_total) if unsupported_total else 1.0,
        },
        "counts": {
            "total": total,
            "correct": correct,
            "unsupported_total": unsupported_total,
            "unsupported_hit": unsupported_hit,
        },
        "results": results,
    }
