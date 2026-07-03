from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

import config
from graph import get_graph
from schemas import OrchestrateRequest, OrchestrateResponse

logger = logging.getLogger(__name__)
router = APIRouter()

_CFG: dict = {}


def _sse(data) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.post("/api/orchestrate/stream")
async def stream_orchestration(req: OrchestrateRequest, request: Request):
    tenant_id = request.headers.get("X-Tenant-Id", "") or config.DEV_TENANT_ID
    session_id = req.session_id or str(uuid.uuid4())

    initial_state = {
        "query":                   req.query,
        "rewritten_query":         req.query,
        "user_id":                 req.user_id,
        "tenant_id":               tenant_id,
        "user_ref":                request.headers.get("X-User-ID", "anonymous"),
        "session_id":              session_id,
        "agent_id":                req.agent_id,
        "messages":                req.messages,
        "cfg":                     _CFG,
        "guardrail_triggered":     False,
        "guardrail_reason":        "",
        "clarification_needed":    False,
        "clarification_questions": [],
        "clarification_response":  req.clarification_response,
        "agents":                  [],
        "plan":                    None,
        "final_response":          "",
        "suggestions":             [],
        "target_results":          [],
        "recovered":               False,
        "success":                 False,
        "planner_ms":              0,
        "executor_ms":             0,
        "synthesizer_ms":          0,
    }

    graph = get_graph()
    lg_config = {"configurable": {"thread_id": session_id}}

    async def event_stream():
        try:
            async for event in graph.astream_events(
                initial_state, config=lg_config, version="v2"
            ):
                kind  = event["event"]
                name  = event.get("name", "")

                if kind == "on_custom_event":
                    if name == "token":
                        yield _sse(event["data"])
                    elif name == "pending_review":
                        yield _sse({"__pending_review": event["data"]})

        except Exception as e:
            logger.error("stream | graph error: %s", e)
            yield _sse("Đã xảy ra lỗi, vui lòng thử lại.")
            yield "data: [DONE]\n\n"
            return

        # Sau khi graph chạy xong — kiểm tra interrupt
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

        # Lấy final state để emit suggestions và check guardrail
        final_state = snapshot.values
        if final_state.get("guardrail_triggered"):
            yield _sse("Xin lỗi, tôi không thể trả lời câu hỏi này.")
            yield "data: [DONE]\n\n"
            return

        suggestions = final_state.get("suggestions", [])
        if suggestions:
            yield _sse({"__suggestions": suggestions})

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
