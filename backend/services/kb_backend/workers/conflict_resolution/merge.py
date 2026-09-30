"""Merge-based conflict resolution strategy."""
from __future__ import annotations

import uuid
from typing import Any

from qdrant_client import models as qmodels
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update

from libs.basemodel.services_databaseconnector.postgres_model import ConflictStatus
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBConflictORM,
    KBTextBlockVersionORM,
    KBTextTableORM,
)
from libs.database_connector.postgres_connector import PostgresClient
from libs.ingestion import config as cfg
from libs.ingestion import conflict_store, llm_client
from libs.ingestion.gold import (
    _CRITERION_CONFLICT_TYPE,
    _CRITERION_SEVERITY,
    canonicalize_chunk_entities,
    decide_keep_table,
    detect_pairwise_conflict,
    fetch_active_block_version,
    remap_chunk_relationships,
    set_conflict_batch_status,
)
from libs.ingestion.prompts import MERGE_SYSTEM
from libs.ingestion.silver import summarize_chunk
from libs.ingestion.stage_chain import StageChain
from services.kb_backend.workers.conflict_resolution.models import (
    KEEP_EXISTING,
    MERGE,
    RESOLVED,
    SUMMARY_FALLBACK_CHARS,
    BatchContext,
    MergedBlock,
    ResolverStores,
)
from services.kb_backend.workers.conflict_resolution.state import (
    _batch_has_open_conflicts,
    _close_out_conflicts,
    _fetch_block_point,
    _ids_with_method,
    _mark_batch_resolved,
    _return_batch_to_user,
)


async def _summarize_working_content(working_content: str) -> str:
    extraction = await summarize_chunk(working_content)
    return extraction.get("summary") or working_content[:SUMMARY_FALLBACK_CHARS]


async def _merge_contents(existing_content: str, incoming_content: str, instruction: str) -> str:
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


async def _fetch_active_block_table(postgres: PostgresClient, block_id: str) -> dict[str, Any] | None:
    async with postgres.get_client() as session:
        result = await session.execute(
            select(KBTextTableORM)
            .join(KBTextBlockVersionORM, KBTextTableORM.version_id == KBTextBlockVersionORM.version_id)
            .where(KBTextBlockVersionORM.block_id == uuid.UUID(block_id))
            .where(KBTextBlockVersionORM.is_active.is_(True))
            .where(KBTextTableORM.is_deleted.is_(False))
            .limit(1)
        )
        row = result.scalars().first()
    if row is None:
        return None
    return {"table_name": row.table_name, "description": row.description, "data": row.data}


async def _reopen_conflict_after_recheck(
    stores: ResolverStores,
    ctx: BatchContext,
    *,
    conflict_id: str,
    scores: dict[str, Any],
    dominant_criterion: str,
    stage_record: StageChain,
) -> None:
    conflict_type = _CRITERION_CONFLICT_TYPE[dominant_criterion]
    severity = _CRITERION_SEVERITY[dominant_criterion]
    async with stores.postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBConflictORM)
                .where(KBConflictORM.conflict_id == uuid.UUID(conflict_id))
                .values(
                    status=ConflictStatus.PENDING.value,
                    resolution_method=None,
                    conflict_type=conflict_type.value,
                    severity=severity.value,
                    from_recheck=True,
                    detailed_explanation=(
                        f"merge re-check: {dominant_criterion} "
                        f"({scores[dominant_criterion]:.2f})"
                    ),
                )
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    stage_record.push(f"conflict_reopened_after_recheck_{conflict_id}")


async def _build_merged_block(
    stores: ResolverStores,
    ctx: BatchContext,
    *,
    merged_content: str,
    embed_model_id: str,
    existing_table: dict[str, Any] | None,
    data_id: str,
) -> MergedBlock:
    extraction = await summarize_chunk(merged_content)
    summary = extraction.get("summary") or merged_content[:SUMMARY_FALLBACK_CHARS]
    entities = await canonicalize_chunk_entities(
        stores.qdrant,
        stores.postgres,
        stores.model,
        ctx.tenant_id,
        extraction.get("entities") or [],
        knowledge_base=ctx.knowledge_base,
        embed_model_id=embed_model_id,
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
        relationships=remap_chunk_relationships(
            extraction.get("relationships"),
            entities.raw_to_canonical,
        ),
        keep_table=keep_table,
        table=existing_table if keep_table else None,
    )


async def _write_merged_version(
    postgres: PostgresClient,
    ctx: BatchContext,
    merged: MergedBlock,
    current_version: int,
) -> None:
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBTextBlockVersionORM)
                .where(KBTextBlockVersionORM.block_id == uuid.UUID(ctx.existing_block_id))
                .where(KBTextBlockVersionORM.is_active.is_(True))
                .values(is_active=False)
            )
            new_version = KBTextBlockVersionORM(
                block_id=uuid.UUID(ctx.existing_block_id),
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


async def _revert_merged_version(postgres: PostgresClient, ctx: BatchContext, current_version: int) -> None:
    async with postgres.get_client() as session:
        try:
            merged_version_id = (await session.execute(
                select(KBTextBlockVersionORM.version_id)
                .where(KBTextBlockVersionORM.block_id == uuid.UUID(ctx.existing_block_id))
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
                .where(KBTextBlockVersionORM.block_id == uuid.UUID(ctx.existing_block_id))
                .where(KBTextBlockVersionORM.version_number == current_version)
                .values(is_active=True)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _rollback_published_merged_block(
    stores: ResolverStores,
    ctx: BatchContext,
    current_version: int,
    previous_point: Any,
) -> None:
    try:
        await _revert_merged_version(stores.postgres, ctx, current_version)
    except Exception:
        pass
    try:
        await stores.qdrant.upsert(
            collection_name=cfg.QDRANT_COLLECTION,
            points=[qmodels.PointStruct(
                id=ctx.existing_block_id,
                vector=previous_point.vector,
                payload=previous_point.payload,
            )],
        )
    except Exception:
        pass


async def _publish_merged_block(
    stores: ResolverStores,
    ctx: BatchContext,
    *,
    merged_content: str,
    current_version: int,
    embed_model_id: str,
    stage_record: StageChain,
) -> None:
    existing_table = await _fetch_active_block_table(stores.postgres, ctx.existing_block_id)
    previous_point = await _fetch_block_point(stores.qdrant, ctx.existing_block_id)
    merged = await _build_merged_block(
        stores,
        ctx,
        merged_content=merged_content,
        embed_model_id=embed_model_id,
        existing_table=existing_table,
        data_id=(previous_point.payload or {}).get("data_id", ""),
    )

    await _write_merged_version(stores.postgres, ctx, merged, current_version)
    try:
        await stores.qdrant.upsert(
            collection_name=cfg.QDRANT_COLLECTION,
            points=[qmodels.PointStruct(
                id=ctx.existing_block_id,
                vector=merged.embedding,
                payload=merged.payload,
            )],
        )
        await stores.graph.remove_chunk(
            tenant_id=ctx.tenant_id,
            knowledge_base=ctx.knowledge_base,
            block_id=ctx.existing_block_id,
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
                    id=ctx.existing_block_id,
                    vector=previous_point.vector,
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
    stores: ResolverStores,
    ctx: BatchContext,
    working_content: str,
    payload_by_conflict_id: dict[str, dict[str, Any]],
    stage_record: StageChain,
) -> tuple[str, bool]:
    pending_merges = [
        conflict for conflict in ctx.conflicts
        if conflict.resolution_method == MERGE and conflict.status != RESOLVED
    ]
    already_merged = any(
        conflict.status == RESOLVED and conflict.resolution_method == MERGE
        for conflict in ctx.conflicts
    )

    for position, conflict in enumerate(pending_merges):
        payload = payload_by_conflict_id.get(conflict.conflict_id)
        if payload is None:
            raise RuntimeError(f"conflict {conflict.conflict_id} has no stored chunk")
        incoming_content = payload.get("content", "")
        skip_recheck = (position == 0 and not already_merged) or conflict.from_recheck

        if not skip_recheck:
            recheck = await detect_pairwise_conflict(working_content, incoming_content)
            if recheck is not None:
                scores, dominant_criterion = recheck
                await _reopen_conflict_after_recheck(
                    stores,
                    ctx,
                    conflict_id=conflict.conflict_id,
                    scores=scores,
                    dominant_criterion=dominant_criterion,
                    stage_record=stage_record,
                )
                await conflict_store.update_working_block(
                    stores.mongo,
                    ctx.batch_id,
                    working_content=working_content,
                    working_summary=await _summarize_working_content(working_content),
                )
                await _return_batch_to_user(stores.postgres, ctx.batch_id)
                return working_content, False

        working_content = await _merge_contents(
            working_content,
            incoming_content,
            conflict.resolution_instruction or "",
        )
        await conflict_store.update_working_block(
            stores.mongo,
            ctx.batch_id,
            working_content=working_content,
            working_summary=working_content[:SUMMARY_FALLBACK_CHARS],
        )
        await _close_out_conflicts(stores.postgres, [conflict.conflict_id], stage_record)

    return working_content, True


async def _apply_merge(
    stores: ResolverStores,
    ctx: BatchContext,
    stage_record: StageChain,
) -> None:
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
        stores.mongo,
        ctx.tenant_id,
        _ids_with_method(ctx.conflicts, MERGE),
    )
    working_content, completed = await _merge_into_working_content(
        stores,
        ctx,
        (batch_document or {}).get("working_content") or block_content,
        payload_by_conflict_id,
        stage_record,
    )
    if not completed:
        return

    await _close_out_conflicts(
        stores.postgres,
        _ids_with_method(ctx.conflicts, KEEP_EXISTING),
        stage_record,
    )

    if await _batch_has_open_conflicts(stores.postgres, ctx.batch_id):
        await set_conflict_batch_status(
            stores.postgres,
            ctx.batch_id,
            ConflictStatus.PENDING.value,
        )
        return

    await _publish_merged_block(
        stores,
        ctx,
        merged_content=working_content,
        current_version=current_version,
        embed_model_id=next(
            (
                payload["embed_model_id"]
                for payload in payload_by_conflict_id.values()
                if payload.get("embed_model_id")
            ),
            cfg.EMBED_MODEL_ID,
        ),
        stage_record=stage_record,
    )
    await _mark_batch_resolved(stores, ctx, stage_record)
