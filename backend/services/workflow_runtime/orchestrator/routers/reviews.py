from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from langgraph.types import Command

from services.workflow_runtime.auth import get_current_user
from services.workflow_runtime.observability import get_langchain_callback_handler, get_langfuse_client
from services.workflow_runtime.orchestrator.graph import get_graph
from services.workflow_runtime.orchestrator.routers.stream import load_pending_session, pending_questions
from services.workflow_runtime.orchestrator.schemas import ClarificationResume, OrchestrateResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/orchestrate/resume", response_model=OrchestrateResponse)
async def resume_orchestration(
    req: ClarificationResume,
    caller: dict[str, Any] = Depends(get_current_user),
) -> OrchestrateResponse:
    """
    Resume orchestration sau khi user cung cấp clarification.
    session_id là thread_id của LangGraph checkpointer VÀ Langfuse trace_id —
    request resume này tiếp tục CÙNG 1 trace đã mở ở /api/orchestrate/stream.

    Non-streaming: the answer comes back in one body, without charts, evidence
    or the agent's own clarification options. POST /api/orchestrate/resume/stream
    streams the same frames as /stream and is what the UI uses.
    """
    graph = await get_graph()
    lf = get_langfuse_client()
    lg_config_base = {"configurable": {"thread_id": req.session_id}}

    # Graph phải đang bị interrupt, và session phải của chính caller này.
    await load_pending_session(graph, req.session_id, caller)

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

    # Interrupted again: say so, or the client shows an empty answer and has
    # no way to reply.
    after = await graph.aget_state(lg_config_base)
    if after.next:
        return OrchestrateResponse(
            plan_id=req.session_id,
            response="",
            success=True,
            needs_clarification=True,
            clarification_questions=pending_questions(after),
            session_id=req.session_id,
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
        summary_ms=result.get("summary_ms", 0),
        session_id=req.session_id,
    )
