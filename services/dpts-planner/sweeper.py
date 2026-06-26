from __future__ import annotations

import asyncio
import logging

import config
import plan_db
import plan_index

logger = logging.getLogger(__name__)


# Cached-plan TTL: erase expired plans from BOTH Mongo and Elasticsearch.
async def sweep_once() -> int:
    expired = await plan_db.find_expired()
    for plan_id in expired:
        await plan_db.delete_plan(plan_id)       # plan DB (Mongo)
        await plan_index.delete_plan(plan_id)    # Cached Plan index (Elasticsearch)
    if expired:
        logger.info("sweeper | erased %d expired plans from mongo+es", len(expired))
    return len(expired)


async def sweep_loop() -> None:
    logger.info("sweeper | started (interval=%ss, ttl=%ss)",
                config.SWEEP_INTERVAL_SECONDS, config.PLAN_TTL_SECONDS)
    while True:
        try:
            await sweep_once()
        except Exception as e:  # noqa: BLE001 — sweeper must never die
            logger.warning("sweeper | error: %s", e)
        await asyncio.sleep(config.SWEEP_INTERVAL_SECONDS)
