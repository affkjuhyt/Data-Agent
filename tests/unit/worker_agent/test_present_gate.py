"""VERIFY before PRESENT (OP-5): empty data, wrong columns, out-of-time."""

from __future__ import annotations

import pytest

from services.worker_agent.agentic import blocks as b
from services.worker_agent.agentic import present_gate as gate
from services.worker_agent.agentic.workspace import Workspace

pytestmark = pytest.mark.unit


def _ws_with_sales() -> Workspace:
    ws = Workspace()
    ws.add(
        title="Doanh thu",
        columns=["Vùng", "Doanh thu"],
        rows=[["Europe", "100"], ["Pacific", "50"]],
        trust="certified",
        query_run_ids=["r1"],
        unit="USD",
    )
    return ws


def test_empty_analyze_workspace_gates() -> None:
    hit = gate.before_write(
        mode="analyze",
        ws=Workspace(),
        dropped_members=[],
        unnamed=[],
        out_of_time=[],
        plan_steps=[],
    )
    assert hit is not None
    assert "Chưa lấy được" in hit.target


def test_out_of_time_hands_over_without_replan_feedback() -> None:
    hit = gate.before_write(
        mode="analyze",
        ws=_ws_with_sales(),
        dropped_members=[],
        unnamed=[],
        out_of_time=["fact_sales"],
        plan_steps=[],
    )
    assert hit is not None
    assert hit.feedback == ""
    assert "fact_sales" in hit.target


def test_wrong_column_drafts_gate_after_zero_made() -> None:
    ws = _ws_with_sales()
    drafts = [
        b.BlockDraft(type="chart", dataset="d1", chart_type="bar", x="Năm", y=["Doanh thu"]),
        b.BlockDraft(type="markdown", content="xin chào"),
    ]
    assert gate.draft_column_errors(drafts, ws)
    hit = gate.after_drafts(mode="analyze", drafts=drafts, made=0, ws=ws)
    assert hit is not None
    assert "không khớp cột" in hit.target


def test_successful_materialize_skips_after_drafts_gate() -> None:
    ws = _ws_with_sales()
    drafts = [b.BlockDraft(type="table", dataset="d1")]
    hit = gate.after_drafts(mode="analyze", drafts=drafts, made=1, ws=ws)
    assert hit is None


def test_no_figure_when_all_zeros() -> None:
    ws = Workspace()
    ws.add(
        title="Trống",
        columns=["Kỳ", "Giá trị"],
        rows=[["2013", "0"], ["2014", "0"]],
        trust="certified",
        query_run_ids=["r1"],
    )
    assert gate.no_figure(ws) is True
    hit = gate.before_write(
        mode="analyze",
        ws=ws,
        dropped_members=[],
        unnamed=[],
        out_of_time=[],
    )
    assert hit is not None
    assert "Không có số liệu" in hit.target
