"""AL-15: what the agent's tools, its Python and its causal rule did is counted for Prometheus:
`dataagent_agent_tool_runs_total`, `dataagent_compute_runs_total`,
`dataagent_causal_sentences_dropped_total` (with the chart checker's
`dataagent_chart_check_issues_total`, AL-03)."""

from __future__ import annotations

from typing import Any

import pytest
from prometheus_client import REGISTRY

from services.worker_agent.agentic import engine
from services.worker_agent.agentic.tools.analyze import Analyze, AnalyzeArgs
from services.worker_agent.agentic.tools.base import Tool, ToolContext, ToolResult

from .test_sandbox_semantic import _ctx

pytestmark = [pytest.mark.unit, pytest.mark.req("AL-15")]


def _value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_every_outcome_of_the_analysis_code_is_counted_by_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs = "dataagent_compute_runs_total"
    monkeypatch.setenv("AGENT_PYTHON_SANDBOX", "local")
    ctx = _ctx()
    before = {o: _value(runs, mode="local", outcome=o) for o in ("refused", "ok", "error")}
    seconds = _value("dataagent_compute_seconds_count", mode="local")
    await Analyze().run(ctx, AnalyzeArgs(code="result = d1['Tỷ lệ'].sum()", inputs=["d1"]))
    await Analyze().run(
        ctx,
        AnalyzeArgs(
            code="result = d2.groupby('Vùng', as_index=False)['Doanh thu'].sum()", inputs=["d2"]
        ),
    )
    await Analyze().run(ctx, AnalyzeArgs(code="result = 1 / 0", inputs=["d2"]))
    assert {o: _value(runs, mode="local", outcome=o) - before[o] for o in before} == {
        "refused": 1,
        "ok": 1,
        "error": 1,
    }
    # The refused code never reached the sandbox: two runs were timed.
    assert _value("dataagent_compute_seconds_count", mode="local") - seconds == 2

    monkeypatch.setenv("AGENT_PYTHON_SANDBOX", "off")
    off = _value(runs, mode="off", outcome="off")
    await Analyze().run(ctx, AnalyzeArgs(code="result = d2", inputs=["d2"]))
    assert _value(runs, mode="off", outcome="off") - off == 1


class _Flaky(Tool):
    name = "insights"
    title = "Đọc số"
    timeout_seconds = 5.0

    def __init__(self, result: ToolResult | None) -> None:
        self.result = result

    async def run(self, ctx: ToolContext, args: Any) -> ToolResult:  # type: ignore[override]
        if self.result is None:
            raise RuntimeError("boom")
        return self.result


async def test_each_tool_run_is_counted_by_tool_and_outcome() -> None:
    runs = "dataagent_agent_tool_runs_total"
    state = engine.State.__new__(engine.State)
    state.question_id = None  # type: ignore[assignment]
    before = {o: _value(runs, tool="insights", outcome=o) for o in ("ok", "arguments", "logic")}
    timed = _value("dataagent_agent_tool_seconds_count", tool="insights")
    ctx = _ctx()
    await engine._run_tool(state, _Flaky(ToolResult(True, "xong")), ctx, None)
    await engine._run_tool(state, _Flaky(ToolResult(False, "sai", failure="arguments")), ctx, None)
    # A tool that crashes is the planner's feedback, counted as a logic failure.
    await engine._run_tool(state, _Flaky(None), ctx, None)
    assert {o: _value(runs, tool="insights", outcome=o) - before[o] for o in before} == {
        "ok": 1,
        "arguments": 1,
        "logic": 1,
    }
    assert _value("dataagent_agent_tool_seconds_count", tool="insights") - timed == 3


def test_a_dropped_cause_is_counted() -> None:
    state = engine.State.__new__(engine.State)
    state.dropped_causes = 0
    before = _value("dataagent_causal_sentences_dropped_total")
    kept = engine._no_cause(
        state, "Doanh thu giảm 10%. Điều này dẫn đến lợi nhuận thấp. Miền Bắc chiếm 60%."
    )
    assert "dẫn đến" not in kept and state.dropped_causes == 1
    assert _value("dataagent_causal_sentences_dropped_total") - before == 1
