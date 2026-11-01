"""Build the analyst-facing plan for a semantic data question."""

from __future__ import annotations

from datetime import date
from typing import Any

from libs.contracts.v1 import AnalysisPlan


def build_analysis_plan(
    *,
    goal: str,
    metric_version_id: str,
    metric_name: str,
    group_by: list[str],
    filters: list[dict[str, Any]],
    period: tuple[date, date] | None,
    top_n: int | None,
    top_desc: bool | None,
    compare_to: str | None = None,
    grain: str | None = None,
    analysis: dict[str, Any] | None = None,
    explain_by: str | None = None,
) -> dict[str, Any]:
    """Return a validated DAG whose outputs describe the answer, not just the query."""
    query_args: dict[str, Any] = {
        "metric_version_id": metric_version_id,
        "group_by": group_by,
        "filters": filters,
        "period": [period[0].isoformat(), period[1].isoformat()] if period else None,
        "top_n": top_n,
        "top_desc": top_desc,
    }
    if compare_to is not None:
        query_args["compare_to"] = compare_to
    if grain is not None:
        query_args["grain"] = grain
    if analysis is not None:
        query_args["analysis"] = analysis

    shape = "scalar" if not group_by else "breakdown"
    visual = "metric" if shape == "scalar" else ("line" if grain else "bar"
    )
    steps: list[dict[str, Any]] = [
        {
            "id": "understand",
            "type": "understand_intent",
            "args": {"metric": metric_name, "goal": goal},
            "output": "intent",
            "checkpoint": "none",
        },
        {
            "id": "resolve_scope",
            "type": "resolve_data_scope",
            "depends_on": ["understand"],
            "args": {"group_by": group_by, "filters": filters, "period": query_args["period"]},
            "output": "data_contract",
            "checkpoint": "none",
        },
        {
            "id": "query",
            "type": "query_data",
            "tool": "query.semantic",
            "depends_on": ["resolve_scope"],
            "args": query_args,
            "output": "result",
            "checkpoint": "none",
        },
        {
            "id": "validate",
            "type": "validate_result",
            "depends_on": ["query"],
            "args": {"metric": metric_name, "expected_shape": shape},
            "output": "validated_result",
            "checkpoint": "none",
        },
        {
            "id": "visualize",
            "type": "select_visualization",
            "depends_on": ["validate"],
            "args": {"shape": shape, "preferred": visual},
            "output": "visualization",
            "checkpoint": "none",
        },
        {
            "id": "narrate",
            "type": "compose_answer",
            "depends_on": ["validate", "visualize"],
            "args": {"metric": metric_name, "explain_by": explain_by},
            "output": "answer_blocks",
            "checkpoint": "none",
        },
    ]
    plan = AnalysisPlan.model_validate(
        {
            "version": 1,
            "goal": goal,
            "steps": steps,
            "outputs": [
                {"step": "narrate", "as": "answer_blocks"},
                {"step": "visualize", "as": "chart"},
                {"step": "query", "as": "table"},
            ],
            "budget": {"max_steps": len(steps), "max_duration": 120},
        }
    )
    return plan.model_dump(mode="json", by_alias=True)
