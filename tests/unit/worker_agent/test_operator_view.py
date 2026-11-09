"""Operator observability summary (OP-8)."""

from __future__ import annotations

import pytest

from services.worker_agent.agentic import operator_view as ov
from services.worker_agent.agentic import plan as p

pytestmark = pytest.mark.unit


def test_operator_view_names_failed_steps_and_replans() -> None:
    plan = p.Plan(
        goal="So sánh doanh thu",
        mode="analyze",
        steps=[
            p.PlanStep(id="s1", tool="query_metric", instruction="đọc", status="done"),
            p.PlanStep(id="s2", tool="analyze", instruction="so", status="failed"),
        ],
    )
    view = ov.build(
        plan=plan,
        replans=1,
        gate_retries=0,
        dataset_trusts={"d1": "certified"},
    )
    assert view["failed_step_ids"] == ["s2"]
    assert view["replans"] == 1
    assert view["trusts"] == ["certified"]
    assert "s2" in view["why"]
    assert view["dag"][0]["id"] == "s1"


def test_handover_why_mentions_gate_retries() -> None:
    view = ov.build(plan=None, handed_back=True, gate_retries=1)
    assert "chuyển sang đường trả lời chuẩn" in view["why"].lower() or "Đã chuyển" in view["why"]
    assert "1" in view["why"]
