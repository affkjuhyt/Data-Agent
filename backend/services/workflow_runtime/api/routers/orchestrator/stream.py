from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import aclosing, nullcontext
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from langgraph.types import Command

from libs.shared.auth import require_tenant
from services.workflow_runtime.api.auth import get_current_user
from services.workflow_runtime.application.orchestrator import config
from services.workflow_runtime.application.orchestrator.agent_catalog import (
    fetch_guardrail_config,
    fetch_published_agents,
)
from services.workflow_runtime.application.orchestrator.graph import (
    discard_plan_speculation,
    get_graph,
)
from services.workflow_runtime.application.orchestrator.nodes.guardrail import run_guardrail
from services.workflow_runtime.application.orchestrator.planner import create_plan
from services.workflow_runtime.application.orchestrator.schemas import (
    ClarificationResume,
    OrchestrateRequest,
)
from services.workflow_runtime.infrastructure.observability import (
    get_langchain_callback_handler,
    get_langfuse_client,
)

logger = logging.getLogger(__name__)
router = APIRouter()
# Included WITHOUT auth by app.py (like /health): Kong's probes for the old
# orchestrator service still hit this path.
health_router = APIRouter()

# Admission control: one slot per in-flight agent turn. Guards the shared
# LLM/GPU from a pile-up of slow/abandoned turns: an abandoned turn's LLM calls
# keep running, so retries snowball into a queue every new turn waits behind
# (see config.MAX_CONCURRENT_TURNS). Paired with cancel-on-disconnect below,
# which frees the slot and the in-flight LLM call when the client goes away.
# None when MAX_CONCURRENT_TURNS == 0 (unlimited).
_turn_slots: asyncio.Semaphore | None = (
    asyncio.Semaphore(config.MAX_CONCURRENT_TURNS) if config.MAX_CONCURRENT_TURNS > 0 else None
)


async def _try_acquire_turn() -> bool:
    """Non-blocking acquire: a turn over the cap is shed (429), never queued.

    `locked()` is True when no slot is free; when it is False `acquire()`
    completes without suspending (asyncio.Semaphore only awaits when it must
    wait), and there is no other await between the check and the acquire, so
    the slot cannot be stolen in between."""
    if _turn_slots is None:
        return True
    if _turn_slots.locked():
        return False
    await _turn_slots.acquire()
    return True


def _release_turn() -> None:
    if _turn_slots is not None:
        _turn_slots.release()


def _overloaded() -> JSONResponse:
    logger.warning("orchestrate | shed 429: %d turns in flight (cap %d)",
                   config.MAX_CONCURRENT_TURNS, config.MAX_CONCURRENT_TURNS)
    return JSONResponse(
        {"error": "overloaded",
         "detail": "too many concurrent agent turns — retry shortly"},
        status_code=429, headers={"Retry-After": "2"},
    )


# asyncio keeps only weak references to tasks; hold fire-and-forget ones here.
_background_tasks: set[asyncio.Task[Any]] = set()


def _flush_in_background(lf: Any) -> None:
    """Langfuse export is a network call the client should not wait on. The
    orchestrate span is already closed, so sending [DONE] first loses nothing."""
    task = asyncio.create_task(asyncio.to_thread(lf.flush))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@health_router.get("/api/orchestrate/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _sse(data: Any) -> str:
    return f"data: {json.dumps(data)}\n\n"


SESSION_NOT_FOUND = "Session không tồn tại hoặc đã hoàn thành."


async def load_pending_session(graph: Any, session_id: str, caller: dict[str, Any]) -> Any:
    """The interrupted session this caller may resume, or 404.

    The checkpointer keys on the session id alone, so without this anyone
    holding another user's id could continue — and read the answer to — their
    turn. A session that belongs to someone else answers exactly like one that
    does not exist, so ids cannot be probed.
    """
    tenant_id = require_tenant(caller)
    snapshot = await graph.aget_state({"configurable": {"thread_id": session_id}})
    values = snapshot.values or {}
    if (
        not snapshot.next
        or str(values.get("tenant_id") or "") != tenant_id
        or values.get("user_ref") != (caller.get("user_id") or "anonymous")
    ):
        raise HTTPException(404, SESSION_NOT_FOUND)
    return snapshot


def _interrupt_value(snapshot: Any) -> dict[str, Any]:
    for task in snapshot.tasks:
        interrupts = getattr(task, "interrupts", None)
        if interrupts:
            value = interrupts[0].value
            return value if isinstance(value, dict) else {}
    return {}


def pending_questions(snapshot: Any) -> list[str]:
    """The clarification questions an interrupted graph is waiting on."""
    return list(_interrupt_value(snapshot).get("clarification_questions", []))


def pending_details(snapshot: Any) -> list[dict[str, Any]]:
    """The same questions with their options ({text, options}). A session
    interrupted before options existed carries texts only."""
    value = _interrupt_value(snapshot)
    details = value.get("clarification_details")
    if isinstance(details, list):
        return details
    return [{"text": text, "options": []} for text in value.get("clarification_questions", [])]


async def _stream_graph(
    graph: Any,
    graph_input: Any,
    *,
    session_id: str,
    configurable: dict[str, Any],
    received: float,
    span_name: str,
    span_input: dict[str, Any],
    span_metadata: dict[str, Any],
) -> AsyncGenerator[str, None]:
    """Run — or resume — the orchestrator graph as SSE frames.

    Shared by a new turn and a resumed one, so the answer to a clarification
    streams like any other: tokens, charts, evidence, and a clarification card
    if it has to ask again.
    """
    lf = get_langfuse_client()
    guardrail_task: asyncio.Task[Any] | None = configurable.get("guardrail_task")

    # session_id = trace_id — emit ngay để frontend fetch trace sau này
    # không phải đợi __conv_ids của từng agent con.
    yield _sse({"__session_id": session_id})

    final_response = ""
    span_ctx = lf.start_as_current_span(
        trace_context={"trace_id": session_id},
        name=span_name,
        input=span_input,
        metadata=span_metadata,
    ) if lf else nullcontext()

    # What the user waited for, recorded on the root span so the trace panel
    # can show it: the first answer token and the last one (the answer
    # complete), counted from the moment the request arrived.
    first_token_ms: int | None = None
    last_token_ms: int | None = None

    with span_ctx as root:
        # PHẢI tạo handler ở đây, sau khi span "orchestrate" đã là OTEL
        # current span — get_langchain_callback_handler() đọc context này
        # để nối đúng cha, tạo trước sẽ khiến nó tự thành root (xem
        # observability.py).
        handler = get_langchain_callback_handler()
        lg_config = {
            "configurable": {"thread_id": session_id, **configurable},
            "callbacks": [handler] if handler else [],
        }
        try:
            async for event in graph.astream_events(
                graph_input, config=lg_config, version="v2"
            ):
                kind = event["event"]
                name = event.get("name", "")

                if kind == "on_custom_event":
                    if name == "token":
                        final_response += event["data"] if isinstance(event["data"], str) else ""
                        if final_response:
                            last_token_ms = round((time.monotonic() - received) * 1000)
                            if first_token_ms is None:
                                first_token_ms = last_token_ms
                        yield _sse(event["data"])
                    elif name == "reset":
                        # The answer on screen was withdrawn; its replacement follows.
                        final_response = ""
                        yield _sse({"__reset": True})
                    elif name == "pending_review":
                        yield _sse({"__pending_review": event["data"]})
                    elif name == "chart":
                        yield _sse({"__chart": event["data"]})
                    elif name == "citations":
                        yield _sse({"__citations": event["data"]})
                    elif name == "options":
                        yield _sse({"__options": event["data"]})
                    elif name == "evidence":
                        yield _sse({"__evidence": event["data"]})
                    elif name == "result_set":
                        yield _sse({"__result_set": event["data"]})
                    elif name == "refusal":
                        yield _sse({"__refusal": event["data"]})

        except Exception as e:
            logger.error("stream | graph error: %s", e)
            if lf:
                _flush_in_background(lf)
            if guardrail_task is not None and not guardrail_task.done():
                guardrail_task.cancel()
            # The answer may already have streamed in full — a failure in a
            # post-answer step (checkpoint serialisation, judge, suggestion)
            # must NOT surface as a scary error on top of a good answer.
            if not final_response.strip():
                yield _sse("Đã xảy ra lỗi, vui lòng thử lại.")
            yield "data: [DONE]\n\n"
            return
        finally:
            if guardrail_task is not None and not guardrail_task.done():
                guardrail_task.cancel()
            if root is not None:
                root.update(metadata={
                    "ttft_ms": first_token_ms,
                    "response_ms": last_token_ms,
                    "total_ms": round((time.monotonic() - received) * 1000),
                    "answer_chars": len(final_response),
                })

    if lf:
        _flush_in_background(lf)

    snapshot = await graph.aget_state(lg_config)
    if snapshot.next:
        yield _sse({"__clarification": {
            "session_id": session_id,
            "questions":  pending_questions(snapshot),
            # Per question: {text, options} — the card renders the options as
            # buttons next to its own "Other".
            "details":    pending_details(snapshot),
        }})
        yield "data: [DONE]\n\n"
        return

    final_state = snapshot.values
    # Không còn check guardrail_triggered ở đây — chặn giờ xảy ra ngay tại
    # gate (single_agent_node/execute_node, xem graph.py), câu từ chối đã
    # được dispatch như 1 "token" bình thường ở trên rồi. suggestions/
    # target_results tự nhiên rỗng khi bị chặn (suggestion_node/summary_node
    # bị routing bỏ qua), nên không cần check lại ở đây.
    suggestions = final_state.get("suggestions", [])
    if suggestions:
        yield _sse({"__suggestions": suggestions})

    # conv_ids giữ lại cho mục đích hiển thị (agent_name/title mỗi task) và để
    # frontend nối tiếp conversation của agent ở lượt sau — KHÔNG còn cần
    # thiết để fetch trace nữa (frontend fetch 1 lần theo __session_id ở trên,
    # xem AgentPlayground.tsx).
    target_results = final_state.get("target_results", [])
    conv_infos = [
        {
            "conv_id":    task.conversation_id,
            "title":      task.task_title,
            "agent_name": task.agent_name,
        }
        for tr in target_results
        for task in tr.task_results
        if task.conversation_id
    ]
    if conv_infos:
        yield _sse({"__conv_ids": conv_infos})

    yield "data: [DONE]\n\n"


def _sse_response(
    frames: AsyncGenerator[str, None], cleanup: Callable[[], None]
) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        # Cancel-on-disconnect + admission release: if the client goes away,
        # Starlette aclose()s this generator, aclosing() closes the graph
        # stream → graph.astream_events is closed → the in-flight LLM node task
        # is cancelled (httpx connection to litellm closes). Either way the
        # finally frees the admission slot so a dropped turn can't wedge the GPU.
        try:
            async with aclosing(frames) as chunks:
                async for chunk in chunks:
                    yield chunk
        finally:
            _release_turn()
            cleanup()

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/api/orchestrate/stream")
async def stream_orchestration(
    req: OrchestrateRequest,
    request: Request,
    caller: dict[str, Any] = Depends(get_current_user),
) -> Response:
    # Start of the user's wait: the timings recorded on the trace count from here.
    received = time.monotonic()

    # Verified identity, not raw headers: get_current_user has already proven
    # this came from Kong (gateway key) or is a signed assertion. The old
    # `headers.get(...) or DEV_TENANT_ID` fallback meant an unauthenticated
    # caller silently became the dev tenant.
    #
    # The fallback is gone entirely now, not just moved behind authentication.
    # A verified caller can still arrive without a tenant — a Keycloak token
    # missing the claim, an assertion signed for a process rather than a tenant
    # — and `or DEV_TENANT_ID` pointed exactly those requests at a real tenant's
    # data. 403 makes a misconfigured token visible instead of quietly serving
    # tenant 0000...0001.
    #
    # Resolved before taking an admission slot: a 403 raised after it leaked
    # the slot.
    tenant_id = require_tenant(caller)

    # Admission control — shed instead of piling a slow turn onto the busy GPU.
    if not await _try_acquire_turn():
        return _overloaded()

    # hex32 (không dấu gạch ngang) — format trace_id chuẩn OTEL, dùng chung
    # làm LangGraph thread_id VÀ Langfuse trace_id (xem observability.py).
    session_id = (req.session_id or uuid.uuid4().hex).replace("-", "")
    try:
        cfg = await fetch_guardrail_config(tenant_id)
    except BaseException:
        _release_turn()
        raise
    # Bắn nền ngay từ đầu, chạy song song với input_refine/route/planner (đều
    # thuần LLM/DB-read, không side-effect) — chỉ được await tại 2 "gate"
    # trước hành động thật (single_agent_node/execute_node, xem graph.py).
    # Input đã trọn vẹn ngay từ t=0 nên race này không đánh đổi gì về đúng-đắn,
    # chỉ ẩn latency của guardrail phía sau latency của pipeline.
    guardrail_task = asyncio.create_task(run_guardrail(req.query, cfg, req.messages))
    # A multi-agent turn needs the tenant's published agents. The query only
    # needs tenant_id, so it runs alongside input_refine instead of after it.
    agents_task = None if req.agent_id else asyncio.create_task(fetch_published_agents(tenant_id))
    # The ToT plan only needs the question and the agents, and makes no calls
    # beyond the model — so it starts now instead of after input_refine. Used
    # when input_refine keeps the question; dropped if it rewrites or asks.
    plan_speculation: dict[str, Any] | None = None
    if agents_task is not None and not req.clarification_response:
        async def _plan_ahead() -> Any:
            return await create_plan(req.query, agents=await agents_task)

        plan_speculation = {"query": req.query, "task": asyncio.create_task(_plan_ahead())}

    initial_state = {
        "query":                   req.query,
        "rewritten_query":         req.query,
        "user_id":                 req.user_id,
        "tenant_id":               tenant_id,
        "user_ref":                caller["user_id"] or "anonymous",
        # Carried to workflow_runtime so the agent asserts the REAL caller
        # downstream (kb_search fails closed on an empty list).
        "user_roles":              caller["roles"],
        "session_id":              session_id,
        "agent_id":                req.agent_id,
        "messages":                req.messages,
        "cfg":                     cfg,
        "guardrail_triggered":     False,
        "guardrail_reason":        "",
        "clarification_needed":    False,
        "clarification_questions": [],
        "clarification_response":  req.clarification_response,
        "clarification_answers":   [a.model_dump() for a in req.clarification_answers],
        # Only one agent to continue with in single-agent mode; a multi-agent
        # plan fans out to several.
        "agent_conversation_id":   req.conversation_id if req.agent_id else None,
        "agents":                  [],
        "plan":                    None,
        "process":                 None,
        "completed_node_ids":      [],
        "final_response":          "",
        "citations":               [],
        "suggestions":             [],
        "suggestions_ready":       False,
        "target_results":          [],
        "recovered":               False,
        "success":                 False,
        "refused":                 False,
        "planner_ms":              0,
        "executor_ms":             0,
        "summary_ms":              0,
    }

    def cleanup() -> None:
        if not guardrail_task.done():
            guardrail_task.cancel()
        if agents_task is not None and not agents_task.done():
            agents_task.cancel()
        # Never taken: input_refine asked or rewrote, or the turn failed.
        discard_plan_speculation(plan_speculation)

    try:
        graph = await get_graph()
    except BaseException:
        _release_turn()
        cleanup()
        raise

    frames = _stream_graph(
        graph, initial_state,
        session_id=session_id,
        configurable={
            "redis": request.app.state.redis,
            "guardrail_task": guardrail_task,
            "agents_task": agents_task,
            "plan_speculation": plan_speculation,
        },
        received=received,
        span_name="orchestrate",
        span_input={"query": req.query, "agent_id": req.agent_id},
        span_metadata={"tenant_id": tenant_id, "user_id": req.user_id},
    )
    return _sse_response(frames, cleanup)


@router.post("/api/orchestrate/resume/stream")
async def stream_resume(
    req: ClarificationResume,
    request: Request,
    caller: dict[str, Any] = Depends(get_current_user),
) -> Response:
    """Continue an interrupted turn with the user's answer, streamed like /stream.

    POST /api/orchestrate/resume returns the whole answer in one JSON body and
    so drops every frame on the way — charts, citations, evidence, and the
    agent's own clarification, which reached the user as plain text with
    nothing to click.
    """
    received = time.monotonic()
    graph = await get_graph()
    snapshot = await load_pending_session(graph, req.session_id, caller)

    if not await _try_acquire_turn():
        return _overloaded()

    values = snapshot.values
    try:
        cfg = values.get("cfg") or await fetch_guardrail_config(require_tenant(caller))
    except BaseException:
        _release_turn()
        raise
    # The answer is new user input on its way to the agent (merged into its
    # question), so it passes the same input guardrail a question does. The
    # graph awaits it at the same gates as on /stream.
    guardrail_task = asyncio.create_task(
        run_guardrail(req.clarification, cfg, values.get("messages") or [])
    )

    def cleanup() -> None:
        if not guardrail_task.done():
            guardrail_task.cancel()

    frames = _stream_graph(
        graph,
        Command(resume=req.clarification, update={"clarification_response": req.clarification}),
        session_id=req.session_id,
        configurable={
            "redis": request.app.state.redis,
            "guardrail_task": guardrail_task,
            "agents_task": None,
            "plan_speculation": None,
        },
        received=received,
        # Same trace as the turn that asked (session_id = trace_id).
        span_name="orchestrate-resume",
        span_input={"clarification": req.clarification},
        span_metadata={"tenant_id": values.get("tenant_id"), "user_id": values.get("user_id")},
    )
    return _sse_response(frames, cleanup)
