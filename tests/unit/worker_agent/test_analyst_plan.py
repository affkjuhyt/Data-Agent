from __future__ import annotations

import datetime

import pytest

from services.worker_agent.analyst_plan import build_analysis_plan

pytestmark = pytest.mark.unit


def test_build_analysis_plan_is_not_metric_first() -> None:
    plan = build_analysis_plan(
        goal="Phân tích doanh số theo khu vực",
        metric_version_id="metric-v1",
        metric_name="Doanh số",
        group_by=["region"],
        filters=[],
        period=(datetime.date(2025, 1, 1), datetime.date(2025, 3, 31)),
        top_n=None,
        top_desc=None,
        grain="month",
    )

    assert [step["id"] for step in plan["steps"]] == [
        "understand",
        "resolve_scope",
        "query",
        "validate",
        "visualize",
        "narrate",
    ]
    assert plan["steps"][2]["type"] == "query_data"
    assert plan["outputs"][0] == {"step": "narrate", "as": "answer_blocks"}
