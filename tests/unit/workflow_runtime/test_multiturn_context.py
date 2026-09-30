"""Multi-turn context survives a clarification.

Seen live: "Thông tin về Phạm Nhật Vượng" … "Phân tích vai trò của ông trong
Vingroup" → asked "theo góc độ nào?" → "quản trị" → an answer about Nguyễn Đức
Trung. The answer was merged into the question that was asked and sent on
without a rewrite, so the agent — which sees that one question, never the
conversation — searched "vai trò của ông trong Vingroup" and answered about
whoever the search returned.

Pinned here: the merged question is rewritten against the history (and may not
ask again), on both the single-agent and multi-agent paths.
"""
from __future__ import annotations

import pathlib
import sys
import warnings
from unittest.mock import AsyncMock, MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_BACKEND = str(REPO_ROOT / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

pytest.importorskip("langgraph", reason="workflow_runtime deps not installed")
pytest.importorskip("psycopg_pool", reason="workflow_runtime deps not installed")

pytestmark = pytest.mark.unit

HISTORY = [
    {"role": "user", "content": "Thông tin về Phạm Nhật Vượng"},
    {"role": "assistant", "content": "Bạn cần thông tin về Phạm Nhật Vượng trong lĩnh vực nào?"},
    {"role": "user", "content": "bất động sản"},
    {"role": "assistant", "content": "Phạm Nhật Vượng là Chủ tịch Tập đoàn Vingroup, một trong những doanh nhân hàng đầu trong lĩnh vực bất động sản tại Việt Nam."},
]
ASKED = "Phân tích vai trò của ông trong Vingroup"
REWRITTEN = "Phân tích vai trò quản trị của ông Phạm Nhật Vượng trong Vingroup"


def _import(name: str):
    # litellm emits a pydantic "Valid config keys have changed in V2"
    # UserWarning at import (see test_graph_cache.py).
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Valid config keys have changed in V2", category=UserWarning)
        import importlib
        return importlib.import_module(name)


@pytest.fixture
def refine():
    return _import("services.workflow_runtime.application.orchestrator.nodes.input_refine")


@pytest.fixture
def graph_mod(monkeypatch):
    module = _import("services.workflow_runtime.application.orchestrator.graph")
    monkeypatch.setattr(module, "adispatch_custom_event", AsyncMock())
    monkeypatch.setattr(module, "get_langfuse_client", lambda: None)
    monkeypatch.setattr(module, "run_suggestion", AsyncMock(return_value=[]))
    return module


def _llm_reply(monkeypatch, refine, content: str) -> AsyncMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    call = AsyncMock(return_value=response)
    monkeypatch.setattr(refine.llm_call, "acompletion", call)
    return call


class TestRewriteOnlyMode:
    async def test_asking_again_after_an_answer_is_refused(self, refine, monkeypatch):
        _llm_reply(monkeypatch, refine, '{"action": "clarify", "questions": [{"text": "Góc độ nào?"}]}')
        merged = refine.merge_clarification(ASKED, "quản trị")

        assert await refine.run_input_refine(merged, HISTORY, {}, allow_clarify=False) == (merged, [])

    async def test_the_model_is_told_the_user_just_answered(self, refine, monkeypatch):
        call = _llm_reply(monkeypatch, refine, f'{{"action": "rewrite", "query": "{REWRITTEN}"}}')

        out = await refine.run_input_refine(refine.merge_clarification(ASKED, "quản trị"), HISTORY, {}, allow_clarify=False)

        prompt = call.await_args.kwargs["messages"][1]["content"]
        assert refine.ANSWERED_MARKER in prompt
        assert "Phạm Nhật Vượng" in prompt
        assert out == (REWRITTEN, [])

    async def test_a_new_question_does_not_carry_the_marker(self, refine, monkeypatch):
        call = _llm_reply(monkeypatch, refine, '{"action": "keep", "query": "x"}')

        await refine.run_input_refine(ASKED, HISTORY, {})

        assert refine.ANSWERED_MARKER not in call.await_args.kwargs["messages"][1]["content"]

    def test_the_prompt_knows_the_marker_and_the_pronoun_rule(self, refine):
        assert refine.ANSWERED_MARKER in refine._SYSTEM
        assert "ông Phạm Nhật Vượng" in refine._SYSTEM
        assert "KHÔNG clarify khi câu có đại từ" in refine._SYSTEM


class TestHistory:
    def test_the_entity_in_a_longer_answer_is_not_cut_off(self, refine):
        answer = "Tập đoàn đa ngành hàng đầu Việt Nam, hoạt động trong nhiều lĩnh vực. " * 5 + "Chủ tịch là Phạm Nhật Vượng."

        lines = refine.history_lines([{"role": "assistant", "content": answer[-590:]}])

        assert "Phạm Nhật Vượng" in lines[0]

    def test_reaches_back_past_a_clarification_exchange(self, refine):
        messages = [{"role": "user", "content": "Thông tin về Phạm Nhật Vượng"}] + [
            {"role": r, "content": f"lượt {i}"} for i, r in enumerate(["assistant", "user"] * 3 + ["assistant"])
        ]

        assert "Phạm Nhật Vượng" in "\n".join(refine.history_lines(messages))

    def test_only_conversation_roles_are_kept(self, refine):
        lines = refine.history_lines([{"role": "tool", "content": "{}"}, {"role": "user", "content": "  hỏi\n  gì  "}])

        assert lines == ["User: hỏi gì"]


class TestSingleAgentPath:
    async def _run(self, graph_mod, monkeypatch, state_extra):
        prompts: list[str] = []

        async def fake_stream(**kwargs):
            prompts.append(kwargs["prompt"])
            yield ("conv_id", "c-1")
            yield ("token", "Trả lời")

        refine = AsyncMock(return_value=(REWRITTEN, []))
        monkeypatch.setattr(graph_mod, "call_agent_stream", fake_stream)
        monkeypatch.setattr(graph_mod, "run_input_refine", refine)
        state = {
            "query": ASKED, "agent_id": "a-1", "user_id": "u-1", "tenant_id": "t-1",
            "user_roles": [], "messages": HISTORY, "cfg": {}, **state_extra,
        }
        out = await graph_mod.single_agent_node(state, {"configurable": {}})
        return out, prompts, refine

    async def test_a_resumed_answer_reaches_the_agent_with_the_name(self, graph_mod, monkeypatch):
        out, prompts, refine = await self._run(graph_mod, monkeypatch, {"clarification_response": "quản trị"})

        merged = refine.await_args.args[0]
        assert ASKED in merged and "quản trị" in merged
        assert refine.await_args.args[1] == HISTORY
        assert refine.await_args.kwargs["allow_clarify"] is False
        assert prompts[-1] == REWRITTEN
        assert out["rewritten_query"] == REWRITTEN

    async def test_card_picks_are_rewritten_too(self, graph_mod, monkeypatch):
        _, prompts, refine = await self._run(graph_mod, monkeypatch, {
            "clarification_answers": [{"reason": "missing_time_range", "value": "this_month"}],
        })

        assert refine.await_args.kwargs["allow_clarify"] is False
        assert prompts[-1] == REWRITTEN

    async def test_a_new_question_may_still_ask(self, graph_mod, monkeypatch):
        _, _, refine = await self._run(graph_mod, monkeypatch, {})

        assert refine.await_args.kwargs["allow_clarify"] is True


class TestMultiAgentPath:
    async def test_a_resumed_answer_is_rewritten_before_planning(self, refine, monkeypatch):
        rewrite = AsyncMock(return_value=(REWRITTEN, []))
        monkeypatch.setattr(refine, "run_input_refine", rewrite)

        out = await refine.input_refine_node(
            {"query": ASKED, "clarification_response": "quản trị", "messages": HISTORY, "cfg": {}},
            {"configurable": {}},
        )

        assert out["rewritten_query"] == REWRITTEN
        assert rewrite.await_args.kwargs["allow_clarify"] is False
