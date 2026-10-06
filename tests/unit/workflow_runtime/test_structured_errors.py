"""A turn that cannot be answered says why, by code (V3 R0-7, NOP-319).

Two paths used to answer anyway: an unreadable agent config became DEV_CONFIG
(another agent's prompt, model and tools, under the name the user asked), and an
unparseable plan became a document search on the raw question. Both now end the
turn with a `__refusal {code, message, next_action, detail_id}` frame and no
answer token.
"""

from __future__ import annotations

import json
import types
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.workflow_runtime.application import config_loader
from services.workflow_runtime.application.runtime_services import conversation_runner as runner
from services.workflow_runtime.domain.refusals import AgentConfigError, PlannerError
from services.workflow_runtime.domain.schemas import RunRequest

pytest.importorskip("litellm", reason="litellm not installed in this environment")

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"
CALLER = {"user_id": "user-1", "tenant_id": TENANT, "roles": ["viewer"]}


# ── config ───────────────────────────────────────────────────────────────────


def _connector(monkeypatch: pytest.MonkeyPatch, *, connected: bool, fails: bool = False) -> None:
    class _Session:
        async def execute(self, _stmt: Any) -> Any:
            raise RuntimeError('relation "Agents" does not exist')

    class _Client:
        def is_connected(self) -> bool:
            return connected

        @asynccontextmanager
        async def get_client(self, tenant_id: str | None = None):
            yield _Session()

    connector = types.ModuleType("libs.database_connector.postgres_connector")
    connector.client = _Client()  # type: ignore[attr-defined]
    mongo = types.ModuleType("services.workflow_runtime.infrastructure.mongo_client")
    mongo.load_flow_nodes = AsyncMock(return_value=[])  # type: ignore[attr-defined]
    mongo.load_flow_edges = AsyncMock(return_value=[])  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, connector.__name__, connector)
    monkeypatch.setitem(__import__("sys").modules, mongo.__name__, mongo)


@pytest.mark.parametrize("connected", [False, True], ids=["not-connected", "query-fails"])
async def test_an_unreadable_config_is_refused_not_replaced(monkeypatch, connected):
    monkeypatch.delenv("RUNTIME_DEV_FALLBACK", raising=False)
    _connector(monkeypatch, connected=connected)

    with pytest.raises(AgentConfigError):
        await config_loader.load_agent_config("agent-1", tenant_id=TENANT)


async def test_dev_config_only_behind_the_personal_dev_flag(monkeypatch):
    monkeypatch.setenv("RUNTIME_DEV_FALLBACK", "1")
    _connector(monkeypatch, connected=False)

    cfg = await config_loader.load_agent_config("agent-1", tenant_id=TENANT)

    assert cfg is not None and cfg["system_prompt"] == config_loader.DEV_CONFIG["system_prompt"]


# ── runner ───────────────────────────────────────────────────────────────────


@pytest.fixture
def quiet_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_fire_and_forget", lambda coro: coro.close())
    monkeypatch.setattr(runner, "validate_agent_tenant", AsyncMock(return_value=True))
    monkeypatch.setattr(runner, "create_conversation", AsyncMock(return_value="conv-1"))
    monkeypatch.setattr(runner, "get_langchain_callback_handler", lambda **_: None)


async def _frames(req: RunRequest) -> list[Any]:
    response = await runner.run_conversation_stream(req, types.SimpleNamespace(headers={}), CALLER)  # type: ignore[arg-type]
    out = []
    async for chunk in response.body_iterator:
        payload = (
            (chunk.decode() if isinstance(chunk, bytes) else chunk).removeprefix("data: ").strip()
        )
        out.append(payload if payload.startswith("[") else json.loads(payload))
    return out


async def test_db_down_streams_a_refusal_and_no_answer(monkeypatch, quiet_runner):
    monkeypatch.setattr(
        runner, "load_agent_config", AsyncMock(side_effect=AgentConfigError("db down"))
    )

    frames = await _frames(
        RunRequest(
            query="Công nợ quá hạn trên 90 ngày theo chi nhánh",
            agent_id="agent-1",
            conversation_id="conv-7",
        )
    )

    assert frames == [
        {
            "__refusal": {
                "code": "AGENT_CONFIG_ERROR",
                "message": "Không đọc được cấu hình của agent này nên hệ thống chưa trả lời.",
                "next_action": "retry",
                "detail_id": "conv-7",
            }
        },
        "[DONE]",
    ]


async def test_an_unpublished_agent_points_at_its_owner(monkeypatch, quiet_runner):
    monkeypatch.setattr(runner, "load_agent_config", AsyncMock(return_value=None))

    [frame, done] = await _frames(RunRequest(query="q", agent_id="agent-1"))

    assert frame["__refusal"]["code"] == "AGENT_CONFIG_ERROR"
    assert frame["__refusal"]["next_action"] == "contact_steward"
    assert "chưa được publish" in frame["__refusal"]["message"]
    assert len(frame["__refusal"]["detail_id"]) == 32
    assert done == "[DONE]"


async def test_a_planner_failure_in_the_graph_becomes_a_refusal(monkeypatch, quiet_runner):
    monkeypatch.setattr(
        runner, "load_agent_config", AsyncMock(return_value={"agent_id": "agent-1"})
    )

    class _Graph:
        async def astream_events(self, *_: Any, **__: Any):
            yield {"event": "on_chain_start", "metadata": {"langgraph_node": "planner"}, "data": {}}
            raise PlannerError("garbage twice")

    monkeypatch.setattr(runner, "_default_graph", _Graph())

    frames = await _frames(RunRequest(query="q", agent_id="agent-1"))

    assert frames == [
        {
            "__refusal": {
                "code": "PLANNER_ERROR",
                "message": "Hệ thống chưa lập được cách trả lời cho câu hỏi này.",
                "next_action": "rephrase",
                "detail_id": "conv-1",
            }
        },
        "[DONE]",
    ]


# ── planner ──────────────────────────────────────────────────────────────────


def _completion(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    return resp


async def _plan(monkeypatch, *contents: str) -> tuple[Any, AsyncMock]:
    from services.workflow_runtime.application.nodes.model_harness import planner
    from services.workflow_runtime.application.runtime_services import llm_call

    call = AsyncMock(side_effect=[_completion(c) for c in contents])
    monkeypatch.setattr(planner.litellm, "acompletion", call)
    monkeypatch.setattr(llm_call, "get_langfuse_client", lambda: None)
    state = {
        "query": "doanh thu tháng 8",
        "config": {"planner_temperature": 0.7},
        "loop_iteration": 0,
    }
    try:
        return await planner.planner_node(state), call  # type: ignore[arg-type]
    except PlannerError as e:
        return e, call


async def test_garbage_twice_is_a_planner_error_after_one_retry_at_temperature_zero(monkeypatch):
    result, call = await _plan(monkeypatch, "không phải JSON", "vẫn không phải JSON")

    assert isinstance(result, PlannerError)
    assert [c.kwargs["temperature"] for c in call.await_args_list] == [0.7, 0]


async def test_garbage_then_a_plan_answers_normally(monkeypatch):
    plan = json.dumps(
        {
            "intent": "question",
            "language": "vi",
            "tool_calls": [{"type": "db_query", "query": "doanh thu tháng 8"}],
        }
    )
    result, call = await _plan(monkeypatch, "rác", plan)

    assert call.await_count == 2
    assert [tc["type"] for tc in result["tool_calls"]] == ["db_query"]


# ── orchestrator ─────────────────────────────────────────────────────────────


async def test_a_refused_task_fails_in_a_multi_agent_plan(monkeypatch):
    """call_agent (plan executor, delegation) treats a refusal like [ERROR]: an
    empty output passed upward would read as an agent that answered nothing."""
    from services.workflow_runtime.application.orchestrator.executor import agent_client

    async def _body():
        yield 'data: {"__refusal": {"code": "PLANNER_ERROR", "message": "m", "next_action": "rephrase"}}\n\n'
        yield "data: [DONE]\n\n"

    resp = MagicMock(
        headers={"X-Conversation-Id": "conv-1"}, body_iterator=_body(), status_code=200
    )
    monkeypatch.setattr(agent_client, "_run_agent", AsyncMock(return_value=resp))

    with pytest.raises(RuntimeError, match="PLANNER_ERROR"):
        await agent_client.call_agent("agent-1", "q", tenant_id=TENANT)
