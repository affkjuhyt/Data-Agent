from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from services.workflow_runtime.application.orchestrator.planner import restore_plan
from services.workflow_runtime.application.orchestrator.planner.dpts import Plan, PlanningProcess

logger = logging.getLogger(__name__)

_CACHE_VERSION = 1
_CACHE_TTL = 1800  # seconds


def _cache_key(tenant_id: str, session_id: str) -> str:
    return f"plan_session:{tenant_id}:{session_id}"


async def load_cached_plan(
    redis: Any, tenant_id: str, session_id: str, query: str
) -> tuple[PlanningProcess, Plan, dict[str, Any]] | None:
    """Resume an in-flight ToT planning/execution session (client retried the
    same session_id, e.g. after a client-side timeout) instead of re-planning
    and re-running already-completed agent calls from scratch."""
    if redis is None or not redis.is_healthy():
        return None
    raw = await redis.get(_cache_key(tenant_id, session_id))
    if not raw:
        return None
    try:
        env = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if env.get("version") != _CACHE_VERSION or env.get("query") != query:
        return None
    try:
        process = restore_plan(env["process"])
        plan = Plan(**env["plan"])
    except Exception as e:
        logger.warning("session_cache | restore failed session=%s: %s", session_id, e)
        return None
    return process, plan, env.get("progress") or {}


async def save_cached_plan(redis: Any, tenant_id: str, session_id: str, query: str,
                            process: PlanningProcess, plan: Plan,
                            completed_node_ids: Iterable[str]) -> None:
    if redis is None or not redis.is_healthy():
        return
    env = {
        "version": _CACHE_VERSION,
        "query": query,
        "plan": plan.model_dump(mode="json"),
        "process": process.dump_state(),
        "progress": {
            "completed_node_ids": list(completed_node_ids),
        },
    }
    await redis.set(_cache_key(tenant_id, session_id), json.dumps(env, ensure_ascii=False), ttl=_CACHE_TTL)


async def clear_cached_plan(redis: Any, tenant_id: str, session_id: str) -> None:
    if redis is None or not redis.is_healthy():
        return
    await redis.delete(_cache_key(tenant_id, session_id))
