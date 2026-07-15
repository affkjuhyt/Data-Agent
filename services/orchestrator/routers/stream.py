from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
import asyncio
import json
import logging
import uuid
from contextlib import nullcontext

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

import config
from agent_catalog import fetch_guardrail_config
from graph import get_graph
from nodes.guardrail import run_guardrail
from observability import get_langchain_callback_handler, get_langfuse_client
from schemas import OrchestrateRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/orchestrate/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def _sse(data: Any) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.post("/api/orchestrate/stream")
async def stream_orchestration(req: OrchestrateRequest, request: Request) -> StreamingResponse:

    tenant_id = request.headers.get("X-Tenant-Id", "") or config.DEV_TENANT_ID
    # hex32 (không dấu gạch ngang) — format trace_id chuẩn OTEL, dùng chung
    # làm LangGraph thread_id VÀ Langfuse trace_id (xem observability.py).
    session_id = (req.session_id or uuid.uuid4().hex).replace("-", "")
    cfg = await fetch_guardrail_config(tenant_id)
    # Bắn nền ngay từ đầu, chạy song song với input_refine/route/planner (đều
    # thuần LLM/DB-read, không side-effect) — chỉ được await tại 2 "gate"
    # trước hành động thật (single_agent_node/execute_node, xem graph.py).
    # Input đã trọn vẹn ngay từ t=0 nên race này không đánh đổi gì về đúng-đắn,
    # chỉ ẩn latency của guardrail phía sau latency của pipeline.
    guardrail_task = asyncio.create_task(run_guardrail(req.query, cfg, req.messages))

    initial_state = {
        "query":                   req.query,
        "rewritten_query":         req.query,
        "user_id":                 req.user_id,
        "tenant_id":               tenant_id,
        "user_ref":                request.headers.get("X-User-ID", "anonymous"),
        "session_id":              session_id,
        "agent_id":                req.agent_id,
        "messages":                req.messages,
        "cfg":                     cfg,
        "guardrail_triggered":     False,
        "guardrail_reason":        "",
        "clarification_needed":    False,
        "clarification_questions": [],
        "clarification_response":  req.clarification_response,
        "agents":                  [],
        "plan":                    None,
        "process":                 None,
        "completed_node_ids":      [],
        "final_response":          "",
        "suggestions":             [],
        "target_results":          [],
        "recovered":               False,
        "success":                 False,
        "planner_ms":              0,
        "executor_ms":             0,
        "summary_ms":              0,
    }

    graph = get_graph()
    lf = get_langfuse_client()

    async def event_stream() -> AsyncIterator[str]:
        # session_id = trace_id — emit ngay để frontend fetch trace sau này
        # không phải đợi __conv_ids của từng agent con.
        yield _sse({"__session_id": session_id})

        final_response = ""
        span_ctx = lf.start_as_current_span(
            trace_context={"trace_id": session_id},
            name="orchestrate",
            input={"query": req.query, "agent_id": req.agent_id},
            metadata={"tenant_id": tenant_id, "user_id": req.user_id},
        ) if lf else nullcontext()

        with span_ctx:
            # PHẢI tạo handler ở đây, sau khi span "orchestrate" đã là OTEL
            # current span — get_langchain_callback_handler() đọc context này
            # để nối đúng cha, tạo trước sẽ khiến nó tự thành root (xem
            # observability.py).
            handler = get_langchain_callback_handler()
            lg_config = {
                "configurable": {
                    "thread_id": session_id,
                    "redis": request.app.state.redis,
                    "guardrail_task": guardrail_task,
                },
                "callbacks": [handler] if handler else [],
            }
            try:
                async for event in graph.astream_events(
                    initial_state, config=lg_config, version="v2"
                ):
                    kind = event["event"]
                    name = event.get("name", "")

                    if kind == "on_custom_event":
                        if name == "token":
                            final_response += event["data"] if isinstance(event["data"], str) else ""
                            yield _sse(event["data"])
                        elif name == "pending_review":
                            yield _sse({"__pending_review": event["data"]})
                        elif name == "chart":
                            yield _sse({"__chart": event["data"]})

            except Exception as e:
                logger.error("stream | graph error: %s", e)
                if lf:
                    await asyncio.to_thread(lf.flush)
                if not guardrail_task.done():
                    guardrail_task.cancel()
                yield _sse("Đã xảy ra lỗi, vui lòng thử lại.")
                yield "data: [DONE]\n\n"
                return
            finally:
                if not guardrail_task.done():
                    guardrail_task.cancel()

        if lf:
            await asyncio.to_thread(lf.flush)

        snapshot = await graph.aget_state(lg_config)
        if snapshot.next:
            interrupt_data = {}
            for task in snapshot.tasks:
                if hasattr(task, "interrupts") and task.interrupts:
                    interrupt_data = task.interrupts[0].value
                    break
            yield _sse({"__clarification": {
                "session_id": session_id,
                "questions":  interrupt_data.get("clarification_questions", []),
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

        # conv_ids giữ lại cho mục đích hiển thị (agent_name/title mỗi task) —
        # KHÔNG còn cần thiết để fetch trace nữa (frontend fetch 1 lần theo
        # __session_id ở trên, xem AgentPlayground.tsx).
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

    return StreamingResponse(event_stream(), media_type="text/event-stream")
