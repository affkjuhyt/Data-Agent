"""
Chạy hàng ngày — xóa memories đã hết hạn hoặc quá RETENTION_DAYS.
Dùng: python app/pipeline/workflow_runtime/jobs/memory_cleanup.py
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))

from dotenv import load_dotenv, find_dotenv
from sqlalchemy import delete, func, select, text

from services.database_connector.postgres_connector import client
from basemodel.services_databaseconnector.postgres_orm.workflow_orm import AgentMemoriesORM

load_dotenv(find_dotenv())

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

RETENTION_DAYS = int(os.environ.get("MEMORY_RETENTION_DAYS", "90"))
BATCH_SIZE     = int(os.environ.get("MEMORY_CLEANUP_BATCH_SIZE", "1000"))


async def _delete_in_batches(where_clause, label: str) -> int:
    total = 0
    while True:
        async with client.get_client() as session:
            subq = (
                select(AgentMemoriesORM.id)
                .where(where_clause)
                .limit(BATCH_SIZE)
            )
            result = await session.execute(
                delete(AgentMemoriesORM).where(AgentMemoriesORM.id.in_(subq))
            )
            deleted = result.rowcount
            await session.commit()
        total += deleted
        logger.info("%s — batch deleted %d rows (total so far: %d)", label, deleted, total)
        if deleted < BATCH_SIZE:
            break
    return total


async def run():
    url = os.environ["DATABASE_URL"]
    client.set_url(url)
    await client.open()

    try:
        async with client.get_client() as session:
            counts = await session.execute(
                select(AgentMemoriesORM.tenant_id, func.count().label("cnt"))
                .where(text(
                    f"(deleted_at IS NOT NULL AND deleted_at < NOW() - INTERVAL '{RETENTION_DAYS} days') "
                    "OR (expires_at IS NOT NULL AND expires_at < NOW())"
                ))
                .group_by(AgentMemoriesORM.tenant_id)
            )
            for row in counts.fetchall():
                logger.info("tenant=%s — %d records pending cleanup", row.tenant_id, row.cnt)

        soft_deleted = await _delete_in_batches(
            text(f"deleted_at IS NOT NULL AND deleted_at < NOW() - INTERVAL '{RETENTION_DAYS} days'"),
            "soft-deleted",
        )
        logger.info("cleanup complete — soft-deleted total: %d", soft_deleted)

        expired = await _delete_in_batches(
            text("expires_at IS NOT NULL AND expires_at < NOW()"),
            "expired",
        )
        logger.info("cleanup complete — expired total: %d", expired)

    except Exception:
        logger.exception("memory_cleanup failed")
        raise
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(run())
