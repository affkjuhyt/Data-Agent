"""Clarification wiring — that asking actually replaces answering.

The rules are covered in test_ambiguity_rules.py. What matters here is the
consequence: when a structural gap is found the planner must drop its tool
calls, so nothing runs and no number is produced for a question whose scope is
the thing in doubt. A clarification that still lets the query execute is worse
than none — the user gets both a question and a confident wrong answer.

Also pinned: the loop guard. A question the rules cannot fully resolve must ask
once, not forever.
"""
from __future__ import annotations

import pathlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path(monkeypatch):
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    fake_litellm = types.ModuleType("litellm")
    fake_litellm.acompletion = AsyncMock()
    fake_litellm.success_callback = []
    fake_litellm.failure_callback = []
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _planner():
    import nodes.model_harness.planner as planner
    return planner


def _llm(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    return resp


_PLAN = (
    '{"intent":"question","analysis_type":"analytical","language":"vi",'
    '"slots":{"metric":"doanh thu"},'
    '"tool_calls":[{"type":"db_query","query":"doanh thu","reason":"r","confidence":0.9}]}'
)


def _stub_check(monkeypatch, questions):
    """Replace the semantic_layer module the planner imports lazily."""
    import types
    mod = types.ModuleType("semantic_layer")
    mod.check_underspecified = AsyncMock(return_value=questions)
    # The planner imports it by its package path; with the full service
    # importable, only this key reaches it (the short one alone left the real
    # checker running and these tests failing).
    monkeypatch.setitem(sys.modules, "services.workflow_runtime.semantic_layer", mod)
    return mod


async def _run(monkeypatch, state_extra=None, questions=None):
    planner = _planner()
    monkeypatch.setattr(planner.litellm, "acompletion", AsyncMock(return_value=_llm(_PLAN)))
    _stub_check(monkeypatch, questions if questions is not None else [])

    state = {
        "query": "doanh thu bao nhiêu",
        "config": {"tenant_id": "t-1"},
        **(state_extra or {}),
    }
    return await planner.planner_node(state)


CLARIFICATION = [{
    "reason": "missing_time_range",
    "text": "Bạn muốn xem trong khoảng thời gian nào?",
    "options": ["this_month", "last_month"],
}]


class TestAskingReplacesAnswering:
    async def test_a_clarification_drops_the_tool_calls(self, monkeypatch):
        """The whole point. Executing a query whose scope is the thing in doubt
        produces a confident number for a question nobody asked."""
        result = await _run(monkeypatch, questions=CLARIFICATION)

        assert result["clarifications"] == CLARIFICATION
        assert result["tool_calls"] == []

    async def test_no_clarification_keeps_the_tool_calls(self, monkeypatch):
        result = await _run(monkeypatch, questions=[])

        assert result["clarifications"] == []
        assert result["tool_calls"]

    async def test_slots_survive_a_clarification_turn(self, monkeypatch):
        """The user's answer is the next turn's delta, so what they already said
        has to still be there to merge into."""
        result = await _run(monkeypatch, questions=CLARIFICATION)

        assert result["slots"]["metric"] == "doanh thu"


class TestLoopGuard:
    async def test_a_reason_asked_last_turn_becomes_an_assumption(self, monkeypatch):
        """Turn N asks; turn N+1 is the answer. Asking the same thing again is
        what made the card feel endless — the default is stated instead."""
        planner = _planner()
        monkeypatch.setattr(planner.litellm, "acompletion", AsyncMock(return_value=_llm(_PLAN)))
        _stub_check(monkeypatch, CLARIFICATION)

        result = await planner.planner_node({
            "query": "tháng 6",
            "config": {"tenant_id": "t-1"},
            "previous_clarification": True,
            "previous_clarified_reasons": ["missing_time_range"],
        })

        assert result["clarifications"] == []
        assert [a["reason"] for a in result["assumptions"]] == ["missing_time_range"]
        assert result["tool_calls"]

    async def test_a_gap_not_asked_about_yet_is_still_asked(self, monkeypatch):
        """The guard is per reason: answering the period must not wave through
        a metric ambiguity nobody has asked about."""
        metric_q = {"reason": "ambiguous_metric", "text": "Chỉ số nào?", "options": ["Doanh thu gộp", "Doanh thu thuần"]}
        planner = _planner()
        monkeypatch.setattr(planner.litellm, "acompletion", AsyncMock(return_value=_llm(_PLAN)))
        _stub_check(monkeypatch, [metric_q, *CLARIFICATION])

        result = await planner.planner_node({
            "query": "tháng 6",
            "config": {"tenant_id": "t-1"},
            "previous_clarification": True,
            "previous_clarified_reasons": ["missing_time_range"],
        })

        assert result["clarifications"] == [metric_q]
        assert [a["reason"] for a in result["assumptions"]] == ["missing_time_range"]
        assert result["tool_calls"] == []
        assert result["clarified_reasons"] == ["ambiguous_metric", "missing_time_range"]

    async def test_a_new_metric_starts_a_new_line_of_questioning(self, monkeypatch):
        result = await _run(monkeypatch, questions=CLARIFICATION, state_extra={
            "previous_slots": {"metric": "số khách hàng"},
            "previous_clarified_reasons": ["missing_time_range"],
        })

        assert result["clarifications"] == CLARIFICATION
        assert result["assumptions"] == []

    async def test_a_marker_without_reasons_still_means_asked(self, monkeypatch):
        """Threads marked before reasons were recorded must not be asked again."""
        result = await _run(monkeypatch, questions=CLARIFICATION, state_extra={"previous_clarification": True})

        assert result["clarifications"] == []
        assert result["tool_calls"]

    async def test_the_marker_rides_with_the_slots_so_both_stores_agree(self):
        """Redis and the durable message copy must carry it identically, or a
        cold cache re-asks a question the user just answered."""
        # conversation_runner pulls the whole service graph; CI has those deps,
        # a bare dev env may not. The function under test is pure either way.
        pytest.importorskip("sqlalchemy", reason="workflow_runtime deps not installed")
        import runtime_services.conversation_runner as runner

        marked = runner._slots_with_marker({"metric": "x"}, CLARIFICATION, ["missing_time_range"])
        cleared = runner._slots_with_marker({"metric": "x"}, [])

        assert marked["_asked_clarification"] is True
        assert cleared["_asked_clarification"] is False
        assert marked["metric"] == "x"
        assert runner._previous_clarified_reasons(marked) == ["missing_time_range"]
        assert runner._previous_clarified_reasons({"_asked_clarification": True}) is None

    async def test_the_marker_is_inert_for_slot_parsing(self):
        """It travels inside the slots dict, so it must not become a slot."""
        from libs.semantic import AnalyticalSlots

        parsed = AnalyticalSlots.from_dict({"metric": "x", "_asked_clarification": True})

        assert parsed.metric == "x"
        assert not hasattr(parsed, "_asked_clarification")


class TestStructuredAnswers:
    async def test_a_picked_period_lands_on_the_slots(self, monkeypatch):
        result = await _run(monkeypatch, state_extra={
            "clarification_answers": [{"reason": "missing_time_range", "value": "last_month"}],
        })

        assert result["slots"]["relative_period"] == "last_month"
        assert result["slots"]["metric"] == "doanh thu"

    async def test_an_answered_reason_is_not_asked_again(self, monkeypatch):
        """Holds even when the thread that asked could not be continued, so
        `previous_clarification` is not set."""
        planner = _planner()
        monkeypatch.setattr(planner.litellm, "acompletion", AsyncMock(return_value=_llm(_PLAN)))
        _stub_check(monkeypatch, CLARIFICATION)

        result = await planner.planner_node({
            "query": "doanh thu bao nhiêu\n\nThông tin bổ sung: Last month",
            "config": {"tenant_id": "t-1"},
            "clarification_answers": [{"reason": "missing_time_range", "value": "last_month"}],
        })

        assert result["clarifications"] == []
        assert result["tool_calls"]
        assert result["clarified_reasons"] == ["missing_time_range"]


class TestFailureIsNotFatal:
    async def test_a_broken_checker_does_not_block_the_turn(self, monkeypatch):
        """A question that could have been clarified is a smaller failure than
        one that cannot be asked at all."""
        import types

        planner = _planner()
        monkeypatch.setattr(planner.litellm, "acompletion", AsyncMock(return_value=_llm(_PLAN)))
        mod = types.ModuleType("semantic_layer")
        mod.check_underspecified = AsyncMock(side_effect=RuntimeError("registry down"))
        monkeypatch.setitem(sys.modules, "services.workflow_runtime.semantic_layer", mod)

        result = await planner.planner_node({"query": "doanh thu", "config": {"tenant_id": "t-1"}})

        assert result["clarifications"] == []
        assert result["tool_calls"]

    async def test_no_tenant_skips_the_check_without_error(self, monkeypatch):
        planner = _planner()
        monkeypatch.setattr(planner.litellm, "acompletion", AsyncMock(return_value=_llm(_PLAN)))
        mod = _stub_check(monkeypatch, [])

        result = await planner.planner_node({"query": "doanh thu", "config": {}})

        assert result["clarifications"] == []
        mod.check_underspecified.assert_awaited_once()
        assert mod.check_underspecified.await_args.args[0] == ""


class TestResponderAsksInstead:
    def test_the_responder_reads_clarifications_and_forbids_guessing(self):
        """Pins the wiring: if the responder stops reading the field, the user
        gets a silent non-answer instead of a question."""
        import inspect

        import nodes.model_harness.responder as responder

        source = inspect.getsource(responder.responder_node)

        assert 'state.get("clarifications")' in source
        assert "HỎI LẠI" in source
        assert "không đoán" in source

    def test_the_responder_states_assumptions(self):
        """An applied default the answer does not mention reads exactly like
        what the user meant."""
        import inspect

        import nodes.model_harness.responder as responder

        source = inspect.getsource(responder.responder_node)

        assert 'state.get("assumptions")' in source
        assert "NÊU RÕ" in source
        assert '"warnings": list(assumptions)' in source


class TestAskingSkipsRetrieval:
    """Graph-level: a clarification must reach the responder WITHOUT any
    retrieval node running. The node-level tests above prove tool_calls are
    dropped; this pins that the graph does not then pick a retrieval node by
    fallback — which is exactly what happened on a planner→db_connector canvas
    in the live E2E (asked the question AND ran the query)."""

    def test_route_after_planner_goes_to_responder_when_asking(self):
        import graph

        route = graph._make_route_after_planner(frozenset({"db_connector", "kb_search"}), "responder")
        asking = {"clarifications": CLARIFICATION, "tool_calls": []}
        assert route(asking) == ["responder"]

        # no clarification → normal routing, including the no-tool_calls fallback
        assert route({"clarifications": [], "tool_calls": [{"type": "db_query"}]}) == ["db_connector"]
        assert set(route({"clarifications": [], "tool_calls": []})) <= {"db_connector", "kb_search"}

    def test_without_a_responder_the_old_fallback_is_kept(self):
        import graph

        route = graph._make_route_after_planner(frozenset({"kb_search"}), None)
        assert route({"clarifications": CLARIFICATION, "tool_calls": []}) == ["kb_search"]

    def test_judge_does_not_loop_a_clarification(self):
        import graph

        route = graph._make_route_after_judge(frozenset({"planner", "responder"}))
        assert route({"clarifications": CLARIFICATION, "judge_verdict": "FAIL", "judge_error_type": "PLANNING_ERROR"}) == "end"
        assert route({"clarifications": [], "judge_verdict": "FAIL", "judge_error_type": "PLANNING_ERROR"}) == "planner"

    def test_db_connector_canvas_compiles_with_the_ask_edge(self):
        """The E2E canvas: planner → db_connector → rrf → reranker → responder → judge."""
        import graph

        nodes = [{"id": n, "data": {"nodeType": n}} for n in
                 ("planner", "db_connector", "rrf_ranking", "reranker", "responder", "judge")]
        edges = [{"source": s, "target": t} for s, t in
                 (("planner", "db_connector"), ("db_connector", "rrf_ranking"), ("rrf_ranking", "reranker"),
                  ("reranker", "responder"), ("responder", "judge"))]
        compiled = graph.build_graph(nodes, edges)
        assert compiled is not None


class TestPlannerKeepsSlotsWhenConfidenceIsMissing:
    def test_tool_call_without_confidence_still_validates(self):
        import nodes.model_harness.planner as planner

        raw = (
            '{"intent":"task","analysis_type":"analytical",'
            '"slots":{"metric":"doanh thu thuần","dimensions":["vùng"],"time_range":["2026-08-01","2026-09-01"]},'
            '"language":"vi","tool_calls":[{"type":"db_query","query":"doanh thu thuần theo vùng"}]}'
        )
        parsed = planner._PlannerOutput.model_validate_json(raw)
        assert parsed.slots["metric"] == "doanh thu thuần"
        assert parsed.tool_calls[0].confidence == 0.5


class TestPlannerKnowsTheDate:
    """Relative periods need an anchor. Without today's date in the prompt the
    model guessed 2023 for "tháng này" in 2026 and the contract query matched
    nothing (live E2E)."""

    def test_date_line_has_today_and_the_month_start(self):
        from datetime import datetime

        import nodes.model_harness.planner as planner

        line = planner._date_context(datetime(2026, 8, 30, 23, 30))
        assert "30/08/2026" in line and "2026-08-30" in line
        assert "Chủ Nhật" in line
        assert '["2026-08-01"' in line
        assert "relative_period" in line

    def test_prompt_includes_the_date_line(self, monkeypatch):
        import nodes.model_harness.planner as planner

        captured = {}

        async def fake(**kw):
            captured["system"] = kw["messages"][0]["content"]
            return _llm(_PLAN)

        monkeypatch.setattr(planner.litellm, "acompletion", fake)
        _stub_check(monkeypatch, [])
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            planner.planner_node({"query": "doanh thu tháng này", "config": {"tenant_id": "t-1"}})
        )
        assert "Hôm nay là" in captured["system"]
