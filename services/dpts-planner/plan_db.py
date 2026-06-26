from __future__ import annotations

import logging
import time

from config import MONGODB_URL, PLANS_COLLECTION
from dpts import Plan
from services.database_connector.mongo_connector import client

logger = logging.getLogger(__name__)

_COLL = PLANS_COLLECTION


# Vertical Container -> plan DB (Mongo). Plan stored with the user query + user_id.
async def init_plan_db() -> None:
    client.set_url(MONGODB_URL)
    await client.open()
    await client.create_index(_COLL, "plan_id", unique=True)
    await client.create_index(_COLL, "user_id")
    await client.create_index(_COLL, "expires_at")


async def close_plan_db() -> None:
    await client.close()


async def store_plan(plan: Plan, user_id: str, ttl_seconds: int, status: str = "ready") -> dict:
    now = time.time()
    doc = plan.model_dump()
    doc.update(user_id=user_id, status=status, created_at=now, expires_at=now + ttl_seconds)
    await client.upsert(_COLL, {"plan_id": plan.plan_id}, doc)
    logger.info("plan DB | stored plan=%s user=%s ttl=%ss", plan.plan_id, user_id, ttl_seconds)
    return doc


async def get_plan(plan_id: str, user_id: str | None = None) -> dict | None:
    filter = {"plan_id": plan_id}
    if user_id is not None:
        filter["user_id"] = user_id
    return await client.find_one(_COLL, filter)


async def mark_status(plan_id: str, status: str) -> None:
    await client.set_fields(_COLL, {"plan_id": plan_id}, {"status": status})


# TTL sweeper support — find then erase expired plans.
async def find_expired(now: float | None = None) -> list[str]:
    rows = await client.find_many(
        _COLL, {"expires_at": {"$lt": now or time.time()}}, projection={"_id": 0, "plan_id": 1}
    )
    return [r["plan_id"] for r in rows]


async def delete_plan(plan_id: str) -> int:
    return await client.delete_one(_COLL, {"plan_id": plan_id})
