"""Ask streaming contract (OP-4): commentary / interrupt / final channels."""

from __future__ import annotations

import pytest

from services.worker_agent.agentic import stream_contract as sc

pytestmark = pytest.mark.unit


def test_channels_partition_known_events() -> None:
    assert sc.channel("planning") == "commentary"
    assert sc.channel("progress") == "commentary"
    assert sc.channel("block") == "commentary"
    assert sc.channel("clarification") == "interrupt"
    assert sc.channel("answer") == "final"
    assert sc.channel("done") == "final"
    assert sc.channel("mystery") is None


def test_progress_message_never_names_a_tool() -> None:
    line = sc.progress_message(phase="plan", goal="So sánh doanh thu Internet 2013")
    assert "query_metric" not in line
    assert "Kế hoạch:" in line
    assert "Internet" in line
    assert "Đang lập" in sc.progress_message(phase="planning")


def test_final_payload_is_self_contained() -> None:
    body = sc.final_payload(
        question_id="q1",
        answer_id="a1",
        kind="data",
        status="answered",
        headline="Doanh thu 2013",
        text="{{F1}} USD",
        blocks=[{"type": "kpi", "id": "b1", "composed": True}],
        trust_summary="Có quản trị",
    )
    assert body["answer_id"] == "a1"
    assert body["headline"] == "Doanh thu 2013"
    assert body["blocks"][0]["type"] == "kpi"
    assert body["trust_summary"] == "Có quản trị"
