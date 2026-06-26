from __future__ import annotations

import logging

import config
import plan_db
import plan_index
from dpts import LLM, MockLLM, Plan, PlanningProcess, StaticMemoryEstimator

logger = logging.getLogger(__name__)

# Live trees keyed by (user_id, plan_id) — isolates concurrent users and lets a
# Planner-gate failure resume the right tree.
_sessions: dict[tuple[str, str], PlanningProcess] = {}


def _build_llm():
    if config.USE_MOCK_EXPANDER:
        return MockLLM(plan_depth=config.MAX_PLAN_DEPTH, branching=config.BRANCHING)
    return LLM(branching=config.BRANCHING)  # routes through the model-service connector


def _new_process() -> PlanningProcess:
    return PlanningProcess(
        _build_llm(), h_es=config.H_ES, h_ds=config.H_DS, branching=config.BRANCHING,
        estimator=StaticMemoryEstimator(config.QUEUE_SIZE), exploit_ratio=config.EXPLOIT_RATIO,
        max_plan_depth=config.MAX_PLAN_DEPTH,
    )


# Cached Plan (ES, per-user) -> hit returns stored plan; miss runs Planning Process.
async def create_plan(user_id: str, user_input: str, use_cache: bool = True) -> dict:
    if use_cache:
        existing_id = await plan_index.search_existing(user_input, user_id)
        if existing_id:
            cached = await plan_db.get_plan(existing_id, user_id)
            if cached:
                return {"plan": cached, "cached": True}

    process = _new_process()
    plan = await process.plan(user_input)
    if plan is None:
        return {"plan": None, "cached": False}

    doc = await plan_db.store_plan(plan, user_id, config.PLAN_TTL_SECONDS)   # plan DB
    await plan_index.index_plan(plan, user_id)                               # Cached Plan index
    _sessions[(user_id, plan.plan_id)] = process
    return {"plan": doc, "cached": False}


# executor -> Planner gate: trim broken branch + delete candidates, then expand again.
async def report_node_failure(user_id: str, plan_id: str, node_id: str) -> dict:
    process = _sessions.get((user_id, plan_id))
    if process is None:
        return {"recovered": False, "reason": "no_live_session", "plan": None}
    try:
        result = process.report_broken_node(node_id)
    except KeyError:
        return {"recovered": False, "reason": "unknown_node", "plan": None}

    await plan_db.mark_status(plan_id, "failed")
    new_plan: Plan | None = await process.resume()
    if new_plan is None:
        return {"recovered": False, "reason": "no_alternative_plan",
                "trimmed": result["trimmed"], "parent_id": result["parent_id"], "plan": None}

    doc = await plan_db.store_plan(new_plan, user_id, config.PLAN_TTL_SECONDS)
    await plan_index.index_plan(new_plan, user_id)
    _sessions[(user_id, new_plan.plan_id)] = process
    return {"recovered": True, "trimmed": result["trimmed"], "parent_id": result["parent_id"], "plan": doc}


async def get_plan(user_id: str, plan_id: str) -> dict | None:
    return await plan_db.get_plan(plan_id, user_id)
