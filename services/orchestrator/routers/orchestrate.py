from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, HTTPException, Request

import config
from agent_catalog import fetch_published_agents
from executor import execute_plan
from observability import get_langfuse_client
from planner import create_plan, report_node_failure
from schemas import OrchestrateRequest, OrchestrateResponse
from synthesizer import synthesize

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_REPLAN = 2  # tối đa retry sau khi replan


@router.post("/api/orchestrate/run", response_model=OrchestrateResponse)
async def run_orchestration(req: OrchestrateRequest, request: Request):
    """
    Luồng chính:
    1. Fetch published agents từ catalog (theo tenant)
    2. Planner tạo plan (ToT) — biết được agents để assign đúng
    3. Executor chạy plan
    4. Nếu fail → report planner → nhận plan mới → retry
    5. Synthesizer gom outputs → final response
    """
    tenant_id = request.headers.get("X-Tenant-Id", "") or config.DEV_TENANT_ID
    session_id = str(uuid.uuid4())

    # LangFuse trace cho toàn bộ orchestration
    lf = get_langfuse_client()
    lf_trace = lf.trace(
        name="orchestrator-run",
        session_id=session_id,
        user_id=req.user_id,
        input={"query": req.query},
    ) if lf else None

    # Step 1: fetch catalog
    agents = await fetch_published_agents(tenant_id)
    if not agents:
        logger.warning("orchestrate | no published agents found for tenant=%s", tenant_id)

    # Step 2: tạo plan
    t_plan = time.monotonic()
    planner_span = lf_trace.span(name="orchestrator-planner", input={"query": req.query}) if lf_trace else None
    plan = await create_plan(req.user_id, req.query, agents=agents)
    planner_ms = round((time.monotonic() - t_plan) * 1000)
    if plan is None:
        if planner_span: planner_span.end(level="ERROR")
        if lf_trace: lf_trace.update(output="Planner failed"); lf.flush()  # type: ignore[union-attr]
        raise HTTPException(422, "Planner không thể tạo plan cho query này.")
    if planner_span:
        planner_span.end(output={"plan_id": plan.plan_id, "targets": len(plan.targets)})

    logger.info("orchestrate | user=%s plan=%s targets=%d", req.user_id, plan.plan_id, len(plan.targets))

    all_target_results = []
    final_outputs: list[str] = []
    recovered = False

    t_exec = time.monotonic()
    for attempt in range(_MAX_REPLAN + 1):
        result = await execute_plan(plan, user_id=req.user_id, original_query=req.query, tenant_id=tenant_id, agents=agents, lf_trace=lf_trace)
        all_target_results.extend(result.target_results)

        if result.success:
            final_outputs = result.all_outputs
            break

        # Task fail — thử replan
        if attempt < _MAX_REPLAN and result.failed_node_id:
            logger.info("orchestrate | replan attempt=%d failed_node=%s", attempt + 1, result.failed_node_id)
            recovery = await report_node_failure(req.user_id, plan.plan_id, result.failed_node_id)
            if recovery.get("recovered") and recovery.get("plan"):
                plan = recovery["plan"]
                recovered = True
                continue

        # Không thể recover → dùng outputs đã có
        final_outputs = result.all_outputs
        break
    executor_ms = round((time.monotonic() - t_exec) * 1000)

    # Step 3: Synthesizer
    t_synth = time.monotonic()
    synth_span = lf_trace.span(name="orchestrator-synthesizer", input={"query": req.query, "outputs_count": len(final_outputs)}) if lf_trace else None
    final_response = await synthesize(req.query, final_outputs)
    synthesizer_ms = round((time.monotonic() - t_synth) * 1000)
    if synth_span:
        synth_span.end(output=final_response[:2000])

    if lf_trace:
        lf_trace.update(output=final_response[:2000])
        lf.flush()  # type: ignore[union-attr]

    return OrchestrateResponse(
        plan_id=plan.plan_id,
        response=final_response,
        target_results=all_target_results,
        recovered=recovered,
        planner_ms=planner_ms,
        executor_ms=executor_ms,
        synthesizer_ms=synthesizer_ms,
        session_id=session_id,
    )


@router.get("/api/orchestrate/health")
async def health():
    return {"status": "ok"}
