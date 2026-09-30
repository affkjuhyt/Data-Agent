"""Conflict-batch persistence and state transitions."""
from __future__ import annotations

import datetime
import uuid

from sqlalchemy import func as sa_func, select, update as sa_update

from libs.basemodel.services_databaseconnector.postgres_model import ConflictStatus
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBConflictBatchORM,
    KBConflictORM,
)
from libs.database_connector.postgres_connector import PostgresClient
from services.kb_backend.workers.conflict_resolution.models import (
    KEEP_EXISTING,
    KEEP_INCOMING,
    MERGE,
    MODE_ALL_KEEP_EXISTING,
    MODE_INVALID,
    MODE_MERGE,
    MODE_SINGLE_KEEP_INCOMING,
    RESOLVED,
    BatchContext,
    Conflict,
    ResolverStores,
)
from libs.ingestion.gold import set_conflict_batch_status
from libs.ingestion import config as cfg
from libs.ingestion import conflict_store
from libs.ingestion.stage_chain import StageChain


def _conflict_ids(conflicts: list[Conflict]) -> list[str]:
    return [conflict.conflict_id for conflict in conflicts]


def _ids_with_method(conflicts: list[Conflict], method: str) -> list[str]:
    return [conflict.conflict_id for conflict in conflicts if conflict.resolution_method == method]


async def _claim_batch(postgres: PostgresClient, batch_id: str) -> bool:
    async with postgres.get_client() as session:
        try:
            result = await session.execute(
                sa_update(KBConflictBatchORM)
                .where(KBConflictBatchORM.batch_id == uuid.UUID(batch_id))
                .where(KBConflictBatchORM.status == ConflictStatus.AWAITING.value)
                .values(status=ConflictStatus.PENDING.value)
            )
            await session.commit()
            return (result.rowcount or 0) == 1
        except Exception:
            await session.rollback()
            raise


async def _load_batch_anchor(postgres: PostgresClient, batch_id: str) -> str | None:
    async with postgres.get_client() as session:
        block_id = await session.scalar(
            select(KBConflictBatchORM.existing_block_id)
            .where(KBConflictBatchORM.batch_id == uuid.UUID(batch_id))
        )
    return str(block_id) if block_id else None


async def _load_batch_conflicts(postgres: PostgresClient, batch_id: str) -> tuple[list[Conflict], str]:
    async with postgres.get_client() as session:
        rows = (await session.execute(
            select(KBConflictORM)
            .where(KBConflictORM.batch_id == uuid.UUID(batch_id))
            .where(KBConflictORM.is_deleted.is_(False))
            .order_by(KBConflictORM.detected_at)
        )).scalars().all()
    conflicts = [
        Conflict(
            conflict_id=str(row.conflict_id),
            status=row.status,
            resolution_method=row.resolution_method,
            resolution_instruction=row.resolution_instruction,
            from_recheck=bool(row.from_recheck),
            detected_at=row.detected_at,
        )
        for row in rows
    ]
    return conflicts, next((row.resolved_by for row in rows if row.resolved_by), "system")


async def _batch_has_open_conflicts(postgres: PostgresClient, batch_id: str) -> bool:
    async with postgres.get_client() as session:
        open_count = await session.scalar(
            select(sa_func.count())
            .select_from(KBConflictORM)
            .where(KBConflictORM.batch_id == uuid.UUID(batch_id))
            .where(KBConflictORM.status != RESOLVED)
            .where(KBConflictORM.is_deleted.is_(False))
        )
    return bool(open_count)


async def _rollback_close_out_conflicts(postgres: PostgresClient, conflict_ids: list[str]) -> None:
    if not conflict_ids:
        return
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBConflictORM)
                .where(KBConflictORM.conflict_id.in_([uuid.UUID(cid) for cid in conflict_ids]))
                .values(status=ConflictStatus.PENDING.value, resolved_at=None)
            )
            await session.commit()
        except Exception:
            await session.rollback()


async def _close_out_conflicts(
    postgres: PostgresClient,
    conflict_ids: list[str],
    stage_record: StageChain,
) -> None:
    if not conflict_ids:
        return
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBConflictORM)
                .where(KBConflictORM.conflict_id.in_([uuid.UUID(cid) for cid in conflict_ids]))
                .values(
                    status=RESOLVED,
                    resolved_at=datetime.datetime.now(datetime.timezone.utc),
                )
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    stage_record.push(
        f"conflicts_closed_out_{'_'.join(conflict_ids)}",
        rollback_func=_rollback_close_out_conflicts,
        rollback_args=(postgres, conflict_ids),
    )


async def _mark_batch_resolved(
    stores: ResolverStores,
    ctx: BatchContext,
    stage_record: StageChain,
) -> None:
    await set_conflict_batch_status(stores.postgres, ctx.batch_id, RESOLVED)
    stage_record.push(
        f"batch_marked_resolved_{ctx.batch_id}",
        rollback_func=set_conflict_batch_status,
        rollback_args=(stores.postgres, ctx.batch_id, ConflictStatus.AWAITING.value),
    )
    try:
        await conflict_store.strip_replayed_payloads(
            stores.mongo,
            ctx.tenant_id,
            _conflict_ids(ctx.conflicts),
        )
        await conflict_store.drop_batch_working_text(stores.mongo, ctx.batch_id)
    except Exception:
        pass


async def _return_batch_to_user(postgres: PostgresClient, batch_id: str) -> None:
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBConflictORM)
                .where(KBConflictORM.batch_id == uuid.UUID(batch_id))
                .where(KBConflictORM.status == ConflictStatus.AWAITING.value)
                .values(status=ConflictStatus.PENDING.value)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    await set_conflict_batch_status(postgres, batch_id, ConflictStatus.PENDING.value)


async def _fetch_block_point(qdrant, block_id: str):
    points = await qdrant.retrieve(
        collection_name=cfg.QDRANT_COLLECTION,
        ids=[block_id],
        with_payload=True,
        with_vectors=True,
    )
    if not points:
        raise RuntimeError(f"block {block_id} has no Qdrant point")
    return points[0]


def _classify_batch_resolution(conflicts: list[Conflict]) -> str:
    methods = [conflict.resolution_method for conflict in conflicts]
    if not methods or any(method is None for method in methods):
        return MODE_INVALID
    distinct_methods = set(methods)
    if distinct_methods == {KEEP_EXISTING}:
        return MODE_ALL_KEEP_EXISTING
    if methods.count(KEEP_INCOMING) == 1 and distinct_methods <= {KEEP_INCOMING, KEEP_EXISTING}:
        return MODE_SINGLE_KEEP_INCOMING
    if MERGE in distinct_methods and KEEP_INCOMING not in distinct_methods:
        return MODE_MERGE
    return MODE_INVALID
