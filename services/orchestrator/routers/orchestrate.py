from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Request

import config
from agent_catalog import fetch_published_agents
from executor import execute_plan
from executor.agent_client import call_agent
from nodes.guardrail import run_guardrail
from nodes.rewrite import run_rewrite
from nodes.suggestion import run_suggestion
from observability import get_langfuse_client
from planner import create_plan, report_node_failure
from schemas import OrchestrateRequest, OrchestrateResponse, TargetResult, TaskResult
from synthesizer import synthesize

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_REPLAN = 2
_GUARDRAIL_CFG: dict = {}  # global guardrail config (env-driven nếu cần)


@router.post("/api/orchestrate/run", response_model=OrchestrateResponse)
async def run_orchestration(req: OrchestrateRequest, request: Request):
    tenant_id = request.headers.get("X-Tenant-Id", "") or config.DEV_TENANT_ID
    user_ref  = request.headers.get("X-User-ID", "anonymous")
    session_id = req.session_id or str(uuid.uuid4())

    lf = get_langfuse_client()
    lf_trace = lf.trace(
        name="orchestrator-run",
        session_id=session_id,
        user_id=req.user_id,
        input={"query": req.query, "mode": "single" if req.agent_id else "multi"},
    ) if lf else None

    # ── Step 1: Guardrail (1 lần cho cả 2 mode) ──────────────────────────────
    triggered, reason = await run_guardrail(req.query, _GUARDRAIL_CFG)
    if triggered:
        logger.info("orchestrate | guardrail triggered: %s", reason)
        if lf_trace:
            lf_trace.update(output="guardrail_blocked")
            lf.flush()  # type: ignore[union-attr]
        return OrchestrateResponse(
            plan_id="",
            response="Xin lỗi, tôi không thể trả lời câu hỏi này.",
            success=False,
            session_id=session_id,
        )

    # ── Step 2: Rewrite (1 lần cho cả 2 mode) ────────────────────────────────
    rewritten_query = await run_rewrite(req.query, [], _GUARDRAIL_CFG)

    # ── Route: single agent hay multi agent ───────────────────────────────────
    if req.agent_id:
        return await _run_single_agent(
            req=req,
            rewritten_query=rewritten_query,
            user_ref=user_ref,
            tenant_id=tenant_id,
            session_id=session_id,
            lf_trace=lf_trace,
            lf=lf,
        )
    else:
        return await _run_multi_agent(
            req=req,
            rewritten_query=rewritten_query,
            user_ref=user_ref,
            tenant_id=tenant_id,
            session_id=session_id,
            lf_trace=lf_trace,
            lf=lf,
        )


async def _run_single_agent(
    req: OrchestrateRequest,
    rewritten_query: str,
    user_ref: str,
    tenant_id: str,
    session_id: str,
    lf_trace,
    lf,
) -> OrchestrateResponse:
    """Single agent mode: gọi thẳng agent, skip ToT planner."""
    t0 = time.monotonic()
    try:
        response_text, conversation_id = await call_agent(
            agent_id=req.agent_id,
            prompt=rewritten_query,
            user_id=req.user_id,
            tenant_id=tenant_id,
            # orchestrated=False → agent tự lo memory (single agent mode)
        )
        success = True
    except Exception as e:
        logger.warning("orchestrate | single agent failed: %s", e)
        response_text = "Đã xảy ra lỗi, vui lòng thử lại."
        conversation_id = None
        success = False

    latency_ms = round((time.monotonic() - t0) * 1000)

    suggestions = await run_suggestion(req.query, [], response_text, _GUARDRAIL_CFG)

    if lf_trace:
        lf_trace.update(output=response_text[:2000])
        lf.flush()  # type: ignore[union-attr]

    task_result = TaskResult(
        task_index=0,
        task_title=rewritten_query[:100],
        agent_id=req.agent_id,
        output=response_text,
        success=success,
        conversation_id=conversation_id,
        latency_ms=latency_ms,
    )
    target_result = TargetResult(
        node_id="single",
        description=rewritten_query[:100],
        task_results=[task_result],
        success=success,
    )

    return OrchestrateResponse(
        plan_id=session_id,
        response=response_text,
        success=success,
        target_results=[target_result],
        suggestions=suggestions,
        executor_ms=latency_ms,
        session_id=session_id,
    )


async def _run_multi_agent(
    req: OrchestrateRequest,
    rewritten_query: str,
    user_ref: str,
    tenant_id: str,
    session_id: str,
    lf_trace,
    lf,
) -> OrchestrateResponse:
    """Multi agent mode: ToT planner → execute (orchestrated=True) → synthesize → suggestion."""
    agents = await fetch_published_agents(tenant_id)
    if not agents:
        logger.warning("orchestrate | no published agents found for tenant=%s", tenant_id)

    # Plan
    t_plan = time.monotonic()
    planner_span = lf_trace.span(name="orchestrator-planner", input={"query": rewritten_query}) if lf_trace else None
    plan = await create_plan(req.user_id, rewritten_query, agents=agents)
    planner_ms = round((time.monotonic() - t_plan) * 1000)

    if plan is None:
        if planner_span: planner_span.end(level="ERROR")
        if lf_trace: lf_trace.update(output="Planner failed"); lf.flush()  # type: ignore[union-attr]
        raise HTTPException(422, "Planner không thể tạo plan cho query này.")
    if planner_span:
        planner_span.end(output={"plan_id": plan.plan_id, "targets": len(plan.targets)})

    logger.info("orchestrate | user=%s plan=%s targets=%d", req.user_id, plan.plan_id, len(plan.targets))

    # Execute với orchestrated=True → agents skip memory
    all_target_results = []
    final_outputs: list[str] = []
    recovered = False
    overall_success = False
    completed_node_ids: set[str] = set()

    t_exec = time.monotonic()
    for attempt in range(_MAX_REPLAN + 1):
        result = await execute_plan(
            plan,
            user_id=req.user_id,
            original_query=rewritten_query,
            tenant_id=tenant_id,
            agents=agents,
            lf_trace=lf_trace,
            skip_node_ids=completed_node_ids,
            orchestrated=True,
        )
        all_target_results.extend(result.target_results)
        completed_node_ids.update(tr.node_id for tr in result.target_results if tr.success)

        if result.success:
            final_outputs = result.all_outputs
            overall_success = True
            break

        if attempt < _MAX_REPLAN and result.failed_node_id:
            logger.info("orchestrate | replan attempt=%d failed_node=%s", attempt + 1, result.failed_node_id)
            recovery = await report_node_failure(req.user_id, plan.plan_id, result.failed_node_id)
            if recovery.get("recovered") and recovery.get("plan"):
                plan = recovery["plan"]
                recovered = True
                continue

        final_outputs = result.all_outputs
        break
    executor_ms = round((time.monotonic() - t_exec) * 1000)

    # Dedup target results
    seen: dict[str, int] = {}
    for i, tr in enumerate(all_target_results):
        seen[tr.node_id] = i
    all_target_results = [all_target_results[i] for i in sorted(seen.values())]

    # Synthesize
    t_synth = time.monotonic()
    synth_span = lf_trace.span(name="orchestrator-synthesizer", input={"query": rewritten_query, "outputs_count": len(final_outputs)}) if lf_trace else None
    final_response = await synthesize(rewritten_query, final_outputs)
    synthesizer_ms = round((time.monotonic() - t_synth) * 1000)
    if synth_span:
        synth_span.end(output=final_response[:2000])

    # Suggestion từ final response (1 lần, thay vì N lần ở mỗi agent)
    suggestions = await run_suggestion(req.query, [], final_response, _GUARDRAIL_CFG)

    if lf_trace:
        lf_trace.update(output=final_response[:2000])
        lf.flush()  # type: ignore[union-attr]

    return OrchestrateResponse(
        plan_id=plan.plan_id,
        response=final_response,
        success=overall_success,
        target_results=all_target_results,
        recovered=recovered,
        suggestions=suggestions,
        planner_ms=planner_ms,
        executor_ms=executor_ms,
        synthesizer_ms=synthesizer_ms,
        session_id=session_id,
    )


@router.get("/api/orchestrate/health")
async def health():
    return {"status": "ok"}
