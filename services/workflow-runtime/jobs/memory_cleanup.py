"""
Chạy hàng ngày — xóa AgentMemories đã hết hạn/soft-deleted quá lâu, và áp dụng
retention + cap cho EpisodicMemory theo MemoryRetentionConfig (per tenant+domain,
doc §3.3) thay cho một giá trị RETENTION_DAYS toàn cục.
Dùng: python jobs/memory_cleanup.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv, find_dotenv
from sqlalchemy import delete, func, select, text

from services.database_connector.postgres_connector import client
from basemodel.services_databaseconnector.postgres_orm.workflow_orm import AgentMemoriesORM, EpisodicMemoryORM

load_dotenv(find_dotenv())

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Housekeeping window for already soft-deleted AgentMemories tombstones — not
# part of the doc's per-domain retention model, just how long to keep them
# around before hard delete.
SOFT_DELETE_RETENTION_DAYS = int(os.environ.get("MEMORY_RETENTION_DAYS", "90"))
BATCH_SIZE = int(os.environ.get("MEMORY_CLEANUP_BATCH_SIZE", "1000"))

# Fallback when a tenant+domain has no MemoryRetentionConfig row (doc §3.3 default).
DEFAULT_EPISODIC_MAX_PER_USER = 500


async def _delete_in_batches(model, where_clause, label: str) -> int:
    total = 0
    while True:
        async with client.get_client() as session:
            subq = select(model.id).where(where_clause).limit(BATCH_SIZE)
            result = await session.execute(delete(model).where(model.id.in_(subq)))
            deleted = result.rowcount
            await session.commit()
        total += deleted
        logger.info("%s — batch deleted %d rows (total so far: %d)", label, deleted, total)
        if deleted < BATCH_SIZE:
            break
    return total


async def _cleanup_agent_memories():
    async with client.get_client() as session:
        counts = await session.execute(
            select(AgentMemoriesORM.tenant_id, func.count().label("cnt"))
            .where(text(
                f"(deleted_at IS NOT NULL AND deleted_at < NOW() - INTERVAL '{SOFT_DELETE_RETENTION_DAYS} days') "
                "OR (expires_at IS NOT NULL AND expires_at < NOW())"
            ))
            .group_by(AgentMemoriesORM.tenant_id)
        )
        for row in counts.fetchall():
            logger.info("AgentMemories — tenant=%s — %d records pending cleanup", row.tenant_id, row.cnt)

    soft_deleted = await _delete_in_batches(
        AgentMemoriesORM,
        text(f"deleted_at IS NOT NULL AND deleted_at < NOW() - INTERVAL '{SOFT_DELETE_RETENTION_DAYS} days'"),
        "AgentMemories soft-deleted",
    )
    logger.info("AgentMemories soft-deleted cleanup complete — total: %d", soft_deleted)

    expired = await _delete_in_batches(
        AgentMemoriesORM,
        text("expires_at IS NOT NULL AND expires_at < NOW()"),
        "AgentMemories expired",
    )
    logger.info("AgentMemories expired cleanup complete — total: %d", expired)


async def _cleanup_episodic_memory():
    """doc §3.3 — retention via MemoryRetentionConfig (expires_at set by the
    memory-consumer at write time); cap eviction archives the lowest-importance
    rows per (tenant, agent, user_ref, domain) when over max_records_per_user."""
    expired = await _delete_in_batches(
        EpisodicMemoryORM,
        text("expires_at IS NOT NULL AND expires_at < NOW()"),
        "EpisodicMemory expired",
    )
    logger.info("EpisodicMemory expired cleanup complete — total: %d", expired)

    async with client.get_client() as session:
        groups = (await session.execute(text("""
            SELECT em.tenant_id, em.agent_id, em.user_ref, em.domain, COUNT(*) AS cnt,
                   COALESCE(rc.max_records_per_user, :default_cap) AS cap
            FROM "EpisodicMemory" em
            LEFT JOIN "MemoryRetentionConfig" rc
              ON rc.tenant_id = em.tenant_id AND rc.domain = em.domain
            WHERE em.status = 'active'
            GROUP BY em.tenant_id, em.agent_id, em.user_ref, em.domain, rc.max_records_per_user
            HAVING COUNT(*) > COALESCE(rc.max_records_per_user, :default_cap)
        """), {"default_cap": DEFAULT_EPISODIC_MAX_PER_USER})).fetchall()

        archived_total = 0
        for g in groups:
            over_by = g.cnt - g.cap
            result = await session.execute(text("""
                UPDATE "EpisodicMemory" SET status = 'archived'
                WHERE id IN (
                    SELECT id FROM "EpisodicMemory"
                    WHERE tenant_id = :tenant_id AND agent_id = :agent_id
                      AND user_ref IS NOT DISTINCT FROM :user_ref AND domain = :domain AND status = 'active'
                    ORDER BY importance_score ASC, created_at ASC
                    LIMIT :over_by
                )
            """), {
                "tenant_id": g.tenant_id, "agent_id": g.agent_id,
                "user_ref": g.user_ref, "domain": g.domain, "over_by": over_by,
            })
            archived_total += result.rowcount
        await session.commit()
        logger.info("EpisodicMemory cap eviction — archived %d rows across %d over-cap groups", archived_total, len(groups))


async def run():
    url = os.environ["DATABASE_URL"]
    client.set_url(url)
    await client.open()

    try:
        await _cleanup_agent_memories()
        await _cleanup_episodic_memory()
    except Exception:
        logger.exception("memory_cleanup failed")
        raise
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
