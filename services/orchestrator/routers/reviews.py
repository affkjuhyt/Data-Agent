from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from langgraph.types import Command

from graph import get_graph
from schemas import ClarificationResume, OrchestrateResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/orchestrate/resume", response_model=OrchestrateResponse)
async def resume_orchestration(req: ClarificationResume):
    """
    Resume orchestration sau khi user cung cấp clarification.
    session_id là thread_id của LangGraph checkpointer.
    """
    graph = get_graph()
    lg_config = {"configurable": {"thread_id": req.session_id}}

    # Kiểm tra graph có đang bị interrupt không
    snapshot = await graph.aget_state(lg_config)
    if not snapshot.next:
        raise HTTPException(404, "Session không tồn tại hoặc đã hoàn thành.")

    # Resume graph với clarification từ user
    result = await graph.ainvoke(
        Command(resume=req.clarification, update={"clarification_response": req.clarification}),
        config=lg_config,
    )

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
        synthesizer_ms=result.get("synthesizer_ms", 0),
        session_id=req.session_id,
    )
