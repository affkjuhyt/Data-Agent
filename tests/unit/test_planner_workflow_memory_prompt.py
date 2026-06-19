"""Verifies planner_node actually builds the `<past_successful_runs>` block
(doc §3.7/§IV) from `state["workflow_memory_context"]` and sends it to the
model. `litellm.completion` is monkeypatched to a canned response — no real
LLM call happens — so this tests OUR prompt-construction code, not whether
a model reacts well to it (that needs a real model and is out of scope
here, see docs/STM_LTM_Memory_Implementation.md).
"""
from __future__ import annotations

import json
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "workflow-runtime")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR, str(REPO_ROOT)) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _canned_completion(content: dict):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(content)))]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return resp


@pytest.mark.unit
def test_planner_injects_past_successful_runs_block(monkeypatch):
    import nodes.model_harness.planner as planner_mod  # same import path graph.py uses

    captured = {}

    def fake_completion(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _canned_completion({"intent": "task", "language": "vi", "tool_calls": []})

    monkeypatch.setattr(planner_mod.litellm, "completion", fake_completion)

    state = {
        "query": "doanh thu quý 1 theo khu vực",
        "config": {},
        "memory_context": [],
        "workflow_memory_context": [
            {"task_description": "doanh thu quý 1 theo khu vực", "param_snapshot": {"filters": {"quarter": "Q1"}}},
        ],
        "loop_iteration": 0,
        "judge_reason": "",
    }

    result = planner_mod.planner_node(state)

    system_msg = captured["messages"][0]["content"]
    assert "<past_successful_runs>" in system_msg
    assert "doanh thu quý 1 theo khu vực" in system_msg
    assert "Q1" in system_msg
    assert result["intent"] == "task"


@pytest.mark.unit
def test_planner_omits_block_when_no_workflow_memory_context(monkeypatch):
    import nodes.model_harness.planner as planner_mod

    captured = {}

    def fake_completion(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _canned_completion({"intent": "question", "language": "vi", "tool_calls": []})

    monkeypatch.setattr(planner_mod.litellm, "completion", fake_completion)

    state = {
        "query": "doanh thu quý 1 theo khu vực",
        "config": {},
        "memory_context": [],
        "workflow_memory_context": [],
        "loop_iteration": 0,
        "judge_reason": "",
    }

    planner_mod.planner_node(state)

    assert "<past_successful_runs>" not in captured["messages"][0]["content"]
