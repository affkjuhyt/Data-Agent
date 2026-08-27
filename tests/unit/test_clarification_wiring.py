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
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "workflow-runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
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
    monkeypatch.setitem(sys.modules, "semantic_layer", mod)
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
    async def test_the_check_is_skipped_right_after_asking(self, monkeypatch):
        """Turn N asks; turn N+1 is the answer. Re-running the rules there
        would ask the same thing forever."""
        planner = _planner()
        monkeypatch.setattr(planner.litellm, "acompletion", AsyncMock(return_value=_llm(_PLAN)))
        mod = _stub_check(monkeypatch, CLARIFICATION)

        result = await planner.planner_node({
            "query": "tháng 6",
            "config": {"tenant_id": "t-1"},
            "previous_clarification": True,
        })

        mod.check_underspecified.assert_not_awaited()
        assert result["clarifications"] == []
        assert result["tool_calls"]

    async def test_the_marker_rides_with_the_slots_so_both_stores_agree(self):
        """Redis and the durable message copy must carry it identically, or a
        cold cache re-asks a question the user just answered."""
        # conversation_runner pulls the whole service graph; CI has those deps,
        # a bare dev env may not. The function under test is pure either way.
        pytest.importorskip("sqlalchemy", reason="workflow-runtime deps not installed")
        import runtime_services.conversation_runner as runner

        marked = runner._slots_with_marker({"metric": "x"}, CLARIFICATION)
        cleared = runner._slots_with_marker({"metric": "x"}, [])

        assert marked["_asked_clarification"] is True
        assert cleared["_asked_clarification"] is False
        assert marked["metric"] == "x"

    async def test_the_marker_is_inert_for_slot_parsing(self):
        """It travels inside the slots dict, so it must not become a slot."""
        from libs.semantic import AnalyticalSlots

        parsed = AnalyticalSlots.from_dict({"metric": "x", "_asked_clarification": True})

        assert parsed.metric == "x"
        assert not hasattr(parsed, "_asked_clarification")


class TestFailureIsNotFatal:
    async def test_a_broken_checker_does_not_block_the_turn(self, monkeypatch):
        """A question that could have been clarified is a smaller failure than
        one that cannot be asked at all."""
        import types

        planner = _planner()
        monkeypatch.setattr(planner.litellm, "acompletion", AsyncMock(return_value=_llm(_PLAN)))
        mod = types.ModuleType("semantic_layer")
        mod.check_underspecified = AsyncMock(side_effect=RuntimeError("registry down"))
        monkeypatch.setitem(sys.modules, "semantic_layer", mod)

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
