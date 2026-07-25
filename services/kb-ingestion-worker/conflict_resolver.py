"""
Conflict Resolution Loop
========================
Resolution is BATCH-level: a batch is anchored on one existing text block and
holds every incoming chunk that conflicts with it. The user resolves the whole
batch at once; the resolver executes it and only writes a new text-block version
once every conflict in the batch is resolved.

Postgres is the index — which conflicts a batch has, their status, the chosen
method. Mongo holds the batch's working text and each conflict's chunk payload.

Batch status flow:
  PENDING   — conflicts saved during ingestion; user reviews in UI
  AWAITING  — user submitted per-conflict methods; resolver picks it up
  RESOLVED  — batch executed; new version written (merge) or existing kept

Rules:
  keep_existing  — allowed only when ALL conflicts choose it (existing block kept)
  keep_incoming  — allowed only when exactly one chooses it and the rest keep_existing
  merge          — merge incoming chunks into the block one by one (see _apply_merge)
"""
from __future__ import annotations

from typing import Any
import asyncio
import datetime
import uuid as _uuid
from dataclasses import dataclass

from qdrant_client import models as qmodels
from sqlalchemy import delete as sa_delete, func as sa_func, select, update as sa_update

from libs.basemodel.services_databaseconnector.postgres_model import (
    ConflictResolution,
    ConflictStatus,
)
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBConflictBatchORM,
    KBConflictORM,
    KBTextBlockORM,
    KBTextBlockVersionORM,
    KBTextTableORM,
)
from libs.database_connector.model_connector import ModelServiceClient
from libs.database_connector.postgres_connector import PostgresClient
from libs.database_connector.qdrant_connector import QdrantClient as QdrantService

from gold import (
    _CRITERION_CONFLICT_TYPE,
    _CRITERION_SEVERITY,
    _commit_chunk_atomic,
    canonicalize_chunk_entities,
    decide_keep_table,
    detect_pairwise_conflict,
    fetch_active_block_version,
    remap_chunk_relationships,
    set_conflict_batch_status,
)
from silver import summarize_chunk
from utils import config as cfg
from utils import conflict_store
from utils import llm_client
from utils.graph_store import GraphStore
from utils.log_writer import IngestionLogger
from utils.prompts import MERGE_SYSTEM
from utils.stage_chain import StageChain


# ── Constants ─────────────────────────────────────────────────────────────────

POLL_INTERVAL = int(cfg.__dict__.get("CONFLICT_POLL_INTERVAL", 30))  # seconds
AWAITING_BATCH_SCAN_LIMIT = 50
SUMMARY_FALLBACK_CHARS = 200

_KEEP_EXISTING = ConflictResolution.KEEP_EXISTING.value
_KEEP_INCOMING = ConflictResolution.KEEP_INCOMING.value
_MERGE = ConflictResolution.MERGE.value
_RESOLVED = ConflictStatus.RESOLVED.value

MODE_ALL_KEEP_EXISTING = "all_keep_existing"
MODE_SINGLE_KEEP_INCOMING = "single_keep_incoming"
MODE_MERGE = "merge"
MODE_INVALID = "invalid"


# ── Models ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ResolverStores:
    """The stores every resolution path reads and writes."""
    postgres: PostgresClient
    qdrant: Any
    graph: GraphStore
    model: ModelServiceClient
    mongo: Any


@dataclass(frozen=True, slots=True)
class Conflict:
    """One conflict's index row; its chunk lives in Mongo under the same conflict_id."""
    conflict_id: str
    status: str
    resolution_method: str | None
    resolution_instruction: str | None
    from_recheck: bool
    detected_at: datetime.datetime | None


@dataclass(frozen=True, slots=True)
class BatchContext:
    """One batch under resolution."""
    batch_id: str
    tenant_id: str
    knowledge_base: str
    existing_block_id: str
    resolved_by: str
    conflicts: list[Conflict]


@dataclass(frozen=True, slots=True)
class MergedBlock:
    """Merged content and everything derived from it, before any store is written."""
    content: str
    summary: str
    embedding: list[float]
    payload: dict
    graph_entities: list[dict]
    relationships: list[dict]
    keep_table: bool
    table: dict | None


# ── Shared helpers ────────────────────────────────────────────────────────────

def _conflict_ids(conflicts: list[Conflict]) -> list[str]:
    """Every conflict_id in the given records."""
    return [conflict.conflict_id for conflict in conflicts]


def _ids_with_method(conflicts: list[Conflict], method: str) -> list[str]:
    """Every conflict_id whose chosen method is `method`."""
    return [conflict.conflict_id for conflict in conflicts if conflict.resolution_method == method]


async def _claim_batch(postgres: PostgresClient, batch_id: str) -> bool:
    """Flip an AWAITING batch to PENDING"""
    async with postgres.get_client() as session:
        try:
            result = await session.execute(
                sa_update(KBConflictBatchORM)
                .where(KBConflictBatchORM.batch_id == _uuid.UUID(batch_id))
                .where(KBConflictBatchORM.status == ConflictStatus.AWAITING.value)
                .values(status=ConflictStatus.PENDING.value)
            )
            await session.commit()
            return (result.rowcount or 0) == 1
        except Exception:
            await session.rollback()
            raise


async def _load_batch_anchor(postgres: PostgresClient, batch_id: str) -> str | None:
    """The block a batch is anchored on."""
    async with postgres.get_client() as session:
        block_id = await session.scalar(
            select(KBConflictBatchORM.existing_block_id)
            .where(KBConflictBatchORM.batch_id == _uuid.UUID(batch_id))
        )
    return str(block_id) if block_id else None


async def _load_batch_conflicts(postgres: PostgresClient, batch_id: str) -> tuple[list[Conflict], str]:
    """Read a batch's conflict records: (conflicts, resolved_by)."""
    async with postgres.get_client() as session:
        rows = (await session.execute(
            select(KBConflictORM)
            .where(KBConflictORM.batch_id == _uuid.UUID(batch_id))
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
    """True while any conflict in the batch is still unresolved."""
    async with postgres.get_client() as session:
        open_count = await session.scalar(
            select(sa_func.count())
            .select_from(KBConflictORM)
            .where(KBConflictORM.batch_id == _uuid.UUID(batch_id))
            .where(KBConflictORM.status != _RESOLVED)
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
                .where(KBConflictORM.conflict_id.in_([_uuid.UUID(cid) for cid in conflict_ids]))
                .values(status=ConflictStatus.PENDING.value, resolved_at=None)
            )
            await session.commit()
        except Exception:
            await session.rollback()


async def _close_out_conflicts(postgres: PostgresClient, conflict_ids: list[str], stage_record: StageChain) -> None:
    """Mark the given conflicts RESOLVED."""
    if not conflict_ids:
        return
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBConflictORM)
                .where(KBConflictORM.conflict_id.in_([_uuid.UUID(cid) for cid in conflict_ids]))
                .values(
                    status=_RESOLVED,
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


async def _mark_batch_resolved(stores: ResolverStores, ctx: BatchContext, stage_record: StageChain) -> None:
    """Close the batch, then shed its dead state; cleanup must never undo the resolve."""
    await set_conflict_batch_status(stores.postgres, ctx.batch_id, _RESOLVED)
    stage_record.push(
        f"batch_marked_resolved_{ctx.batch_id}",
        rollback_func=set_conflict_batch_status,
        rollback_args=(stores.postgres, ctx.batch_id, ConflictStatus.AWAITING.value),
    )
    try:
        await conflict_store.strip_replayed_payloads(
            stores.mongo, ctx.tenant_id, _conflict_ids(ctx.conflicts),
        )
        await conflict_store.drop_batch_working_text(stores.mongo, ctx.batch_id)
    except Exception:
        pass


async def _return_batch_to_user(postgres: PostgresClient, batch_id: str) -> None:
    """Regroup every still-AWAITING conflict as PENDING so the user can re-resolve."""
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBConflictORM)
                .where(KBConflictORM.batch_id == _uuid.UUID(batch_id))
                .where(KBConflictORM.status == ConflictStatus.AWAITING.value)
                .values(status=ConflictStatus.PENDING.value)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    await set_conflict_batch_status(postgres, batch_id, ConflictStatus.PENDING.value)


async def _fetch_block_point(qdrant, block_id: str):
    """The block's live Qdrant point (vector + payload). Raises when it is gone."""
    points = await qdrant.retrieve(
        collection_name=cfg.QDRANT_COLLECTION, ids=[block_id], with_payload=True, with_vectors=True,
    )
    if not points:
        raise RuntimeError(f"block {block_id} has no Qdrant point")
    return points[0]


def _classify_batch_resolution(conflicts: list[Conflict]) -> str:
    """The batch's resolution mode, or MODE_INVALID when the selection breaks the rules."""
    methods = [conflict.resolution_method for conflict in conflicts]
    if not methods or any(method is None for method in methods):
        return MODE_INVALID
    distinct_methods = set(methods)
    if distinct_methods == {_KEEP_EXISTING}:
        return MODE_ALL_KEEP_EXISTING
    if methods.count(_KEEP_INCOMING) == 1 and distinct_methods <= {_KEEP_INCOMING, _KEEP_EXISTING}:
        return MODE_SINGLE_KEEP_INCOMING
    if _MERGE in distinct_methods and _KEEP_INCOMING not in distinct_methods:
        return MODE_MERGE
    return MODE_INVALID


# ── keep_existing ─────────────────────────────────────────────────────────────

async def _apply_keep_existing(stores: ResolverStores, ctx: BatchContext, stage_record: StageChain) -> None:
    """Keep the existing block: nothing was committed, so only close the records out."""
    await _close_out_conflicts(stores.postgres, _conflict_ids(ctx.conflicts), stage_record)
    await _mark_batch_resolved(stores, ctx, stage_record)


# ── keep_incoming ─────────────────────────────────────────────────────────────

async def _rollback_mark_payload_committed(mongo, tenant_id: str, conflict_id: str) -> None:
    try:
        await conflict_store.mark_payload_committed(mongo, tenant_id, conflict_id, None)
    except Exception:
        pass


async def _commit_incoming_chunk(
        stores: ResolverStores, ctx: BatchContext, payload: dict, stage_record: StageChain,
) -> str:
    """Commit an incoming chunk as a block and add its graph edges; returns its block_id.

    Idempotent per conflict: _commit_chunk_atomic mints a NEW block id every call, so a
    retry after a later stage failed must re-use the block the first attempt committed
    (recorded on the payload) instead of committing the same chunk a second time.
    """
    embed_model_id = payload.get("embed_model_id") or cfg.EMBED_MODEL_ID
    data_id = payload["data_id"]
    entities = await canonicalize_chunk_entities(
        stores.qdrant, stores.postgres, stores.model, ctx.tenant_id, payload.get("entities") or [],
        knowledge_base=ctx.knowledge_base, embed_model_id=embed_model_id,
    )

    block_id = payload.get("committed_block_id")
    if block_id is None:
        block_id = await _commit_chunk_atomic(
            postgres=stores.postgres,
            qdrant=stores.qdrant,
            tenant_id=ctx.tenant_id,
            data_id=data_id,
            block_index=payload["block_index"],
            content=payload["content"],
            table_involved=payload.get("table_involved", False),
            table=payload.get("table"),
            payload_meta={
                "summary": payload["summary"],
                "entities": entities.canonicals,
                "embedding_model_id": embed_model_id,
                "knowledge_base": ctx.knowledge_base,
            },
            embedding=payload["embedding"],
            summary=payload["summary"],
            approved_by=payload.get("approved_by", "system"),
            embed_model_id=embed_model_id,
            knowledge_base=ctx.knowledge_base,
            entities=entities.canonicals,
            entity_type_fields=entities.qdrant_type_fields(),
            stage_record=stage_record,
        )
        await conflict_store.mark_payload_committed(
            stores.mongo, ctx.tenant_id, payload["conflict_id"], block_id,
        )
        stage_record.push(
            f"conflict_payload_marked_committed_{payload['conflict_id']}",
            rollback_func=_rollback_mark_payload_committed,
            rollback_args=(stores.mongo, ctx.tenant_id, payload["conflict_id"]),
        )

    # MERGE/on-conflict-do-nothing.
    await stores.graph.add_chunk(
        tenant_id=ctx.tenant_id,
        knowledge_base=ctx.knowledge_base,
        data_id=data_id,
        block_id=block_id,
        entities=entities.graph_nodes(),
        relationships=remap_chunk_relationships(payload.get("relationships"), entities.raw_to_canonical),
        embed_model_id=embed_model_id,
    )
    stage_record.push(
        f"conflict_chunk_graph_updated_{block_id}",
        rollback_func=stores.graph.remove_chunk,
        rollback_kwargs={"tenant_id": ctx.tenant_id, "knowledge_base": ctx.knowledge_base, "block_id": block_id},
    )
    return block_id


async def _set_block_deleted(postgres, block_id: str, is_deleted: bool) -> None:
    """Flip a block's Postgres is_deleted flag."""
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBTextBlockORM)
                .where(KBTextBlockORM.block_id == _uuid.UUID(block_id))
                .values(is_deleted=is_deleted)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _rollback_soft_delete_block(postgres, qdrant, block_id: str) -> None:
    try:
        await _set_block_deleted(postgres, block_id, False)
    except Exception:
        pass
    try:
        await qdrant.set_payload(
            collection_name=cfg.QDRANT_COLLECTION, payload={"is_deleted": False}, points=[block_id],
        )
    except Exception:
        pass


async def _soft_delete_block(stores: ResolverStores, ctx: BatchContext, block_id: str, stage_record: StageChain) -> None:
    """Soft-delete a block across Postgres, Qdrant, and the graph.

    Postgres goes first and both flags are undone if a later store fails, so the block
    is never hidden from search while Postgres still lists it as live.
    """
    await _set_block_deleted(stores.postgres, block_id, True)
    try:
        await stores.qdrant.set_payload(
            collection_name=cfg.QDRANT_COLLECTION, payload={"is_deleted": True}, points=[block_id],
        )
        await stores.graph.remove_chunk(
            tenant_id=ctx.tenant_id, knowledge_base=ctx.knowledge_base, block_id=block_id,
        )
    except Exception:
        try:
            await _set_block_deleted(stores.postgres, block_id, False)
            await stores.qdrant.set_payload(
                collection_name=cfg.QDRANT_COLLECTION, payload={"is_deleted": False}, points=[block_id],
            )
        except Exception:
            pass
        raise
    stage_record.push(
        f"block_soft_deleted_{block_id}",
        rollback_func=_rollback_soft_delete_block,
        rollback_args=(stores.postgres, stores.qdrant, block_id),
    )


async def _apply_keep_incoming(stores: ResolverStores, ctx: BatchContext, stage_record: StageChain) -> None:
    """Replace the existing block with the one chosen incoming chunk; discard the rest."""
    chosen = next(conflict for conflict in ctx.conflicts if conflict.resolution_method == _KEEP_INCOMING)
    payload = await conflict_store.load_payload(stores.mongo, ctx.tenant_id, chosen.conflict_id)
    if payload is None:
        raise RuntimeError(f"conflict {chosen.conflict_id} has no stored chunk")

    # Commit first, retire second: _commit_chunk_atomic writes a NEW block id and rolls
    # itself back, so a failure here leaves the existing block untouched. Retiring first
    # would drop the only copy if the commit then failed.
    await _commit_incoming_chunk(stores, ctx, payload, stage_record)
    if ctx.existing_block_id:
        await _soft_delete_block(stores, ctx, ctx.existing_block_id, stage_record)

    await _close_out_conflicts(stores.postgres, _conflict_ids(ctx.conflicts), stage_record)
    await _mark_batch_resolved(stores, ctx, stage_record)


# ── merge ─────────────────────────────────────────────────────────────────────

async def _summarize_working_content(working_content: str) -> str:
    """LLM summary of the merged working text, falling back to a truncation."""
    extraction = await summarize_chunk(working_content)
    return extraction.get("summary") or working_content[:SUMMARY_FALLBACK_CHARS]


async def _merge_contents(existing_content: str, incoming_content: str, instruction: str) -> str:
    """LLM-merge two chunk contents into one, honouring an optional instruction."""
    user_message = f"Existing chunk:\n{existing_content}\n\nIncoming chunk:\n{incoming_content}"
    if instruction:
        user_message += f"\n\nResolution instruction: {instruction}"
    return await llm_client.chat_plain(
        messages=[
            {"role": "system", "content": MERGE_SYSTEM},
            {"role": "user", "content": user_message},
        ],
        max_tokens=1024,
        temperature=0.2,
    )


async def _fetch_active_block_table(postgres, block_id: str) -> dict | None:
    """The table attached to the block's active version, or None when it has none."""
    async with postgres.get_client() as session:
        result = await session.execute(
            select(KBTextTableORM)
            .join(KBTextBlockVersionORM, KBTextTableORM.version_id == KBTextBlockVersionORM.version_id)
            .where(KBTextBlockVersionORM.block_id == _uuid.UUID(block_id))
            .where(KBTextBlockVersionORM.is_active.is_(True))
            .where(KBTextTableORM.is_deleted.is_(False))
            .limit(1)
        )
        row = result.scalars().first()
    if row is None:
        return None
    return {"table_name": row.table_name, "description": row.description, "data": row.data}


async def _reopen_conflict_after_recheck(
    stores: ResolverStores, ctx: BatchContext, *, conflict_id: str,
    scores: dict, dominant_criterion: str, stage_record: StageChain,
) -> None:
    """Reopen one conflict against the merged text, flagged so a re-approved merge forces through."""
    conflict_type = _CRITERION_CONFLICT_TYPE[dominant_criterion]
    severity = _CRITERION_SEVERITY[dominant_criterion]
    async with stores.postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBConflictORM)
                .where(KBConflictORM.conflict_id == _uuid.UUID(conflict_id))
                .values(
                    status=ConflictStatus.PENDING.value,
                    resolution_method=None,
                    conflict_type=conflict_type.value,
                    severity=severity.value,
                    from_recheck=True,
                    detailed_explanation=f"merge re-check: {dominant_criterion} ({scores[dominant_criterion]:.2f})",
                )
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    stage_record.push(f"conflict_reopened_after_recheck_{conflict_id}")


async def _build_merged_block(
    stores: ResolverStores, ctx: BatchContext, *, merged_content: str, embed_model_id: str,
    existing_table: dict | None, data_id: str,
) -> MergedBlock:
    """Derive summary, entities, embedding, and the table decision from merged content."""
    extraction = await summarize_chunk(merged_content)
    summary = extraction.get("summary") or merged_content[:SUMMARY_FALLBACK_CHARS]
    entities = await canonicalize_chunk_entities(
        stores.qdrant, stores.postgres, stores.model, ctx.tenant_id, extraction.get("entities") or [],
        knowledge_base=ctx.knowledge_base, embed_model_id=embed_model_id,
    )
    keep_table = existing_table is not None and await decide_keep_table(merged_content)
    return MergedBlock(
        content=merged_content,
        summary=summary,
        embedding=(await stores.model.embed([merged_content], model_id=embed_model_id))[0],
        payload={
            "tenant_id": ctx.tenant_id,
            "data_id": data_id,
            "block_id": ctx.existing_block_id,
            "summary": summary,
            "entities": entities.canonicals,
            **entities.qdrant_type_fields(),
            "embedding_model_id": embed_model_id,
            "knowledge_base": ctx.knowledge_base,
            "is_deleted": False,
        },
        graph_entities=entities.graph_nodes(),
        relationships=remap_chunk_relationships(extraction.get("relationships"), entities.raw_to_canonical),
        keep_table=keep_table,
        table=existing_table if keep_table else None,
    )


async def _write_merged_version(
    postgres, ctx: BatchContext, merged: MergedBlock, current_version: int,
) -> None:
    """Write the merged content as the block's new active Postgres version."""
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBTextBlockVersionORM)
                .where(KBTextBlockVersionORM.block_id == _uuid.UUID(ctx.existing_block_id))
                .where(KBTextBlockVersionORM.is_active.is_(True))
                .values(is_active=False)
            )
            new_version = KBTextBlockVersionORM(
                block_id=_uuid.UUID(ctx.existing_block_id),
                version_number=current_version + 1,
                content=merged.content,
                created_by=ctx.resolved_by or "system",
                table_involved=merged.keep_table,
                payload=merged.payload,
                is_active=True,
            )
            session.add(new_version)
            await session.flush()
            if merged.table:
                session.add(KBTextTableORM(
                    version_id=new_version.version_id,
                    table_name=merged.table["table_name"],
                    description=merged.table["description"],
                    data=merged.table["data"],
                ))
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _revert_merged_version(postgres, ctx: BatchContext, current_version: int) -> None:
    """Drop the merged version and re-activate the previous one."""
    async with postgres.get_client() as session:
        try:
            merged_version_id = (await session.execute(
                select(KBTextBlockVersionORM.version_id)
                .where(KBTextBlockVersionORM.block_id == _uuid.UUID(ctx.existing_block_id))
                .where(KBTextBlockVersionORM.version_number == current_version + 1)
            )).scalars().first()
            if merged_version_id is not None:
                await session.execute(
                    sa_delete(KBTextTableORM).where(KBTextTableORM.version_id == merged_version_id)
                )
                await session.execute(
                    sa_delete(KBTextBlockVersionORM)
                    .where(KBTextBlockVersionORM.version_id == merged_version_id)
                )
            await session.execute(
                sa_update(KBTextBlockVersionORM)
                .where(KBTextBlockVersionORM.block_id == _uuid.UUID(ctx.existing_block_id))
                .where(KBTextBlockVersionORM.version_number == current_version)
                .values(is_active=True)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _rollback_published_merged_block(stores: ResolverStores, ctx: BatchContext, current_version: int, previous_point) -> None:
    try:
        await _revert_merged_version(stores.postgres, ctx, current_version)
    except Exception:
        pass
    try:
        await stores.qdrant.upsert(
            collection_name=cfg.QDRANT_COLLECTION,
            points=[qmodels.PointStruct(
                id=ctx.existing_block_id, vector=previous_point.vector,
                payload=previous_point.payload,
            )],
        )
    except Exception:
        pass


async def _publish_merged_block(
    stores: ResolverStores, ctx: BatchContext, *, merged_content: str, current_version: int,
    embed_model_id: str, stage_record: StageChain,
) -> None:
    """Publish merged content as the block's new active version across every store.

    The three stores cannot commit together, so Postgres goes first and is undone if a
    later one fails — never leaving a searchable point whose text Postgres lacks.
    """
    existing_table = await _fetch_active_block_table(stores.postgres, ctx.existing_block_id)
    previous_point = await _fetch_block_point(stores.qdrant, ctx.existing_block_id)
    merged = await _build_merged_block(
        stores, ctx, merged_content=merged_content, embed_model_id=embed_model_id,
        existing_table=existing_table, data_id=(previous_point.payload or {}).get("data_id", ""),
    )

    await _write_merged_version(stores.postgres, ctx, merged, current_version)
    try:
        await stores.qdrant.upsert(
            collection_name=cfg.QDRANT_COLLECTION,
            points=[qmodels.PointStruct(
                id=ctx.existing_block_id, vector=merged.embedding, payload=merged.payload,
            )],
        )
        await stores.graph.remove_chunk(
            tenant_id=ctx.tenant_id, knowledge_base=ctx.knowledge_base, block_id=ctx.existing_block_id,
        )
        await stores.graph.add_chunk(
            tenant_id=ctx.tenant_id,
            knowledge_base=ctx.knowledge_base,
            data_id=merged.payload["data_id"],
            block_id=ctx.existing_block_id,
            entities=merged.graph_entities,
            relationships=merged.relationships,
            embed_model_id=embed_model_id,
        )
    except Exception:
        try:
            await _revert_merged_version(stores.postgres, ctx, current_version)
            await stores.qdrant.upsert(
                collection_name=cfg.QDRANT_COLLECTION,
                points=[qmodels.PointStruct(
                    id=ctx.existing_block_id, vector=previous_point.vector,
                    payload=previous_point.payload,
                )],
            )
        except Exception:
            pass
        raise
    stage_record.push(
        f"merged_block_published_{ctx.existing_block_id}",
        rollback_func=_rollback_published_merged_block,
        rollback_args=(stores, ctx, current_version, previous_point),
    )


async def _merge_into_working_content(
    stores: ResolverStores, ctx: BatchContext, working_content: str, payload_by_conflict_id: dict[str, dict],
    stage_record: StageChain,
) -> tuple[str, bool]:
    """Fold each merge-marked incoming into the working text; (content, completed)."""
    pending_merges = [
        conflict for conflict in ctx.conflicts
        if conflict.resolution_method == _MERGE and conflict.status != _RESOLVED
    ]
    # Only a resolved MERGE has changed the working text; a resolved keep_existing
    # discarded its chunk, so it must not make the next merge think it has something
    # new to re-check against.
    already_merged = any(
        conflict.status == _RESOLVED and conflict.resolution_method == _MERGE
        for conflict in ctx.conflicts
    )

    for position, conflict in enumerate(pending_merges):
        payload = payload_by_conflict_id.get(conflict.conflict_id)
        if payload is None:
            raise RuntimeError(f"conflict {conflict.conflict_id} has no stored chunk")
        incoming_content = payload.get("content", "")
        # The first merge has nothing to re-check against; a merge the user re-approved
        # after a re-check (from_recheck) skips it so the batch cannot loop forever.
        skip_recheck = (position == 0 and not already_merged) or conflict.from_recheck

        if not skip_recheck:
            recheck = await detect_pairwise_conflict(working_content, incoming_content)
            if recheck is not None:
                scores, dominant_criterion = recheck
                await _reopen_conflict_after_recheck(
                    stores, ctx, conflict_id=conflict.conflict_id,
                    scores=scores, dominant_criterion=dominant_criterion, stage_record=stage_record,
                )
                # Summarize only here, where the user must judge against the merged text.
                await conflict_store.update_working_block(
                    stores.mongo, ctx.batch_id, working_content=working_content,
                    working_summary=await _summarize_working_content(working_content),
                )
                await _return_batch_to_user(stores.postgres, ctx.batch_id)
                return working_content, False

        working_content = await _merge_contents(
            working_content, incoming_content, conflict.resolution_instruction or "",
        )
        await conflict_store.update_working_block(
            stores.mongo, ctx.batch_id,
            working_content=working_content, working_summary=working_content[:SUMMARY_FALLBACK_CHARS],
        )
        await _close_out_conflicts(stores.postgres, [conflict.conflict_id], stage_record)

    return working_content, True


async def _apply_merge(stores: ResolverStores, ctx: BatchContext, stage_record: StageChain) -> None:
    """Fold the merge-marked chunks into the block, publishing once the batch is fully resolved."""
    active_version = (
        await fetch_active_block_version(stores.postgres, ctx.existing_block_id)
        if ctx.existing_block_id else None
    )
    if active_version is None:
        await _return_batch_to_user(stores.postgres, ctx.batch_id)
        return
    block_content, current_version = active_version

    batch_document = await conflict_store.load_resolution_batch(stores.mongo, ctx.batch_id)
    payload_by_conflict_id = await conflict_store.load_payloads(
        stores.mongo, ctx.tenant_id, _ids_with_method(ctx.conflicts, _MERGE),
    )
    working_content, completed = await _merge_into_working_content(
        stores, ctx, (batch_document or {}).get("working_content") or block_content,
        payload_by_conflict_id, stage_record,
    )
    if not completed:
        return

    # keep_existing conflicts share the batch — their incoming chunks are discarded.
    await _close_out_conflicts(stores.postgres, _ids_with_method(ctx.conflicts, _KEEP_EXISTING), stage_record)

    # The new version lands only when nothing in the batch is still open.
    if await _batch_has_open_conflicts(stores.postgres, ctx.batch_id):
        await set_conflict_batch_status(stores.postgres, ctx.batch_id, ConflictStatus.PENDING.value)
        return

    await _publish_merged_block(
        stores, ctx,
        merged_content=working_content,
        current_version=current_version,
        embed_model_id=next(
            (payload["embed_model_id"] for payload in payload_by_conflict_id.values()
             if payload.get("embed_model_id")),
            cfg.EMBED_MODEL_ID,
        ),
        stage_record=stage_record,
    )
    await _mark_batch_resolved(stores, ctx, stage_record)


# ── Batch resolution ──────────────────────────────────────────────────────────

async def resolve_batch(
    batch_id: str,
    postgres: PostgresClient,
    qdrant_svc: QdrantService,
    graph: GraphStore,
    model: ModelServiceClient,
    database,
) -> None:
    """Execute one batch's user-chosen resolution as a whole."""
    logger = IngestionLogger(db=database)
    stage_record = StageChain()

    if not await _claim_batch(postgres, batch_id):
        return
    stage_record.push("batch_claimed")

    stores = ResolverStores(
        postgres=postgres, qdrant=qdrant_svc.get_client(), graph=graph, model=model, mongo=database,
    )
    try:
        batch_document = await conflict_store.load_resolution_batch(database, batch_id)
        if batch_document is None:
            # PENDING, not AWAITING: a permanently missing doc left AWAITING is re-claimed
            # every poll tick forever. Surface it for a human instead of spinning.
            await logger.logging(
                f"batch_id={batch_id} has no working document — returning to PENDING", level="WARNING",
            )
            await set_conflict_batch_status(postgres, batch_id, ConflictStatus.PENDING.value)
            return

        conflicts, resolved_by = await _load_batch_conflicts(postgres, batch_id)
        ctx = BatchContext(
            batch_id=batch_id,
            tenant_id=batch_document.get("tenant_id", ""),
            knowledge_base=batch_document.get("knowledge_base", ""),
            existing_block_id=await _load_batch_anchor(postgres, batch_id) or "",
            resolved_by=resolved_by,
            conflicts=conflicts,
        )
        stage_record.push("batch_loaded")

        mode = _classify_batch_resolution(ctx.conflicts)
        if mode == MODE_ALL_KEEP_EXISTING:
            await _apply_keep_existing(stores, ctx, stage_record)
        elif mode == MODE_SINGLE_KEEP_INCOMING:
            await _apply_keep_incoming(stores, ctx, stage_record)
        elif mode == MODE_MERGE:
            await _apply_merge(stores, ctx, stage_record)
        else:
            # Back to PENDING, not AWAITING: only a new human selection can fix this,
            # and AWAITING is what the poll loop re-claims — it would spin forever.
            await logger.logging(
                f"batch_id={batch_id} invalid resolution selection — returning to PENDING", level="WARNING",
            )
            await _return_batch_to_user(postgres, batch_id)
            return
        stage_record.push("resolution_applied")
    except Exception as exc:
        # Transient (LLM/store) failure — AWAITING so the poll loop retries it. Each
        # path leaves its stores consistent before raising, so the retry is safe.
        await stage_record.unwind()
        await logger.logging(
            f"batch resolution failed batch_id={batch_id}: {exc} — reverting to AWAITING", level="ERROR",
        )
        try:
            await set_conflict_batch_status(postgres, batch_id, ConflictStatus.AWAITING.value)
        except Exception:
            # The claim already moved it off AWAITING, so nothing will retry it now.
            await logger.logging(
                f"batch_id={batch_id} STRANDED as {ConflictStatus.PENDING.value} — revert to AWAITING failed",
                level="CRITICAL",
            )
        return

    await logger.logging(f"batch_id={batch_id} resolved (mode={mode})")


async def fetch_awaiting_batch_ids(postgres: PostgresClient) -> list[str]:
    """Ids of batches whose users have submitted resolutions (AWAITING)."""
    async with postgres.get_client() as session:
        result = await session.execute(
            select(KBConflictBatchORM.batch_id)
            .where(KBConflictBatchORM.status == ConflictStatus.AWAITING.value)
            .where(KBConflictBatchORM.is_deleted.is_(False))
            .order_by(KBConflictBatchORM.created_at)
            .limit(AWAITING_BATCH_SCAN_LIMIT)
        )
        return [str(batch_id) for batch_id in result.scalars().all()]


async def conflict_resolve_loop(
    postgres: PostgresClient,
    qdrant_svc: QdrantService,
    graph: GraphStore,
    model: ModelServiceClient,
    mongo,
) -> None:
    """Background loop that polls for AWAITING batches and resolves them."""
    database = mongo.get_client()
    while True:
        try:
            batch_ids = await fetch_awaiting_batch_ids(postgres)
            for batch_id in batch_ids:
                await resolve_batch(batch_id, postgres, qdrant_svc, graph, model, database)
        except Exception:
            pass

        await asyncio.sleep(POLL_INTERVAL)
