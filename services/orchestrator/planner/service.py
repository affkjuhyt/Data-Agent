from __future__ import annotations

import logging

import config
from .dpts import LLM, MockLLM, Plan, PlanningProcess, StaticMemoryEstimator

logger = logging.getLogger(__name__)

# Live trees keyed by (user_id, plan_id) — failure recovery resumes the same tree
_sessions: dict[tuple[str, str], PlanningProcess] = {}


def _build_llm():
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


async def create_plan(user_id: str, user_input: str, agents: list[dict] | None = None) -> Plan | None:
    process = _new_process()
    plan = await process.plan(user_input, agents=agents)
    if plan is None:
        return None
    _sessions[(user_id, plan.plan_id)] = process
    logger.info("plan created | user=%s plan=%s targets=%d agents=%d",
                user_id, plan.plan_id, len(plan.targets), len(agents or []))
    return plan


async def report_node_failure(user_id: str, plan_id: str, node_id: str) -> dict:
    process = _sessions.get((user_id, plan_id))
    if process is None:
        return {"recovered": False, "reason": "no_live_session", "plan": None}
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

    _sessions[(user_id, new_plan.plan_id)] = process
    logger.info("plan recovered | user=%s new_plan=%s", user_id, new_plan.plan_id)
    return {"recovered": True, "trimmed": result["trimmed"], "plan": new_plan}
