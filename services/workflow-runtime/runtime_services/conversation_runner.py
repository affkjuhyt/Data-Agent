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
    retrieve_memories,
)
from observability import get_langfuse_handler
from schemas import RunRequest

logger = logging.getLogger(__name__)

_graph_cache: dict[str, object] = {}


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
        "tool_calls": [],
        "kb_chunks": [],
        "mcp_results": [],
        "rrf_results": [],
        "reranked_chunks": [],
        "response": "",
        "suggestions": [],
        "guardrail_triggered": False,
        "guardrail_message": "",
        "guardrail_stage": None,
        "guardrail_reason": None,
        "intent": None,
        "config": cfg,
    }

    handler = get_langfuse_handler(
        run_name=f"conv-{conv_id or 'new'}",
        session_id=conv_id,
        user_id=user_ref,
        metadata={
            "agent_id": agent_id,
            "tenant_id": tenant_id,
            "responder_model": cfg.get("responder_model"),
            "planner_model": cfg.get("planner_model"),
        },
    )
    callbacks = [handler] if handler else []

    _OPTIONS_OPEN  = "<OPTIONS>"
    _OPTIONS_CLOSE = "</OPTIONS>"

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
        started_at = time.monotonic()

        pending_suggestions: list[str] | None = None

        try:
            async for event in graph.astream_events(state, version="v2", config={"callbacks": callbacks}):
                kind = event["event"]
                node = event.get("metadata", {}).get("langgraph_node", "")

                if kind == "on_chain_end" and node == "guardrail_input":
                    output = event["data"].get("output", {})
                    if isinstance(output, dict) and output.get("guardrail_triggered"):
                        guardrail_triggered = True
                        guardrail_msg = output.get("guardrail_message", "")
                        guardrail_stage = output.get("guardrail_stage")
                        guardrail_reason = output.get("guardrail_reason")

                elif kind == "on_chain_end" and node == "suggestion":
                    output = event["data"].get("output", {})
                    if isinstance(output, dict):
                        topics = output.get("suggestions", [])
                        if topics:
                            pending_suggestions = topics

                elif kind == "on_chat_model_stream" and node == "responder":
                    token = event["data"]["chunk"].content or ""
                    if not token:
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
                        }
                    },
                )
                await cache_append_messages(conv_id, [
                    {"role": "user",      "content": req.query},
                    {"role": "assistant", "content": assistant_text},
                ])
                if assistant_msg_id:
                    await save_trace(
                        message_id=assistant_msg_id,
                        trace_index=0,
                        tool_name="responder",
                        input={"query": req.query},
                        output={"response": assistant_text, "guardrail_triggered": guardrail_triggered},
                        latency_ms=latency,
                    )

            if cfg.get("memory_enabled") and not guardrail_triggered:
                final_state = {**state, "response": assistant_text}
                await apply_memory_policy(final_state, agent_id, tenant_id, user_ref)

    response = StreamingResponse(event_stream(), media_type="text/event-stream")
    response.headers["X-Conversation-Id"] = conv_id or ""
    return response
