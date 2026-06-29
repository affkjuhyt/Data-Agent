from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Request
from langgraph.types import Command

import config
from graph import get_graph
from schemas import OrchestrateRequest, OrchestrateResponse

logger = logging.getLogger(__name__)
router = APIRouter()

_CFG: dict = {}  # global config cho guardrail/rewrite/suggestion/clarification models


@router.post("/api/orchestrate/run", response_model=OrchestrateResponse)
async def run_orchestration(req: OrchestrateRequest, request: Request):
    tenant_id = request.headers.get("X-Tenant-Id", "") or config.DEV_TENANT_ID
    user_ref  = request.headers.get("X-User-ID", "anonymous")
    session_id = req.session_id or str(uuid.uuid4())

    initial_state = {
        "query":                 req.query,
        "rewritten_query":       req.query,
        "user_id":               req.user_id,
        "tenant_id":             tenant_id,
        "user_ref":              user_ref,
        "session_id":            session_id,
        "agent_id":              req.agent_id,
        "messages":              list(req.messages),
        "cfg":                   _CFG,
        "guardrail_triggered":   False,
        "guardrail_reason":      "",
        "clarification_needed":  False,
        "clarification_questions": [],
        "clarification_response": None,
        "agents":                [],
        "plan":                  None,
        "final_response":        "",
        "suggestions":           [],
        "target_results":        [],
        "recovered":             False,
        "success":               False,
        "planner_ms":            0,
        "executor_ms":           0,
        "synthesizer_ms":        0,
    }

    graph = get_graph()
    lg_config = {"configurable": {"thread_id": session_id}}

    result = await graph.ainvoke(initial_state, config=lg_config)

    # Kiểm tra interrupt (HITL — input_refine cần hỏi user)
    snapshot = await graph.aget_state(lg_config)
    if snapshot.next:
        # Graph bị interrupt tại input_refine node
        interrupt_data = {}
        for task in snapshot.tasks:
            if hasattr(task, "interrupts") and task.interrupts:
                interrupt_data = task.interrupts[0].value
                break

        return OrchestrateResponse(
            plan_id=session_id,
            response="",
            success=False,
            needs_clarification=True,
            clarification_questions=interrupt_data.get("clarification_questions", []),
            session_id=session_id,
        )

    # Guardrail blocked
    if result.get("guardrail_triggered"):
        return OrchestrateResponse(
            plan_id=session_id,
            response="Xin lỗi, tôi không thể trả lời câu hỏi này.",
            success=False,
            session_id=session_id,
        )

    # Plan failed
    if not result.get("success") and not result.get("final_response"):
        raise HTTPException(422, "Không thể xử lý yêu cầu này.")

    return OrchestrateResponse(
        plan_id=result.get("plan").plan_id if result.get("plan") else session_id,
        response=result.get("final_response", ""),
        success=result.get("success", False),
        target_results=result.get("target_results", []),
        recovered=result.get("recovered", False),
        suggestions=result.get("suggestions", []),
        planner_ms=result.get("planner_ms", 0),
        executor_ms=result.get("executor_ms", 0),
        synthesizer_ms=result.get("synthesizer_ms", 0),
        session_id=session_id,
    )


@router.get("/api/orchestrate/health")
async def health():
    return {"status": "ok"}
