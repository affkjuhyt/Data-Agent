import base64
import logging
import os
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, List

from app.pipeline.workflow_runtime.config_loader import load_agent_config
from app.pipeline.workflow_runtime.graph import build_graph
from app.pipeline.workflow_runtime.services.observability import get_langfuse_handler
from app.pipeline.workflow_runtime.services.memory import (
    retrieve_memories, apply_memory_policy,
    _DEV_AGENT_ID, _DEV_TENANT_ID,
)

from services.database_connector.postgres_connector import client as pg_client
from services.database_connector.mongo_connector import client as mongo_client

logger = logging.getLogger(__name__)

router = APIRouter()

_graph_cache: dict[str, object] = {}
_default_graph = build_graph()


class Message(BaseModel):
    role: str
    content: str


class RunRequest(BaseModel):
    query: str
    agent_id: str
    conversation_id: Optional[str] = None
    messages: List[Message] = []


@router.post("/conversations/run")
async def run_conversation(req: RunRequest, request: Request):
    cfg      = await load_agent_config(req.agent_id)
    agent_id = cfg.get("agent_id", _DEV_AGENT_ID)

    flow_nodes = cfg.get("flow_nodes") or []
    if flow_nodes:
        if req.agent_id not in _graph_cache:
            _graph_cache[req.agent_id] = build_graph(flow_nodes)
        _graph = _graph_cache[req.agent_id]
    else:
        _graph = _default_graph

    tenant_id = request.headers.get("X-Tenant-ID", _DEV_TENANT_ID)

    if not await pg_client.validate_agent_tenant(agent_id, tenant_id):
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

    conv_id = req.conversation_id or await pg_client.create_conversation(
        agent_version_id=cfg.get("agent_version_id", None),
        user_ref=user_ref,
        channel=request.headers.get("X-Channel", "web"),
        tenant_id=tenant_id,
    )

    state = {
        "query": req.query,
        "messages": [m.model_dump() for m in req.messages],
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
            "agent_id":        agent_id,
            "tenant_id":       tenant_id,
            "responder_model": cfg.get("responder_model"),
            "planner_model":   cfg.get("planner_model"),
        },
    )
    callbacks = [handler] if handler else []

    has_output_guardrail = bool(cfg.get("guardrail_output_patterns"))

    async def event_stream():
        full_response: list[str] = []
        guardrail_triggered = False
        guardrail_msg = ""
        guardrail_stage = None
        guardrail_reason = None
        t0 = time.monotonic()
        try:
            async for event in _graph.astream_events(state, version="v2", config={"callbacks": callbacks}):
                kind = event["event"]
                node = event.get("metadata", {}).get("langgraph_node", "")

                if kind == "on_chain_end" and node in ("guardrail_input", "guardrail_output"):
                    output = event["data"].get("output", {})
                    if isinstance(output, dict) and output.get("guardrail_triggered"):
                        guardrail_triggered = True
                        guardrail_msg    = output.get("guardrail_message", "")
                        guardrail_stage  = output.get("guardrail_stage")
                        guardrail_reason = output.get("guardrail_reason")

                elif kind == "on_chat_model_stream" and node == "responder":
                    token = event["data"]["chunk"].content or ""
                    if token:
                        full_response.append(token)
                        if not has_output_guardrail:
                            yield f"data: {token}\n\n"

            if guardrail_triggered:
                yield f"data: {guardrail_msg}\n\n"
            elif has_output_guardrail:
                combined = "".join(full_response)
                if combined:
                    yield f"data: {combined}\n\n"

            yield "data: [DONE]\n\n"

        except Exception as e:
            logger.exception("event_stream error: %s", e)
            yield "data: [ERROR] Đã xảy ra lỗi, vui lòng thử lại.\n\n"
        finally:
            latency = int((time.monotonic() - t0) * 1000)
            assistant_text = guardrail_msg if guardrail_triggered else "".join(full_response)
            if conv_id:
                await pg_client.save_message(conv_id, "user", req.query)
                asst_msg_id = await pg_client.save_message(
                    conv_id, "assistant", assistant_text, latency_ms=latency,
                    metadata={
                        "guardrail": {
                            "triggered": guardrail_triggered,
                            "stage":     guardrail_stage,
                            "reason":    guardrail_reason,
                        }
                    },
                )
                if asst_msg_id:
                    await pg_client.save_trace(
                        message_id=asst_msg_id,
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


@router.get("/traces/{conv_id}")
async def get_traces_by_conv(conv_id: str):
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key  = os.environ.get("LANGFUSE_SECRET_KEY")
    host        = os.environ.get("LANGFUSE_HOST", "http://localhost:3001")

    if not public_key or not secret_key:
        return JSONResponse(status_code=503, content={"detail": "Langfuse not configured"})

    token        = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    auth_headers = {"Authorization": f"Basic {token}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{host}/api/public/traces",
                params={"sessionId": conv_id},
                headers=auth_headers,
            )
            r.raise_for_status()
            traces = r.json().get("data", [])

            if not traces:
                return {"observations": []}

            trace_id = traces[0]["id"]
            r2 = await client.get(
                f"{host}/api/public/observations",
                params={"traceId": trace_id},
                headers=auth_headers,
            )
            r2.raise_for_status()
            return {"observations": r2.json().get("data", [])}

    except httpx.HTTPStatusError as e:
        logger.error("langfuse proxy HTTP error: %s", e.response.status_code)
        return JSONResponse(status_code=502, content={"detail": f"Langfuse HTTP {e.response.status_code}"})
    except Exception as e:
        logger.exception("langfuse proxy error: %s", e)
        return JSONResponse(status_code=502, content={"detail": "Langfuse unavailable"})
