"""Keep-incoming conflict resolution strategy."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import update as sa_update

from libs.database_connector.postgres_connector import PostgresClient
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import KBTextBlockORM
from services.kb_backend.workers.conflict_resolution.models import (
    KEEP_INCOMING,
    BatchContext,
    ResolverStores,
)
from services.kb_backend.workers.conflict_resolution.state import (
    _close_out_conflicts,
    _conflict_ids,
    _mark_batch_resolved,
)
from libs.ingestion.gold import (
    _commit_chunk_atomic,
    canonicalize_chunk_entities,
    remap_chunk_relationships,
)
from libs.ingestion import config as cfg
from libs.ingestion import conflict_store
from libs.ingestion.stage_chain import StageChain


async def _rollback_mark_payload_committed(mongo: Any, tenant_id: str, conflict_id: str) -> None:
    try:
        await conflict_store.mark_payload_committed(mongo, tenant_id, conflict_id, None)
    except Exception:
        pass


async def _commit_incoming_chunk(
    stores: ResolverStores,
    ctx: BatchContext,
    payload: dict[str, Any],
    stage_record: StageChain,
) -> str:
    embed_model_id = payload.get("embed_model_id") or cfg.EMBED_MODEL_ID
    data_id = payload["data_id"]
    entities = await canonicalize_chunk_entities(
        stores.qdrant,
        stores.postgres,
        stores.model,
        ctx.tenant_id,
        payload.get("entities") or [],
        knowledge_base=ctx.knowledge_base,
        embed_model_id=embed_model_id,
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
            stores.mongo,
            ctx.tenant_id,
            payload["conflict_id"],
            block_id,
        )
        stage_record.push(
            f"conflict_payload_marked_committed_{payload['conflict_id']}",
            rollback_func=_rollback_mark_payload_committed,
            rollback_args=(stores.mongo, ctx.tenant_id, payload["conflict_id"]),
        )

    await stores.graph.add_chunk(
        tenant_id=ctx.tenant_id,
        knowledge_base=ctx.knowledge_base,
        data_id=data_id,
        block_id=block_id,
        entities=entities.graph_nodes(),
        relationships=remap_chunk_relationships(
            payload.get("relationships"),
            entities.raw_to_canonical,
        ),
        embed_model_id=embed_model_id,
    )
    stage_record.push(
        f"conflict_chunk_graph_updated_{block_id}",
        rollback_func=stores.graph.remove_chunk,
        rollback_kwargs={
            "tenant_id": ctx.tenant_id,
            "knowledge_base": ctx.knowledge_base,
            "block_id": block_id,
        },
    )
    return block_id


async def _set_block_deleted(postgres: PostgresClient, block_id: str, is_deleted: bool) -> None:
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBTextBlockORM)
                .where(KBTextBlockORM.block_id == uuid.UUID(block_id))
                .values(is_deleted=is_deleted)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _rollback_soft_delete_block(postgres: PostgresClient, qdrant: Any, block_id: str) -> None:
    try:
        await _set_block_deleted(postgres, block_id, False)
    except Exception:
        pass
    try:
        await qdrant.set_payload(
            collection_name=cfg.QDRANT_COLLECTION,
            payload={"is_deleted": False},
            points=[block_id],
        )
    except Exception:
        pass


async def _soft_delete_block(
    stores: ResolverStores,
    ctx: BatchContext,
    block_id: str,
    stage_record: StageChain,
) -> None:
    await _set_block_deleted(stores.postgres, block_id, True)
    try:
        await stores.qdrant.set_payload(
            collection_name=cfg.QDRANT_COLLECTION,
            payload={"is_deleted": True},
            points=[block_id],
        )
        await stores.graph.remove_chunk(
            tenant_id=ctx.tenant_id,
            knowledge_base=ctx.knowledge_base,
            block_id=block_id,
        )
    except Exception:
        try:
            await _set_block_deleted(stores.postgres, block_id, False)
            await stores.qdrant.set_payload(
                collection_name=cfg.QDRANT_COLLECTION,
                payload={"is_deleted": False},
                points=[block_id],
            )
        except Exception:
            pass
        raise
    stage_record.push(
        f"block_soft_deleted_{block_id}",
        rollback_func=_rollback_soft_delete_block,
        rollback_args=(stores.postgres, stores.qdrant, block_id),
    )


async def _apply_keep_incoming(
    stores: ResolverStores,
    ctx: BatchContext,
    stage_record: StageChain,
) -> None:
    chosen = next(
        conflict for conflict in ctx.conflicts
        if conflict.resolution_method == KEEP_INCOMING
    )
    payload = await conflict_store.load_payload(
        stores.mongo,
        ctx.tenant_id,
        chosen.conflict_id,
    )
    if payload is None:
        raise RuntimeError(f"conflict {chosen.conflict_id} has no stored chunk")

    await _commit_incoming_chunk(stores, ctx, payload, stage_record)
    if ctx.existing_block_id:
        await _soft_delete_block(stores, ctx, ctx.existing_block_id, stage_record)

    await _close_out_conflicts(stores.postgres, _conflict_ids(ctx.conflicts), stage_record)
    await _mark_batch_resolved(stores, ctx, stage_record)
