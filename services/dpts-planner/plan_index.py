from __future__ import annotations

import logging

from config import ES_INDEX, ES_MIN_SCORE, ES_URL
from dpts import Plan
from services.database_connector.elasticsearch_connector import client

logger = logging.getLogger(__name__)

_enabled = False
_INDEX = ES_INDEX


# Cached Plan index: (user_id, user_query) -> plan_id. Scoped per user.
async def init_plan_index() -> None:
    global _enabled
    if not ES_URL:
        logger.info("plan index | DPTS_ES_URL not set — Cached Plan search disabled")
        return
    try:
        client.set_url(ES_URL)
        await client.open()
        await client.ensure_index(_INDEX, {
            "user_id": {"type": "keyword"},
            "plan_id": {"type": "keyword"},
            "user_input": {"type": "text"},
        })
        _enabled = True
        logger.info("plan index | elasticsearch ready index=%s", _INDEX)
    except Exception as e:  # noqa: BLE001 — never block startup on ES outage
        logger.warning("plan index | elasticsearch unavailable: %s", e)
        _enabled = False


async def close_plan_index() -> None:
    if _enabled:
        await client.close()


async def index_plan(plan: Plan, user_id: str) -> None:
    if not _enabled:
        return
    await client.index_document(_INDEX, plan.plan_id,
                                {"user_id": user_id, "plan_id": plan.plan_id, "user_input": plan.user_input})


# Cached Plan: does this user already have a plan for a similar query?
async def search_existing(user_input: str, user_id: str) -> str | None:
    if not _enabled:
        return None
    hit = await client.search_top_match(
        _INDEX, "user_input", user_input, min_score=ES_MIN_SCORE, filters={"user_id": user_id}
    )
    return hit["plan_id"] if hit else None


async def delete_plan(plan_id: str) -> None:
    if _enabled:
        await client.delete_document(_INDEX, plan_id)
