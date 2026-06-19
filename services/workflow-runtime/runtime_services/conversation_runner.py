import asyncio
import json
import logging
import re
import time

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from config_loader import load_agent_config
from db import create_conversation, get_messages, save_message, save_trace, validate_agent_tenant
from redis_cache import cache_get_messages, cache_append_messages, cache_set_messages
from graph import build_graph
from memory_middleware import (
    _DEV_AGENT_ID,
    _DEV_TENANT_ID,
    apply_memory_policy,
    retrieve_episodic_past,
    retrieve_memories,
    retrieve_pending_reminders,
)
from workflow_memory import record_workflow_outcome, retrieve_workflow_memory
from observability import get_langfuse_handler
from schemas import RunRequest

logger = logging.getLogger(__name__)

_graph_cache: dict[str, object] = {}

# asyncio only holds a weak reference to a task created via create_task — if
# nothing else references it, it can be GC'd mid-execution. Keep a strong ref
# here until done, per the stdlib docs' own recommendation for fire-and-forget tasks.
_background_tasks: set[asyncio.Task] = set()


def _fire_and_forget(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# doc §3.7 — Workflow Memory needs a task_type to filter on, but that's only
# known once the planner decides to call a data tool, which is *after* the
# point retrieval needs to happen. No table_name-style "task type taxonomy"
# step exists yet (see docs/STM_LTM_Memory_Implementation.md), so task_type
# is inferred from which data tools the agent has configured.
_DATA_TOOL_TASK_TYPE = {
    "text_to_sql":    "sql_query",
    "text_to_pandas": "report_gen",
}

_SKIP_TRACE_NODES = frozenset({"LangGraph", "join_retrieval", "join_end"})

def _skip_trace(name: str) -> bool:
    return name in _SKIP_TRACE_NODES or name.startswith(("_route", "route_"))


async def run_conversation_stream(req: RunRequest, request: Request):
    tenant_id = request.headers.get("X-Tenant-ID", _DEV_TENANT_ID)
    cfg = await load_agent_config(req.agent_id, tenant_id=tenant_id)
    if cfg is None:
        async def _err():
            yield "data: [ERROR] Agent chưa được publish.\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")
    agent_id = cfg.get("agent_id", _DEV_AGENT_ID)

    cache_key = cfg.get("agent_version_id") or req.agent_id
    if cache_key not in _graph_cache:
        _graph_cache[cache_key] = build_graph(
            cfg.get("flow_nodes") or None,
            cfg.get("flow_edges") or None,
        )
    graph = _graph_cache[cache_key]
    cfg["tenant_id"] = tenant_id
    # doc §3.7 — gates which Schema Memory tables _text_to_sql_handler may
    # see. Kong-style convention: comma-separated roles already verified
    # upstream (see tests/integration/conftest.py headers_factory).
    cfg["user_roles"] = [r.strip() for r in request.headers.get("X-User-Roles", "").split(",") if r.strip()]
    if not await validate_agent_tenant(agent_id, tenant_id):
        return JSONResponse(status_code=403, content={"detail": "Agent not found or access denied"})

    user_ref = request.headers.get("X-User-ID", "anonymous")

    memory_context = []
    if cfg.get("memory_enabled"):
        memory_context = await retrieve_memories(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_ref=user_ref,
            query=req.query,
        )
        if not req.conversation_id:
            # New session — surface due reminders + recent episodic history once (doc §IV).
            reminders, episodic_past = await asyncio.gather(
                retrieve_pending_reminders(tenant_id=tenant_id, agent_id=agent_id, user_ref=user_ref),
                retrieve_episodic_past(tenant_id=tenant_id, agent_id=agent_id, user_ref=user_ref),
            )
            memory_context = [
                {**r, "memory_type": "episodic_future"} for r in reminders
            ] + [
                {**e, "memory_type": "episodic_past"} for e in episodic_past
            ] + memory_context

    workflow_memory_context = []
    data_task_type = next(
        (_DATA_TOOL_TASK_TYPE[t] for t in cfg.get("allowed_tools", []) if t in _DATA_TOOL_TASK_TYPE),
        None,
    )
    if data_task_type:
        workflow_memory_context = await retrieve_workflow_memory(
            tenant_id=tenant_id,
            agent_id=agent_id,
            task_type=data_task_type,
            query=req.query,
        )

    # Load history: Redis → DB → client fallback
    if req.conversation_id:
        history = await cache_get_messages(req.conversation_id)
        if history is None:
            history = await get_messages(req.conversation_id, tenant_id=tenant_id)
            if history:
                await cache_set_messages(req.conversation_id, history)
        history = history or [m.model_dump() for m in req.messages]
    else:
        history = [m.model_dump() for m in req.messages]

    conv_id = req.conversation_id or await create_conversation(
        agent_version_id=cfg.get("agent_version_id", None),
        user_ref=user_ref,
        channel=request.headers.get("X-Channel", "web"),
        tenant_id=tenant_id,
    )

    state = {
        "query": req.query,
        "original_query": None,
        "messages": history,
        "memory_context": memory_context,
        "workflow_memory_context": workflow_memory_context,
        "tool_calls": [],
        "kb_chunks": [],
        "mcp_results": [],
        "tool_results": [],
        "tool_errors": [],
        "loop_iteration": 0,
        "judge_verdict": None,
        "judge_error_type": None,
        "judge_reason": None,
        "rrf_results": [],
        "reranked_chunks": [],
        "response": "",
        "suggestions": [],
        "guardrail_triggered": False,
        "guardrail_message": "",
        "guardrail_stage": None,
        "guardrail_reason": None,
        "intent": None,
        "language": None,
        "config": cfg,
    }

    handler = get_langfuse_handler(
        run_name=f"conv-{conv_id or 'new'}",
        session_id=conv_id,
        user_id=user_ref,
        metadata={
            "agent_id": agent_id,
            "agent_version_id": cfg.get("agent_version_id"),
            "tenant_id": tenant_id,
            "channel": request.headers.get("X-Channel", "web"),
            "responder_model": cfg.get("responder_model"),
            "planner_model": cfg.get("planner_model"),
        },
    )
    callbacks = [handler] if handler else []

    _OPTIONS_OPEN  = "<OPTIONS>"
    _OPTIONS_CLOSE = "</OPTIONS>"
    _THINK_OPEN    = "<think>"
    _THINK_CLOSE   = "</think>"

    async def event_stream():
        full_response: list[str] = []
        guardrail_triggered = False
        guardrail_msg = ""
        guardrail_stage = None
        guardrail_reason = None
        # selective buffering để chặn <OPTIONS> tag khỏi frontend
        pending = ""           # giữ tạm token cuối để detect prefix của <OPTIONS>
        options_mode = False   # True khi đã qua <OPTIONS>
        options_buffer = ""    # nội dung sau <OPTIONS>
        think_mode = False     # True khi đang trong <think>...</think>
        think_buffer = ""      # buffer think content (bị discard)
        started_at = time.monotonic()

        pending_suggestions: list[str] | None = None

        # per-node trace tracking
        node_start_times: dict[str, float] = {}
        node_traces: list[dict] = []
        detected_language: str | None = None
        detected_intent: str | None = None

        try:
            async for event in graph.astream_events(state, version="v2", config={"callbacks": callbacks}):
                kind = event["event"]
                node = event.get("metadata", {}).get("langgraph_node", "")

                if kind == "on_chain_start" and node and not _skip_trace(node):
                    node_start_times[node] = time.monotonic()

                elif kind == "on_chain_end" and node and not _skip_trace(node):
                    if node in node_start_times:
                        node_lat = int((time.monotonic() - node_start_times.pop(node)) * 1000)
                        out = event["data"].get("output", {})
                        inp = event["data"].get("input", {})
                        node_traces.append({
                            "tool_name": node,
                            "input":     inp if isinstance(inp, dict) else {},
                            "output":    out if isinstance(out, dict) else {},
                            "latency_ms": node_lat,
                        })
                        # Expand per-tool spans for tool_executor
                        if node == "tool_executor" and isinstance(out, dict):
                            for tt in out.get("_tool_traces", []):
                                node_traces.append({
                                    "tool_name": f"tool/{tt['tool_name']}",
                                    "input":     {"query": tt.get("query", ""), "arguments": tt.get("arguments", {})},
                                    "output":    {"result_id": tt.get("result_id"), "error": tt.get("error")},
                                    "latency_ms": tt["latency_ms"],
                                    "status":     tt.get("status", "success"),
                                })
                    # node-specific logic
                    if node == "guardrail_input":
                        out = event["data"].get("output", {})
                        if isinstance(out, dict) and out.get("guardrail_triggered"):
                            guardrail_triggered = True
                            guardrail_msg    = out.get("guardrail_message", "")
                            guardrail_stage  = out.get("guardrail_stage")
                            guardrail_reason = out.get("guardrail_reason")
                    elif node == "suggestion":
                        out = event["data"].get("output", {})
                        if isinstance(out, dict):
                            topics = out.get("suggestions", [])
                            if topics:
                                pending_suggestions = topics
                    elif node == "planner":
                        out = event["data"].get("output", {})
                        if isinstance(out, dict):
                            detected_language = out.get("language")
                            detected_intent   = out.get("intent")

                elif kind == "on_chat_model_stream" and node == "responder":
                    token = event["data"]["chunk"].content or ""
                    if not token:
                        continue

                    # Strip <think>...</think> — discard toàn bộ thinking block
                    if think_mode:
                        think_buffer += token
                        if _THINK_CLOSE in think_buffer:
                            token = think_buffer.split(_THINK_CLOSE, 1)[1]
                            think_mode = False
                            think_buffer = ""
                        else:
                            continue
                        if not token:
                            continue
                    elif _THINK_OPEN in (pending + token):
                        combined = pending + token
                        idx = combined.find(_THINK_OPEN)
                        before = combined[:idx]
                        if before:
                            full_response.append(before)
                            yield f"data: {json.dumps(before)}\n\n"
                        pending = ""
                        think_mode = True
                        think_buffer = combined[idx + len(_THINK_OPEN):]
                        continue

                    if options_mode:
                        options_buffer += token
                    else:
                        pending += token
                        tag_pos = pending.find(_OPTIONS_OPEN)
                        if tag_pos != -1:
                            # flush text trước tag, bắt đầu buffer options
                            before = pending[:tag_pos]
                            if before:
                                full_response.append(before)
                                yield f"data: {json.dumps(before)}\n\n"
                            options_mode = True
                            options_buffer = pending[tag_pos + len(_OPTIONS_OPEN):]
                            pending = ""
                        else:
                            # flush phần chắc chắn không phải prefix của <OPTIONS>
                            max_hold = len(_OPTIONS_OPEN) - 1
                            flush_to = len(pending)
                            for hold in range(min(max_hold, len(pending)), 0, -1):
                                if _OPTIONS_OPEN.startswith(pending[-hold:]):
                                    flush_to = len(pending) - hold
                                    break
                            to_send = pending[:flush_to]
                            if to_send:
                                full_response.append(to_send)
                                yield f"data: {json.dumps(to_send)}\n\n"
                            pending = pending[flush_to:]

            # flush pending còn lại (không phải <OPTIONS>)
            if pending:
                full_response.append(pending)
                yield f"data: {json.dumps(pending)}\n\n"

            # parse responder options nếu có
            responder_options: list[str] | None = None
            if options_mode:
                close_pos = options_buffer.find(_OPTIONS_CLOSE)
                if close_pos != -1:
                    try:
                        parsed = json.loads(options_buffer[:close_pos].strip())
                        if isinstance(parsed, list) and parsed:
                            responder_options = [str(o) for o in parsed[:3]]
                    except (json.JSONDecodeError, Exception):
                        pass
                # fallback: </OPTIONS> bị thiếu (LLM cắt giữa chừng / max_tokens hit)
                if not responder_options:
                    fallback = options_buffer.replace(_OPTIONS_CLOSE, "").strip()
                    if fallback:
                        full_response.append(fallback)
                        yield f"data: {json.dumps(fallback)}\n\n"

            if guardrail_triggered:
                yield f"data: {json.dumps('Xin lỗi, tôi không thể trả lời câu hỏi này.')}\n\n"
            elif responder_options:
                yield f"data: {json.dumps({'__options': responder_options})}\n\n"
            elif pending_suggestions:
                yield f"data: {json.dumps({'__suggestions': pending_suggestions})}\n\n"

            yield "data: [DONE]\n\n"

        except Exception:
            logger.exception("event_stream error")
            yield "data: [ERROR] Đã xảy ra lỗi, vui lòng thử lại.\n\n"
        finally:
            latency = int((time.monotonic() - started_at) * 1000)
            assistant_text = guardrail_msg if guardrail_triggered else "".join(full_response)

            if conv_id:
                await save_message(conv_id, "user", req.query)
                assistant_msg_id = await save_message(
                    conv_id,
                    "assistant",
                    assistant_text,
                    latency_ms=latency,
                    metadata={
                        "guardrail": {
                            "triggered": guardrail_triggered,
                            "stage": guardrail_stage,
                            "reason": guardrail_reason,
                        },
                        "language": detected_language,
                        "intent": detected_intent,
                        "responder_model": cfg.get("responder_model"),
                    },
                )
                await cache_append_messages(conv_id, [
                    {"role": "user",      "content": req.query},
                    {"role": "assistant", "content": assistant_text},
                ])
                if assistant_msg_id:
                    if node_traces:
                        for idx, nt in enumerate(node_traces):
                            await save_trace(
                                message_id=assistant_msg_id,
                                trace_index=idx,
                                tool_name=nt["tool_name"],
                                input=nt["input"],
                                output=nt["output"],
                                latency_ms=nt["latency_ms"],
                                status=nt.get("status", "success"),
                            )
                    else:
                        await save_trace(
                            message_id=assistant_msg_id,
                            trace_index=0,
                            tool_name="responder",
                            input={"query": req.query},
                            output={"response": assistant_text, "guardrail_triggered": guardrail_triggered},
                            latency_ms=latency,
                        )

            if cfg.get("memory_enabled") and not guardrail_triggered and conv_id:
                final_state = {**state, "response": assistant_text}
                await apply_memory_policy(
                    final_state, agent_id, tenant_id, user_ref,
                    conversation_id=conv_id, message_id=assistant_msg_id,
                )

            # Workflow Memory write (doc §3.7) — one record per data-tool call
            # this turn actually executed, regardless of success/failure.
            for nt in node_traces:
                if not nt["tool_name"].startswith("tool/"):
                    continue
                task_type = _DATA_TOOL_TASK_TYPE.get(nt["tool_name"][len("tool/"):])
                if not task_type:
                    continue
                _fire_and_forget(record_workflow_outcome(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    task_type=task_type,
                    task_description=nt["input"].get("query") or "",
                    param_snapshot=nt["input"].get("arguments") or {},
                    outcome="success" if nt.get("status", "success") == "success" else "failed",
                    duration_ms=nt.get("latency_ms"),
                ))

    response = StreamingResponse(event_stream(), media_type="text/event-stream")
    response.headers["X-Conversation-Id"] = conv_id or ""
    return response
