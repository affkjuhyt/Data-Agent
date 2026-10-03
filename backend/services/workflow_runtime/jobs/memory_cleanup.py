"""
Chạy hàng ngày — xóa AgentMemories đã hết hạn/soft-deleted quá lâu, và áp dụng
retention + cap cho EpisodicMemory theo MemoryRetentionConfig (per tenant+domain,
doc §3.3) thay cho một giá trị RETENTION_DAYS toàn cục.

Chạy bằng app_user (NOBYPASSRLS): các bảng memory FORCE row-level security, nên
job dọn lần lượt từng tenant, mỗi session đặt app.tenant_id của tenant đó.
Dùng: python jobs/memory_cleanup.py
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv, find_dotenv
from sqlalchemy import delete, func, select, update

from libs.database_connector.postgres_connector import client
from libs.basemodel.services_databaseconnector.postgres_orm.workflow_orm import (
    AgentMemoriesORM,
    EpisodicMemoryORM,
    MemoryRetentionConfigORM,
)
from libs.basemodel.services_databaseconnector.postgres_orm.auth_release_orm import TenantsORM

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


async def _tenant_ids() -> list[str]:
    """"Tenants" has no tenant_id column and no row-level policy: the one read
    that is not scoped to a tenant."""
    async with client.get_platform_client() as session:
        return [str(t) for t in (await session.scalars(select(TenantsORM.id).order_by(TenantsORM.id)))]


async def _delete_in_batches(model: Any, where_clause: Any, label: str, tenant_id: str) -> int:
    total = 0
    while True:
        async with client.get_client(tenant_id=tenant_id) as session:
            subq = select(model.id).where(where_clause).limit(BATCH_SIZE)
            result = await session.execute(delete(model).where(model.id.in_(subq)))
            deleted: int = result.rowcount
            await session.commit()
        total += deleted
        logger.info("%s — tenant=%s — batch deleted %d rows (total so far: %d)", label, tenant_id, deleted, total)
        if deleted < BATCH_SIZE:
            break
    return total


async def _cleanup_agent_memories(tenant_id: str) -> None:
    tombstone_cutoff = datetime.now(timezone.utc) - timedelta(days=SOFT_DELETE_RETENTION_DAYS)
    tombstone_filter = AgentMemoriesORM.deleted_at.is_not(None) & (AgentMemoriesORM.deleted_at < tombstone_cutoff)
    expired_filter = AgentMemoriesORM.expires_at.is_not(None) & (AgentMemoriesORM.expires_at < datetime.now(timezone.utc))

    async with client.get_client(tenant_id=tenant_id) as session:
        counts = await session.execute(
            select(AgentMemoriesORM.tenant_id, func.count().label("cnt"))
            .where(tombstone_filter | expired_filter)
            .group_by(AgentMemoriesORM.tenant_id)
        )
        for row in counts.fetchall():
            logger.info("AgentMemories — tenant=%s — %d records pending cleanup", row.tenant_id, row.cnt)

    soft_deleted = await _delete_in_batches(
        AgentMemoriesORM,
        tombstone_filter,
        "AgentMemories soft-deleted",
        tenant_id,
    )
    logger.info("AgentMemories soft-deleted cleanup complete — total: %d", soft_deleted)

    expired = await _delete_in_batches(
        AgentMemoriesORM,
        expired_filter,
        "AgentMemories expired",
        tenant_id,
    )
    logger.info("AgentMemories expired cleanup complete — total: %d", expired)


async def _cleanup_episodic_memory(tenant_id: str) -> None:
    """doc §3.3 — retention via MemoryRetentionConfig (expires_at set by the
    memory-consumer at write time); cap eviction archives the lowest-importance
    rows per (tenant, agent, user_ref, domain) when over max_records_per_user."""
    expired = await _delete_in_batches(
        EpisodicMemoryORM,
        EpisodicMemoryORM.expires_at.is_not(None) & (EpisodicMemoryORM.expires_at < datetime.now(timezone.utc)),
        "EpisodicMemory expired",
        tenant_id,
    )
    logger.info("EpisodicMemory expired cleanup complete — total: %d", expired)

    async with client.get_client(tenant_id=tenant_id) as session:
        cap_expr = func.coalesce(
            MemoryRetentionConfigORM.max_records_per_user,
            DEFAULT_EPISODIC_MAX_PER_USER,
        ).label("cap")
        cnt_expr = func.count().label("cnt")
        groups = (
            await session.execute(
                select(
                    EpisodicMemoryORM.tenant_id,
                    EpisodicMemoryORM.agent_id,
                    EpisodicMemoryORM.user_ref,
                    EpisodicMemoryORM.domain,
                    cnt_expr,
                    cap_expr,
                )
                .outerjoin(
                    MemoryRetentionConfigORM,
                    (MemoryRetentionConfigORM.tenant_id == EpisodicMemoryORM.tenant_id)
                    & (MemoryRetentionConfigORM.domain == EpisodicMemoryORM.domain),
                )
                .where(EpisodicMemoryORM.status == "active")
                .group_by(
                    EpisodicMemoryORM.tenant_id,
                    EpisodicMemoryORM.agent_id,
                    EpisodicMemoryORM.user_ref,
                    EpisodicMemoryORM.domain,
                    MemoryRetentionConfigORM.max_records_per_user,
                )
                .having(func.count() > cap_expr)
            )
        ).fetchall()

        archived_total = 0
        for g in groups:
            over_by = g.cnt - g.cap
            victims = (
                select(EpisodicMemoryORM.id)
                .where(
                    EpisodicMemoryORM.tenant_id == g.tenant_id,
                    EpisodicMemoryORM.agent_id == g.agent_id,
                    EpisodicMemoryORM.user_ref.is_not_distinct_from(g.user_ref),
                    EpisodicMemoryORM.domain == g.domain,
                    EpisodicMemoryORM.status == "active",
                )
                .order_by(EpisodicMemoryORM.importance_score.asc(), EpisodicMemoryORM.created_at.asc())
                .limit(over_by)
            )
            result = await session.execute(
                update(EpisodicMemoryORM).where(EpisodicMemoryORM.id.in_(victims)).values(status="archived")
            )
            archived_total += result.rowcount
        await session.commit()
        logger.info("EpisodicMemory cap eviction — archived %d rows across %d over-cap groups", archived_total, len(groups))


async def run() -> None:
    url = os.environ["DATABASE_URL"]
    client.set_url(url)
    await client.open()

    try:
        for tenant_id in await _tenant_ids():
            await _cleanup_agent_memories(tenant_id)
            await _cleanup_episodic_memory(tenant_id)
    except Exception:
        logger.exception("memory_cleanup failed")
        raise
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
