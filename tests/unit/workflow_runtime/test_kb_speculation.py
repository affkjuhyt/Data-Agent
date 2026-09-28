"""Speculative knowledge-base search (backend/services/workflow_runtime/nodes/
sandbox/kb_search.py).

The planner starts searching the user's question the moment it starts deciding;
kb_search_node takes that result only when its own query is the same question.
A rewritten query, or a plan without kb_search, cancels it — so the answer is
built from exactly the search it would have run anyway.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")

pytestmark = pytest.mark.unit

_CFG = {"kb_endpoint": "http://kb-backend:8220"}


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _kb_search():
    import nodes.sandbox.kb_search as kb_search
    return kb_search


def _record_searches(kb_search, monkeypatch, *, block_on: str | None = None):
    calls: list[str] = []
    started = asyncio.Event()

    async def fake_search(cfg, query):
        calls.append(query)
        if query == block_on:
            started.set()
            await asyncio.sleep(30)
        return {"kb_chunks": [{"id": query, "content": query, "score": 1.0}]}

    monkeypatch.setattr(kb_search, "search_knowledge_base", fake_search)
    return calls, started


class TestKbSearchNode:
    async def test_the_same_question_reuses_the_speculative_search(self, monkeypatch):
        kb = _kb_search()
        calls, _ = _record_searches(kb, monkeypatch)
        speculation = {"enabled": True}
        kb.start_speculative_search(speculation, _CFG, "Quy định  nghỉ phép?")

        result = await kb.kb_search_node(
            {"config": _CFG, "query": "quy định nghỉ phép?", "tool_calls": []},
            {"configurable": {"kb_speculation": speculation}},
        )

        assert calls == ["Quy định  nghỉ phép?"], "searched twice instead of reusing"
        assert result["kb_chunks"][0]["id"] == "Quy định  nghỉ phép?"
        assert "task" not in speculation

    async def test_a_rewritten_query_cancels_it_and_searches_again(self, monkeypatch):
        kb = _kb_search()
        calls, started = _record_searches(kb, monkeypatch, block_on="câu hỏi gốc")
        speculation = {"enabled": True}
        kb.start_speculative_search(speculation, _CFG, "câu hỏi gốc")
        task = speculation["task"]
        await started.wait()

        result = await kb.kb_search_node(
            {"config": _CFG, "query": "câu hỏi gốc",
             "tool_calls": [{"type": "kb_search", "query": "từ khoá viết lại"}]},
            {"configurable": {"kb_speculation": speculation}},
        )

        assert result["kb_chunks"][0]["id"] == "từ khoá viết lại"
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_without_speculation_it_searches_directly(self, monkeypatch):
        kb = _kb_search()
        calls, _ = _record_searches(kb, monkeypatch)

        await kb.kb_search_node({"config": _CFG, "query": "q", "tool_calls": []})

        assert calls == ["q"]

    async def test_nothing_starts_when_disabled_or_without_a_knowledge_base(self, monkeypatch):
        kb = _kb_search()
        _record_searches(kb, monkeypatch)
        disabled = {"enabled": False}
        no_endpoint = {"enabled": True}

        kb.start_speculative_search(disabled, _CFG, "q")
        kb.start_speculative_search(no_endpoint, {}, "q")

        assert "task" not in disabled and "task" not in no_endpoint


def _canned(content: dict):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=json.dumps(content)))]
    resp.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return resp


class TestPlannerDropsTheSearchItWillNotUse:
    async def _plan(self, monkeypatch, tool_calls):
        import nodes.model_harness.planner as planner

        async def fake_completion(**kwargs):
            return _canned({"intent": "question", "analysis_type": "analytical",
                            "language": "vi", "tool_calls": tool_calls})

        monkeypatch.setattr(planner.litellm, "acompletion", fake_completion, raising=False)
        from services.workflow_runtime.runtime_services import llm_call
        monkeypatch.setattr(llm_call, "get_langfuse_client", lambda: None)

        async def pending():
            await asyncio.sleep(30)

        task = asyncio.create_task(pending())
        speculation = {"enabled": True, "query": "doanh thu", "task": task}
        await planner.planner_node(
            {"query": "doanh thu", "config": {}, "memory_context": [], "workflow_memory_context": [],
             "loop_iteration": 0, "judge_reason": "", "previous_clarification": True},
            {"configurable": {"kb_speculation": speculation}},
        )
        return task, speculation

    async def test_a_plan_with_tools_but_no_kb_search_cancels_it(self, monkeypatch):
        task, speculation = await self._plan(monkeypatch, [{"type": "db_query", "query": "doanh thu"}])

        assert "task" not in speculation
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_a_plan_with_no_tools_keeps_it_for_the_kb_search_fallback(self, monkeypatch):
        task, speculation = await self._plan(monkeypatch, [])

        assert speculation.get("task") is task and not task.done()
        task.cancel()
