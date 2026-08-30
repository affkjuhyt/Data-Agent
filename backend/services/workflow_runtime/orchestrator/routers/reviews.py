from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext

from fastapi import APIRouter, HTTPException
from langgraph.types import Command

from orchestrator.graph import get_graph
from observability import get_langchain_callback_handler, get_langfuse_client
from orchestrator.schemas import ClarificationResume, OrchestrateResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/orchestrate/resume", response_model=OrchestrateResponse)
async def resume_orchestration(req: ClarificationResume) -> OrchestrateResponse:
    """
    Resume orchestration sau khi user cung cấp clarification.
    session_id là thread_id của LangGraph checkpointer VÀ Langfuse trace_id —
    request resume này tiếp tục CÙNG 1 trace đã mở ở /api/orchestrate/stream.
    """
    graph = get_graph()
    lf = get_langfuse_client()
    lg_config_base = {"configurable": {"thread_id": req.session_id}}

    # Kiểm tra graph có đang bị interrupt không
    snapshot = await graph.aget_state(lg_config_base)
    if not snapshot.next:
        raise HTTPException(404, "Session không tồn tại hoặc đã hoàn thành.")

    span_ctx = lf.start_as_current_span(
        trace_context={"trace_id": req.session_id},
        name="orchestrate-resume",
        input={"clarification": req.clarification},
    ) if lf else nullcontext()

    # Resume graph với clarification từ user
    with span_ctx:
        # PHẢI tạo handler ở đây, sau khi span đã là OTEL current span (xem
        # lý do tương tự trong routers/stream.py).
        handler = get_langchain_callback_handler()
        lg_config = {**lg_config_base, "callbacks": [handler] if handler else []}
        result = await graph.ainvoke(
            Command(resume=req.clarification, update={"clarification_response": req.clarification}),
            config=lg_config,
        )
    if lf:
        await asyncio.to_thread(lf.flush)

    if result.get("guardrail_triggered"):
        return OrchestrateResponse(
            plan_id=req.session_id,
            response="Xin lỗi, tôi không thể trả lời câu hỏi này.",
            success=False,
            session_id=req.session_id,
        )

    if not result.get("success") and not result.get("final_response"):
        raise HTTPException(422, "Không thể xử lý yêu cầu này.")

    return OrchestrateResponse(
        plan_id=result.get("plan").plan_id if result.get("plan") else req.session_id,
        response=result.get("final_response", ""),
        success=result.get("success", False),
        target_results=result.get("target_results", []),
        recovered=result.get("recovered", False),
        suggestions=result.get("suggestions", []),
        planner_ms=result.get("planner_ms", 0),
        executor_ms=result.get("executor_ms", 0),
        summary_ms=result.get("summary_ms", 0),
        session_id=req.session_id,
    )
