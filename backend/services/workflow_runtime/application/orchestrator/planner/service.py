from __future__ import annotations

import logging
from typing import Any

from services.workflow_runtime.application.orchestrator import config

from .dpts import LLM, MockLLM, Plan, PlanningProcess, StaticMemoryEstimator

logger = logging.getLogger(__name__)


def _build_llm() -> LLM | MockLLM:
    if config.USE_MOCK_PLANNER:
        return MockLLM(plan_depth=config.MAX_PLAN_DEPTH, branching=config.BRANCHING)
    return LLM(branching=config.BRANCHING)


def _new_process() -> PlanningProcess:
    return PlanningProcess(
        _build_llm(),
        h_es=config.H_ES,
        h_ds=config.H_DS,
        branching=config.BRANCHING,
        estimator=StaticMemoryEstimator(config.QUEUE_SIZE),
        exploit_ratio=config.EXPLOIT_RATIO,
        max_plan_depth=config.MAX_PLAN_DEPTH,
    )


def restore_plan(state: dict[str, Any]) -> PlanningProcess:
    process = _new_process()
    process.load_state(state)
    return process


async def create_plan(user_input: str, agents: list[dict[str, Any]] | None = None) -> tuple[PlanningProcess, Plan] | None:
    process = _new_process()
    plan = await process.plan(user_input, agents=agents)
    if plan is None:
        return None
    logger.info("plan created | plan=%s targets=%d agents=%d",
                plan.plan_id, len(plan.targets), len(agents or []))
    return process, plan


async def report_node_failure(process: PlanningProcess, node_id: str) -> dict[str, Any]:
    """Executor reported a broken node — trim its branch and re-expand the SAME tree."""
    try:
        result = process.report_broken_node(node_id)
    except KeyError:
        return {"recovered": False, "reason": "unknown_node", "plan": None}

    new_plan: Plan | None = await process.resume()
    if new_plan is None:
        return {
            "recovered": False,
            "reason": "no_alternative_plan",
            "trimmed": result["trimmed"],
            "plan": None,
        }

    logger.info("plan recovered | new_plan=%s", new_plan.plan_id)
    return {"recovered": True, "trimmed": result["trimmed"], "plan": new_plan}
