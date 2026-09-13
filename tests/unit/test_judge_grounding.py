"""Unit tests for Judge Pha 2 GROUNDING_ERROR (backend/services/workflow_runtime/
nodes/model_harness/judge.py) — trường hợp data đúng nhưng responder diễn
giải sai. Reset chỉ "response" (giữ nguyên context đã tra được), route thẳng
về responder (không qua planner) — khác PLANNING_ERROR (reset toàn bộ,
route về planner). RETRIEVAL_ERROR/TOOL_ERROR không còn thuộc phạm vi của
judge_node nữa (đã chuyển sang judge_phase1_node, xem test_judge_phase1.py).
"""
from __future__ import annotations

import json
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _canned(content: dict):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(content)))]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return resp


def test_fail_grounding_error_only_resets_response():
    import nodes.model_harness.judge as judge_mod

    result = judge_mod._fail(iteration=0, error_type="GROUNDING_ERROR", reason="số liệu sai")

    assert result["judge_verdict"] == "FAIL"
    assert result["judge_error_type"] == "GROUNDING_ERROR"
    assert result["response"] == ""
    assert result["loop_iteration"] == 1
    # Không reset context/data đã tra được — khác PLANNING_ERROR, vì context
    # đúng, chỉ có bước diễn giải của responder bị sai.
    assert "kb_chunks" not in result
    assert "tool_results" not in result
    assert "reranked_chunks" not in result


def test_fail_planning_error_resets_everything_downstream():
    import nodes.model_harness.judge as judge_mod

    result = judge_mod._fail(iteration=0, error_type="PLANNING_ERROR", reason="sai chiến lược")

    assert result["response"] == ""
    assert result["tool_calls"] == []
    assert result["kb_chunks"] == []
    assert result["reranked_chunks"] == []


def test_error_types_no_longer_include_retrieval_or_tool_error():
    import nodes.model_harness.judge as judge_mod

    assert "RETRIEVAL_ERROR" not in judge_mod._ERROR_TYPES
    assert "TOOL_ERROR" not in judge_mod._ERROR_TYPES
    assert judge_mod._ERROR_TYPES == frozenset({"DATA_QUALITY", "PLANNING_ERROR", "GROUNDING_ERROR"})


async def test_judge_node_accepts_grounding_error_verdict(monkeypatch):
    import nodes.model_harness.judge as judge_mod

    async def fake_acompletion(**kwargs):
        return _canned({"verdict": "FAIL", "error_type": "GROUNDING_ERROR", "reason": "trích sai số liệu"})

    monkeypatch.setattr(judge_mod.litellm, "acompletion", fake_acompletion)

    state = {
        "config": {},
        "loop_iteration": 0,
        "response": "câu trả lời có số liệu sai",
        "reranked_chunks": [{"content": "context đúng"}],
        "tool_errors": [],
        "query": "câu hỏi test",
    }
    result = await judge_mod.judge_node(state)

    assert result["judge_verdict"] == "FAIL"
    assert result["judge_error_type"] == "GROUNDING_ERROR"
    assert result["response"] == ""


def test_route_after_judge_grounding_error_goes_to_responder():
    import graph as graph_mod

    route = graph_mod._make_route_after_judge(frozenset({"planner", "responder", "kb_search"}))

    state = {"judge_verdict": "FAIL", "judge_error_type": "GROUNDING_ERROR"}
    assert route(state) == "responder"


def test_route_after_judge_planning_error_goes_to_planner():
    import graph as graph_mod

    route = graph_mod._make_route_after_judge(frozenset({"planner", "responder"}))

    state = {"judge_verdict": "FAIL", "judge_error_type": "PLANNING_ERROR"}
    assert route(state) == "planner"


def test_route_after_judge_pass_goes_to_end():
    import graph as graph_mod

    route = graph_mod._make_route_after_judge(frozenset({"planner", "responder"}))
    assert route({"judge_verdict": "PASS"}) == "end"


async def test_the_judge_sees_the_rows_the_responder_answered_from(monkeypatch):
    """The judge used to see 400 chars per item: a monthly SQL result was cut to
    its first months, every later figure read as invented, and a correct answer
    failed GROUNDING_ERROR (3/3 live). It now gets the responder's budget."""
    import nodes.model_harness.judge as judge_mod

    captured = {}

    async def fake_completion(**kwargs):
        captured["user"] = kwargs["messages"][1]["content"]
        return _canned({"verdict": "PASS"})

    monkeypatch.setattr(judge_mod.litellm, "acompletion", fake_completion, raising=False)
    from services.workflow_runtime.runtime_services import llm_call
    monkeypatch.setattr(llm_call, "get_langfuse_client", lambda: None)
    rows = "".join(f'{{"month": {m}, "net": {m}000000}}' for m in range(1, 80))
    assert len(rows) > 1500

    await judge_mod.judge_node({
        "config": {"context_max_chars": 1500},
        "query": "doanh thu thuần theo tháng",
        "response": "Tháng 40: 40000000",
        "loop_iteration": 0,
        "reranked_chunks": [{"content": rows}],
        "tool_errors": [],
    })

    assert rows[:1500] in captured["user"]
    assert rows[:1501] not in captured["user"]
