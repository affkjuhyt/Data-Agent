from __future__ import annotations

import logging
import time as _time
import uuid
from typing import Any

from qdrant_client import models as qmodels
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from sqlalchemy import cast as sa_cast, delete as sa_delete, func as sa_func, select as sa_select, text as sa_text, update as sa_update
from sqlalchemy.dialects.postgresql import JSONB

from libs.shared.row_version import with_new_version
from libs.basemodel.services_databaseconnector.postgres_model import ConflictStatus, KBLifecycleHistoryInsert, Tier
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBConflictBatchORM,
    KBConflictORM,
    KBDataORM,
    KBQdrantCollectionORM,
    KBTextBlockORM,
    KBTextBlockVersionORM,
    KBTextTableORM,
)
from libs.database_connector.model_connector import ModelServiceClient
from libs.database_connector.postgres_connector import PostgresClient
from libs.ingestion.gold.summarization import _reduce_summaries
from libs.ingestion import config as cfg
from libs.ingestion import conflict_store
from libs.ingestion.graph_store import GraphStore
from libs.ingestion.log_writer import IngestionLogger
from libs.ingestion.pipeline_events import NULL_EMITTER, PipelineEventEmitter
from libs.ingestion.stage_chain import StageChain

log = logging.getLogger(__name__)

DEFAULT_KNOWLEDGE_BASE = "General Knowledge Base"


async def _get_collection_embed_model_id(postgres: PostgresClient, collection_name: str) -> str:
    async with postgres.get_client() as session:
        row = await session.scalar(
            sa_select(KBQdrantCollectionORM.embedding_model_id)
            .where(KBQdrantCollectionORM.collection_name == collection_name)
            .limit(1)
        )
    return str(row) if row else cfg.EMBED_MODEL_ID


async def _get_data_row(postgres: PostgresClient, data_id: str) -> tuple[str, str, str]:
    try:
        async with postgres.get_client() as session:
            row = (
                await session.execute(
                    sa_select(KBDataORM.name, KBDataORM.knowledge_base_id, KBDataORM.purpose)
                    .where(KBDataORM.data_id == uuid.UUID(data_id))
                    .limit(1)
                )
            ).first()
        if row:
            return str(row[0] or ""), (str(row[1]) if row[1] else DEFAULT_KNOWLEDGE_BASE), str(row[2] or "production")
    except Exception:
        pass
    return "", DEFAULT_KNOWLEDGE_BASE, "production"


async def _clear_open_conflicts(
    postgres: PostgresClient,
    database: Any,
    tenant_id: str,
    data_id: str,
    stage_record: StageChain,
) -> None:
    async with postgres.get_client() as session:
        rows = (
            await session.execute(
                sa_select(KBConflictORM.conflict_id, KBConflictORM.batch_id)
                .where(KBConflictORM.data_id == uuid.UUID(data_id))
                .where(KBConflictORM.status != ConflictStatus.RESOLVED.value)
            )
        ).all()
    if not rows:
        return

    conflict_ids = [str(row.conflict_id) for row in rows]
    emptied_batch_ids = [str(batch_id) for batch_id in {row.batch_id for row in rows} if batch_id]
    await conflict_store.drop_payloads(database, tenant_id, conflict_ids)
    stage_record.push(f"stale_conflict_payloads_dropped_{data_id}")

    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_delete(KBConflictORM).where(KBConflictORM.conflict_id.in_([uuid.UUID(cid) for cid in conflict_ids]))
            )
            await session.execute(
                sa_delete(KBConflictBatchORM)
                .where(KBConflictBatchORM.batch_id.in_([uuid.UUID(bid) for bid in emptied_batch_ids]))
                .where(
                    ~sa_select(KBConflictORM.conflict_id)
                    .where(KBConflictORM.batch_id == KBConflictBatchORM.batch_id)
                    .exists()
                )
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    stage_record.push(f"stale_conflict_rows_dropped_{data_id}")

    async with postgres.get_client() as session:
        surviving = set(
            (
                await session.execute(
                    sa_select(KBConflictBatchORM.batch_id).where(
                        KBConflictBatchORM.batch_id.in_([uuid.UUID(bid) for bid in emptied_batch_ids])
                    )
                )
            ).scalars().all()
        )
    await conflict_store.drop_batches(database, [bid for bid in emptied_batch_ids if uuid.UUID(bid) not in surviving])
    stage_record.push(f"stale_conflict_batches_dropped_{data_id}")


async def _cleanup_previous_promotion(
    postgres: PostgresClient,
    qdrant: AsyncQdrantClient,
    graph: GraphStore,
    database: Any,
    tenant_id: str,
    data_id: str,
    stage_record: StageChain,
    knowledge_base: str = "",
) -> None:
    await qdrant.delete(
        collection_name=cfg.QDRANT_COLLECTION,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[qmodels.FieldCondition(key="data_id", match=qmodels.MatchValue(value=data_id))]
            )
        ),
    )
    stage_record.push(f"stale_chunk_points_dropped_{data_id}")

    await qdrant.delete(
        collection_name=cfg.QDRANT_DOC_COLLECTION,
        points_selector=qmodels.PointIdsList(points=[data_id]),
    )
    stage_record.push(f"stale_doc_point_dropped_{data_id}")

    await graph.remove_document(tenant_id=tenant_id, knowledge_base=knowledge_base, data_id=data_id)
    stage_record.push(f"stale_graph_document_removed_{data_id}")

    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBTextBlockORM)
                .where(KBTextBlockORM.owner_id == uuid.UUID(data_id))
                .where(KBTextBlockORM.is_deleted.is_(False))
                .values(is_deleted=True)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    stage_record.push(f"stale_text_blocks_soft_deleted_{data_id}")

    await _clear_open_conflicts(postgres, database, tenant_id, data_id, stage_record)


async def _rollback_committed_chunk(postgres: PostgresClient, qdrant: AsyncQdrantClient, block_id: str) -> None:
    try:
        await qdrant.delete(
            collection_name=cfg.QDRANT_COLLECTION,
            points_selector=qmodels.PointIdsList(points=[block_id]),
        )
    except Exception:
        pass
    async with postgres.get_client() as session:
        try:
            await session.execute(sa_delete(KBTextBlockORM).where(KBTextBlockORM.block_id == uuid.UUID(block_id)))
            await session.commit()
        except Exception:
            await session.rollback()


async def _commit_chunk_atomic(
    postgres: PostgresClient,
    qdrant: AsyncQdrantClient,
    tenant_id: str,
    data_id: str,
    block_index: int,
    content: str,
    table_involved: bool,
    table: dict[str, Any] | None,
    payload_meta: dict[str, Any],
    embedding: list[float],
    summary: str,
    approved_by: str,
    embed_model_id: str,
    stage_record: StageChain,
    knowledge_base: str = "",
    purpose: str = "production",
    entities: list[str] | None = None,
    entity_type_fields: dict[str, Any] | None = None,
    emitter: PipelineEventEmitter = NULL_EMITTER,
) -> str:
    block_id: str | None = None
    qdrant_upserted = False
    try:
        async with postgres.get_client() as session:
            try:
                postgres_started = _time.monotonic()
                block_row = KBTextBlockORM(owner_id=uuid.UUID(data_id), block_index=block_index)
                session.add(block_row)
                await session.flush()

                version_row = KBTextBlockVersionORM(
                    block_id=block_row.block_id,
                    version_number=1,
                    content=content,
                    created_by=approved_by,
                    table_involved=table_involved,
                    payload=payload_meta,
                    is_active=True,
                )
                session.add(version_row)
                await session.flush()

                if table_involved and table:
                    table_row = KBTextTableORM(
                        version_id=version_row.version_id,
                        table_name=table.get("table_name", f"table_{block_index}"),
                        description=table.get("description"),
                        data=table.get("data"),
                    )
                    session.add(table_row)
                    await session.flush()

                block_id = str(block_row.block_id)
                postgres_ms = round((_time.monotonic() - postgres_started) * 1000)

                qdrant_started = _time.monotonic()
                await qdrant.upsert(
                    collection_name=cfg.QDRANT_COLLECTION,
                    points=[
                        qmodels.PointStruct(
                            id=block_id,
                            vector=embedding,
                            payload={
                                "tenant_id": tenant_id,
                                "data_id": data_id,
                                "block_id": block_id,
                                "summary": summary,
                                "entities": entities or [],
                                **(entity_type_fields or {}),
                                "embedding_model_id": embed_model_id,
                                "knowledge_base": knowledge_base,
                                "purpose": purpose,
                                "is_deleted": False,
                            },
                        )
                    ],
                )
                qdrant_upserted = True
                qdrant_ms = round((_time.monotonic() - qdrant_started) * 1000)

                await session.commit()
                stage_record.push(
                    f"chunk_committed_{block_index}",
                    rollback_func=_rollback_committed_chunk,
                    rollback_args=(postgres, qdrant, block_id),
                )
                await emitter.emit(
                    "gold.store.timing",
                    {"index": block_index, "postgres_ms": postgres_ms, "qdrant_ms": qdrant_ms},
                )
                return block_id
            except Exception as exc:
                await session.rollback()
                rollback_note = ""
                if qdrant_upserted and block_id:
                    try:
                        await qdrant.delete(
                            collection_name=cfg.QDRANT_COLLECTION,
                            points_selector=qmodels.PointIdsList(points=[block_id]),
                        )
                    except Exception as rollback_exc:
                        rollback_note = f"; Qdrant rollback also failed: {rollback_exc}"
                raise RuntimeError(f"Atomic commit failed block_index={block_index}: {exc}{rollback_note}") from exc
    except RuntimeError as error:
        raise error
    except Exception as exc:
        raise RuntimeError(f"Unexpected error during atomic commit block_index={block_index}: {exc}") from exc


async def fetch_active_block_version(postgres: PostgresClient, block_id: str) -> tuple[str, int] | None:
    async with postgres.get_client() as session:
        result = await session.execute(
            sa_select(KBTextBlockVersionORM)
            .join(KBTextBlockORM, KBTextBlockVersionORM.block_id == KBTextBlockORM.block_id)
            .where(KBTextBlockORM.block_id == uuid.UUID(block_id))
            .where(KBTextBlockVersionORM.is_active.is_(True))
            .where(KBTextBlockVersionORM.is_deleted.is_(False))
            .limit(1)
        )
        row = result.scalars().first()
    return (row.content, row.version_number) if row is not None else None


async def _reduce_document_summary(
    committed_summaries: list[str],
    logger: IngestionLogger,
    emitter: PipelineEventEmitter,
) -> str:
    await logger.progress(status="GOLD_REDUCE")
    async with emitter.step("gold.doc.reduce", summaries=len(committed_summaries)) as reduce_scope:
        doc_summary = await _reduce_summaries(committed_summaries, emitter)
        reduce_scope.set(summary=doc_summary, chars=len(doc_summary))
    return doc_summary


async def _write_doc_summary_point(
    *,
    qdrant: AsyncQdrantClient,
    model: ModelServiceClient,
    logger: IngestionLogger,
    data_id: str,
    tenant_id: str,
    knowledge_base: str,
    embed_model_id: str,
    doc_summary: str,
    top_intents: list[str],
    committed_count: int,
    emitter: PipelineEventEmitter,
    stage_record: StageChain,
    purpose: str = "production",
) -> None:
    await logger.progress(status="GOLD_GRAPH")
    async with emitter.step("gold.doc.commit", data_id=data_id) as doc_scope:
        doc_embedding = (await model.embed([doc_summary], model_id=embed_model_id))[0]
        await qdrant.upsert(
            collection_name=cfg.QDRANT_DOC_COLLECTION,
            points=[
                qmodels.PointStruct(
                    id=data_id,
                    vector=doc_embedding,
                    payload={
                        "tenant_id": tenant_id,
                        "data_id": data_id,
                        "summary": doc_summary,
                        "intents": top_intents,
                        "chunk_count": committed_count,
                        "embedding_model_id": embed_model_id,
                        "knowledge_base": knowledge_base,
                        "purpose": purpose,
                        "is_deleted": False,
                    },
                )
            ],
        )
        stage_record.push(
            f"doc_summary_written_{data_id}",
            rollback_func=qdrant.delete,
            rollback_kwargs={
                "collection_name": cfg.QDRANT_DOC_COLLECTION,
                "points_selector": qmodels.PointIdsList(points=[data_id]),
            },
        )
        doc_scope.set(intents=top_intents)


async def _rollback_document_tier(postgres: PostgresClient, data_id: str) -> None:
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBDataORM).where(KBDataORM.data_id == uuid.UUID(data_id)).values(**with_new_version(current_tier=Tier.SILVER.value))
            )
            await session.commit()
        except Exception:
            await session.rollback()


async def _update_document_tier(
    postgres: PostgresClient,
    data_id: str,
    top_intents: list[str],
    stage_record: StageChain,
) -> None:
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBDataORM)
                .where(KBDataORM.data_id == uuid.UUID(data_id))
                .values(**with_new_version(
                    current_tier=Tier.GOLD.value,
                    doc_metadata=sa_func.coalesce(KBDataORM.doc_metadata, sa_cast({}, JSONB)).op("||")(
                        sa_cast({"intents": top_intents}, JSONB)
                    ),
                ))
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    stage_record.push(
        f"document_tier_updated_{data_id}",
        rollback_func=_rollback_document_tier,
        rollback_args=(postgres, data_id),
    )


async def set_conflict_batch_status(postgres: PostgresClient, conflict_batch_id: str | None, status_value: str) -> None:
    if conflict_batch_id is None:
        return
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBConflictBatchORM)
                .where(KBConflictBatchORM.batch_id == uuid.UUID(conflict_batch_id))
                .values(status=status_value)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _record_gold_lifecycle(
    postgres: PostgresClient,
    data_id: str,
    approved_by: str,
    counts: dict[str, int],
    embed_model_id: str,
) -> None:
    try:
        await postgres.insert(
            KBLifecycleHistoryInsert(
                data_id=data_id,
                to_tier=Tier.GOLD,
                from_tier=Tier.SILVER,
                approved_by=approved_by,
                notes=(
                    f"Promoted to gold: {counts['committed']} committed, "
                    f"{counts['conflicted']} conflicts, {counts['skipped']} skipped, "
                    f"{counts['failed']} failed, {counts['graph_failed']} graph-failed"
                ),
                pipeline_metadata={
                    "stage": "gold",
                    "chunks_committed": counts["committed"],
                    "chunks_conflicted": counts["conflicted"],
                    "chunks_skipped": counts["skipped"],
                    "chunks_failed": counts["failed"],
                    "chunks_graph_failed": counts["graph_failed"],
                    "embed_model_id": embed_model_id,
                },
            )
        )
    except Exception:
        pass


async def _load_pii_policy(postgres: PostgresClient, tenant_id: str) -> list[str]:
    try:
        async with postgres.get_client() as session:
            result = await session.execute(
                sa_text("SELECT pii_masking, pii_types FROM compliance_settings WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
            row = result.mappings().first()
    except Exception as exc:
        log.warning("Gold: PII policy lookup failed tenant=%s: %s — masking disabled", tenant_id, exc)
        return []
    if not row or not row["pii_masking"]:
        return []
    raw = (row["pii_types"] or "").strip()
    return [segment for segment in (part.strip().upper() for part in raw.split(",")) if segment]
