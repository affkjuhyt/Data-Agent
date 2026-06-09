import json
import logging
import time

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from config_loader import load_agent_config
from db import create_conversation, save_message, save_trace, validate_agent_tenant
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

    conv_id = req.conversation_id or await create_conversation(
        agent_version_id=cfg.get("agent_version_id", None),
        user_ref=user_ref,
        channel=request.headers.get("X-Channel", "web"),
        tenant_id=tenant_id,
    )

    state = {
        "query": req.query,
        "messages": [message.model_dump() for message in req.messages],
        "memory_context": memory_context,
        "tool_calls": [],
        "kb_chunks": [],
        "mcp_results": [],
        "rrf_results": [],
        "reranked_chunks": [],
        "response": "",
        "guardrail_triggered": False,
        "guardrail_message": "",
        "guardrail_stage": None,
        "guardrail_reason": None,
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

    async def event_stream():
        full_response: list[str] = []
        guardrail_triggered = False
        guardrail_msg = ""
        guardrail_stage = None
        guardrail_reason = None
        started_at = time.monotonic()

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

                elif kind == "on_chat_model_stream" and node == "responder":
                    token = event["data"]["chunk"].content or ""
                    if token:
                        full_response.append(token)
                        yield f"data: {json.dumps(token)}\n\n"

            if guardrail_triggered:
                yield f"data: {json.dumps(guardrail_msg)}\n\n"

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
