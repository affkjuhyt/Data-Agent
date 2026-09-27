from __future__ import annotations

from typing import Any
import logging
import uuid

from fastapi import HTTPException
from qdrant_client import models as qmodels
from sqlalchemy import select as sa_select, update as sa_update, delete as sa_delete

from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBDataORM,
    KBTextBlockORM,
    KBTextBlockVersionORM,
    KBTextTableORM,
    KBLifecycleHistoryORM,
    KBTableORM,
    KBConflictORM,
    KBConflictBatchORM,
)
from services.kb_backend.pipeline.light_rag import config as lr_cfg
from services.kb_backend.pipeline.ingestion.config import MINIO_BUCKET
from services.kb_backend.schemas import DocResponse

log = logging.getLogger(__name__)

LEVELS = ("document", "chunk", "version")
_PREVIEW = 160


def _doc_filter(tenant_id: str | None, data_id: str) -> qmodels.Filter:
    must = [qmodels.FieldCondition(key="data_id", match=qmodels.MatchValue(value=data_id))]
    if tenant_id:
        must.append(qmodels.FieldCondition(key="tenant_id", match=qmodels.MatchValue(value=tenant_id)))
    return qmodels.Filter(must=must)


async def qdrant_set_deleted_by_data_id(
        qdrant: Any, tenant_id: str | None, data_id: str, value: bool
) -> None:
    client = qdrant.get_client()
    for collection in (lr_cfg.QDRANT_COLLECTION, lr_cfg.QDRANT_DOC_COLLECTION):
        await client.set_payload(
            collection_name=collection,
            payload={"is_deleted": value},
            points=_doc_filter(tenant_id, data_id),
        )


async def qdrant_set_deleted_by_block(qdrant: Any, block_id: str, value: bool) -> None:
    await qdrant.get_client().set_payload(
        collection_name=lr_cfg.QDRANT_COLLECTION,
        payload={"is_deleted": value},
        points=[block_id],
    )


async def _qdrant_delete_by_data_id(qdrant: Any, tenant_id: str | None, data_id: str) -> None:
    client = qdrant.get_client()
    for collection in (lr_cfg.QDRANT_COLLECTION, lr_cfg.QDRANT_DOC_COLLECTION):
        await client.delete(
            collection_name=collection,
            points_selector=qmodels.FilterSelector(filter=_doc_filter(tenant_id, data_id)),
        )


async def _qdrant_delete_block(qdrant: Any, block_id: str) -> None:
    await qdrant.get_client().delete(
        collection_name=lr_cfg.QDRANT_COLLECTION,
        points_selector=qmodels.PointIdsList(points=[block_id]),
    )


def _preview(text: str | None) -> str:
    t = (text or "").strip().replace("\n", " ")
    return t[:_PREVIEW] + ("…" if len(t) > _PREVIEW else "")


def _tenant_condition(level: str, tenant_id: str) -> Any:
    tenant_uuid = uuid.UUID(tenant_id)
    if level == "document":
        return KBDataORM.tenant_id == tenant_uuid
    owned_data_ids = sa_select(KBDataORM.data_id).where(KBDataORM.tenant_id == tenant_uuid)
    if level == "chunk":
        return KBTextBlockORM.owner_id.in_(owned_data_ids)
    owned_block_ids = sa_select(KBTextBlockORM.block_id).where(KBTextBlockORM.owner_id.in_(owned_data_ids))
    return KBTextBlockVersionORM.block_id.in_(owned_block_ids)


_ID_COLUMN_BY_LEVEL = {
    "document": KBDataORM.data_id,
    "chunk": KBTextBlockORM.block_id,
    "version": KBTextBlockVersionORM.version_id,
}


async def _trash_target(
        postgres: Any, level: str, item_id: uuid.UUID, tenant_id: str | None,
) -> tuple[bool, str | None]:
    """(row still present, MinIO object key) — one session for what took three."""
    id_column = _ID_COLUMN_BY_LEVEL[level]
    async with postgres.get_client() as session:
        if await session.scalar(sa_select(id_column).where(id_column == item_id).limit(1)) is None:
            return False, None
        if tenant_id:
            owned = await session.scalar(
                sa_select(id_column)
                .where(id_column == item_id, _tenant_condition(level, tenant_id))
                .limit(1)
            )
            if owned is None:
                raise HTTPException(status_code=404, detail=f"{level} not found")
        if level != "document":
            return True, None
        object_key = await session.scalar(sa_select(KBDataORM.path).where(KBDataORM.data_id == item_id))
    return True, object_key


async def list_trash(postgres: Any, level: str, tenant_id: str | None) -> dict[str, Any]:
    if level not in LEVELS:
        raise HTTPException(status_code=422, detail=f"Unknown trash level: {level}")

    async with postgres.get_client() as session:
        if level == "document":
            stmt = sa_select(KBDataORM).where(KBDataORM.is_deleted.is_(True))
            if tenant_id:
                stmt = stmt.where(KBDataORM.tenant_id == uuid.UUID(tenant_id))
            rows = (await session.execute(
                stmt.order_by(KBDataORM.inserted_at.desc()).limit(300)
            )).scalars().all()
            items = [
                DocResponse.model_validate({
                    "data_id": str(r.data_id),
                    "name": r.name,
                    "source_type": r.source_type,
                    "extension": r.extension,
                    "current_tier": r.current_tier,
                    "added_by": r.added_by,
                    "added_on": str(r.added_on or ""),
                    "abstract": r.abstract,
                    "metadata": r.doc_metadata,
                    "language": r.language,
                }).model_dump()
                for r in rows
            ]
            return {"level": level, "items": items, "total": len(items)}

        if level == "chunk":
            av = KBTextBlockVersionORM
            stmt = (
                sa_select(
                    KBTextBlockORM.block_id, KBTextBlockORM.block_index,
                    KBTextBlockORM.owner_id, KBDataORM.name, av.content,
                )
                .join(KBDataORM, KBDataORM.data_id == KBTextBlockORM.owner_id)
                .join(av, (av.block_id == KBTextBlockORM.block_id) & (av.is_active.is_(True)), isouter=True)
                .where(KBTextBlockORM.is_deleted.is_(True))
            )
            if tenant_id:
                stmt = stmt.where(KBDataORM.tenant_id == uuid.UUID(tenant_id))
            rows = (await session.execute(
                stmt.order_by(KBTextBlockORM.inserted_at.desc()).limit(300)
            )).all()
            items = [{
                "id": str(r.block_id),
                "block_id": str(r.block_id),
                "data_id": str(r.owner_id),
                "doc_name": r.name,
                "block_index": r.block_index,
                "preview": _preview(r.content),
            } for r in rows]
            return {"level": level, "items": items, "total": len(items)}

        stmt = (
            sa_select(
                KBTextBlockVersionORM.version_id, KBTextBlockVersionORM.block_id,
                KBTextBlockVersionORM.version_number, KBTextBlockVersionORM.content,
                KBTextBlockVersionORM.is_active, KBTextBlockORM.block_index, KBDataORM.name,
            )
            .join(KBTextBlockORM, KBTextBlockORM.block_id == KBTextBlockVersionORM.block_id)
            .join(KBDataORM, KBDataORM.data_id == KBTextBlockORM.owner_id)
            .where(KBTextBlockVersionORM.is_deleted.is_(True))
        )
        if tenant_id:
            stmt = stmt.where(KBDataORM.tenant_id == uuid.UUID(tenant_id))
        rows = (await session.execute(
            stmt.order_by(KBTextBlockVersionORM.inserted_at.desc()).limit(300)
        )).all()
        items = [{
            "id": str(r.version_id),
            "version_id": str(r.version_id),
            "block_id": str(r.block_id),
            "version_number": r.version_number,
            "is_active": bool(r.is_active),
            "doc_name": r.name,
            "block_index": r.block_index,
            "preview": _preview(r.content),
        } for r in rows]
        return {"level": level, "items": items, "total": len(items)}


async def set_postgres_deleted(
        postgres: Any, level: str, item_id: uuid.UUID, is_deleted: bool, tenant_id: str | None = None,
) -> int:
    orm_by_level = {
        "document": (KBDataORM, KBDataORM.data_id),
        "chunk": (KBTextBlockORM, KBTextBlockORM.block_id),
        "version": (KBTextBlockVersionORM, KBTextBlockVersionORM.version_id),
    }
    orm, id_column = orm_by_level[level]
    conditions = [id_column == item_id, orm.is_deleted.is_(not is_deleted)]
    if tenant_id:
        conditions.append(_tenant_condition(level, tenant_id))
    async with postgres.get_client() as session:
        try:
            result = await session.execute(
                sa_update(orm).where(*conditions).values(is_deleted=is_deleted)
            )
            if level == "document" and result.rowcount:
                await _sync_conflicts_with_document(session, item_id, is_deleted)
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    return result.rowcount or 0


async def _sync_conflicts_with_document(session: Any, data_id: uuid.UUID, is_deleted: bool) -> None:
    """A trashed document's conflicts must leave the Conflict Resolution
    screen with it (and come back on restore); only a permanent delete used to
    clear them, so deleted data kept showing as "pending". The reads exclude
    is_deleted rows, so flagging is enough. A batch follows its conflicts: it is
    hidden only once none of its conflicts is still visible."""
    batch_ids = set((await session.execute(
        sa_select(KBConflictORM.batch_id)
        .where(KBConflictORM.data_id == data_id, KBConflictORM.batch_id.is_not(None))
    )).scalars().all())
    await session.execute(
        sa_update(KBConflictORM).where(KBConflictORM.data_id == data_id).values(is_deleted=is_deleted)
    )
    if batch_ids:
        has_visible_conflict = (
            sa_select(KBConflictORM.conflict_id)
            .where(KBConflictORM.batch_id == KBConflictBatchORM.batch_id, KBConflictORM.is_deleted.is_(False))
            .exists()
        )
        await session.execute(
            sa_update(KBConflictBatchORM)
            .where(KBConflictBatchORM.batch_id.in_(batch_ids))
            .values(is_deleted=~has_visible_conflict)
        )


async def restore(postgres: Any, qdrant: Any, level: str, item_id: str, tenant_id: str | None) -> None:
    if level not in LEVELS:
        raise HTTPException(status_code=422, detail=f"Unknown trash level: {level}")
    item_uuid = uuid.UUID(item_id)

    if await set_postgres_deleted(postgres, level, item_uuid, False, tenant_id) == 0:
        raise HTTPException(status_code=404, detail=f"{level} not in trash")

    try:
        if level == "document":
            await qdrant_set_deleted_by_data_id(qdrant, tenant_id, item_id, False)
        elif level == "chunk":
            await qdrant_set_deleted_by_block(qdrant, item_id, False)
    except Exception as exc:
        log.error("trash restore %s %s: Qdrant mirror failed (%s) — re-trashing", level, item_id, exc)
        try:
            await set_postgres_deleted(postgres, level, item_uuid, True)
        except Exception as rollback_exc:
            log.error("trash restore %s %s: ROLLBACK FAILED (%s) — live in Postgres, "
                      "hidden in Qdrant", level, item_id, rollback_exc)
        raise HTTPException(status_code=503, detail="Restore failed — vector store unavailable")
    log.info("trash restore %s %s", level, item_id)


_DOC_MONGO_COLLECTIONS = ("kb_silver_staging", "kb_structured_staging", "kb_ingestion_logs", "pipeline_events")


async def _delete_blocks(session: Any, block_ids: list[uuid.UUID]) -> None:
    """Versions and text tables first: these FKs do not cascade in the real schema."""
    if not block_ids:
        return
    version_ids = (await session.execute(
        sa_select(KBTextBlockVersionORM.version_id).where(KBTextBlockVersionORM.block_id.in_(block_ids))
    )).scalars().all()
    if version_ids:
        await session.execute(sa_delete(KBTextTableORM).where(KBTextTableORM.version_id.in_(version_ids)))
        await session.execute(
            sa_delete(KBTextBlockVersionORM).where(KBTextBlockVersionORM.block_id.in_(block_ids))
        )
    await session.execute(sa_delete(KBTextBlockORM).where(KBTextBlockORM.block_id.in_(block_ids)))


async def _delete_document(session: Any, data_id: uuid.UUID) -> None:
    block_ids = (await session.execute(
        sa_select(KBTextBlockORM.block_id).where(KBTextBlockORM.owner_id == data_id)
    )).scalars().all()
    await _delete_blocks(session, list(block_ids))

    # A document that ever raised a gold-promotion conflict blocks its own
    # deletion until these are cleared; the batch goes only if now empty.
    batch_ids = (await session.execute(
        sa_select(KBConflictORM.batch_id)
        .where(KBConflictORM.data_id == data_id, KBConflictORM.batch_id.is_not(None))
    )).scalars().all()
    await session.execute(sa_delete(KBConflictORM).where(KBConflictORM.data_id == data_id))
    if batch_ids:
        await session.execute(
            sa_delete(KBConflictBatchORM).where(
                KBConflictBatchORM.batch_id.in_(set(batch_ids)),
                ~sa_select(KBConflictORM.conflict_id)
                .where(KBConflictORM.batch_id == KBConflictBatchORM.batch_id)
                .exists(),
            )
        )

    await session.execute(sa_delete(KBTableORM).where(KBTableORM.owner_id == data_id))
    await session.execute(sa_delete(KBLifecycleHistoryORM).where(KBLifecycleHistoryORM.data_id == data_id))
    await session.execute(sa_delete(KBDataORM).where(KBDataORM.data_id == data_id))


async def _purge_vectors_and_graph(qdrant: Any, neo4j: Any, level: str,
                                   item_id: str, tenant_id: str | None) -> None:
    if level == "document":
        await _qdrant_delete_by_data_id(qdrant, tenant_id, item_id)
        async with neo4j.get_client().session() as graph:
            await (await graph.run(
                "MATCH ()-[r:RELATES {data_id: $did}]-() DELETE r", did=item_id)).consume()
    elif level == "chunk":
        await _qdrant_delete_block(qdrant, item_id)


async def _purge_document_blobs(minio: Any, mongo: Any, object_key: str | None, data_id: str) -> None:
    """Best-effort: a leftover blob is garbage, not a correctness problem."""
    if minio is not None and object_key:
        try:
            await minio.get_client().delete(MINIO_BUCKET, object_key)
        except Exception as exc:
            log.warning("trash flush: minio delete failed key=%s: %s", object_key, exc)
    if mongo is None:
        return
    database = mongo.get_client()
    for collection in _DOC_MONGO_COLLECTIONS:
        try:
            await database[collection].delete_many({"data_id": data_id})
        except Exception as exc:
            log.warning("trash flush: mongo delete failed coll=%s data_id=%s: %s", collection, data_id, exc)


async def flush(
        postgres: Any, qdrant: Any, neo4j: Any, level: str, item_id: str, tenant_id: str | None,
        minio: Any = None, mongo: Any = None,
) -> None:
    if level not in LEVELS:
        raise HTTPException(status_code=422, detail=f"Unknown trash level: {level}")
    item_uuid = uuid.UUID(item_id)

    still_exists, object_key = await _trash_target(postgres, level, item_uuid, tenant_id)
    if not still_exists:
        log.info("trash flush %s %s — row already gone (cascaded by a parent flush); skipping",
                 level, item_id)
        return

    # Vectors first: a deleted row with live vectors stays searchable.
    await _purge_vectors_and_graph(qdrant, neo4j, level, item_id, tenant_id)

    async with postgres.get_client() as session:
        try:
            if level == "document":
                await _delete_document(session, item_uuid)
            elif level == "chunk":
                await _delete_blocks(session, [item_uuid])
            else:
                await session.execute(
                    sa_delete(KBTextBlockVersionORM).where(KBTextBlockVersionORM.version_id == item_uuid)
                )
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    if level == "document":
        await _purge_document_blobs(minio, mongo, object_key, item_id)

    log.info("trash flush %s %s (permanent)", level, item_id)
