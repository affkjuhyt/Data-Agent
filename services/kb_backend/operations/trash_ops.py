"""Trash — list / restore / permanently-delete (flush) soft-deleted KB entities.

Three levels (the UI surfaces these as the three Trash modes):
  • document — KBData.is_deleted. Restoring/flushing also syncs is_deleted on the
               document's Qdrant points: every chunk point (data_id filter) and the
               doc-summary point. Flush hard-deletes the Postgres rows (blocks
               cascade to versions/tables via FK), the Qdrant points, and the
               document's Neo4j relationships (KB-shared entity nodes are kept).
  • chunk    — KBTextBlock.is_deleted. The Qdrant chunk point id == block_id, so
               restore/flush syncs / deletes exactly that point.
  • version  — KBTextBlockVersion.is_deleted. Postgres only — a Qdrant chunk point
               is per-block (the active version's embedding), not per-version.

Every is_deleted mutation here keeps Postgres and Qdrant in lock-step.
"""
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
    KBLifecycleHistoryORM,
    KBTableORM,
)
from services.kb_backend.pipeline.light_rag import config as lr_cfg
from services.kb_backend.pipeline.ingestion.config import MINIO_BUCKET
from services.kb_backend.schemas import DocResponse

log = logging.getLogger(__name__)

LEVELS = ("document", "chunk", "version")
_PREVIEW = 160


# ── Qdrant sync helpers ───────────────────────────────────────────────────────
def _doc_filter(tenant_id: str | None, data_id: str) -> qmodels.Filter:
    must = [qmodels.FieldCondition(key="data_id", match=qmodels.MatchValue(value=data_id))]
    if tenant_id:
        must.append(qmodels.FieldCondition(key="tenant_id", match=qmodels.MatchValue(value=tenant_id)))
    return qmodels.Filter(must=must)


async def qdrant_set_deleted_by_data_id(
    qdrant: Any, tenant_id: str | None, data_id: str, value: bool
) -> None:
    """Set is_deleted=value on a document's chunk points (data_id filter) and its
    doc-summary point — both collections, best-effort."""
    client = qdrant.get_client()
    for collection in (lr_cfg.QDRANT_COLLECTION, lr_cfg.QDRANT_DOC_COLLECTION):
        try:
            await client.set_payload(
                collection_name=collection,
                payload={"is_deleted": value},
                points=_doc_filter(tenant_id, data_id),
            )
        except Exception as exc:
            log.warning("trash: set is_deleted=%s failed on %s data_id=%s: %s", value, collection, data_id, exc)


async def qdrant_set_deleted_by_block(qdrant: Any, block_id: str, value: bool) -> None:
    """Set is_deleted=value on the single chunk point whose id == block_id."""
    try:
        await qdrant.get_client().set_payload(
            collection_name=lr_cfg.QDRANT_COLLECTION,
            payload={"is_deleted": value},
            points=[block_id],
        )
    except Exception as exc:
        log.warning("trash: set is_deleted=%s failed for block_id=%s: %s", value, block_id, exc)


async def _qdrant_delete_by_data_id(qdrant: Any, tenant_id: str | None, data_id: str) -> None:
    client = qdrant.get_client()
    for collection in (lr_cfg.QDRANT_COLLECTION, lr_cfg.QDRANT_DOC_COLLECTION):
        try:
            await client.delete(
                collection_name=collection,
                points_selector=qmodels.FilterSelector(filter=_doc_filter(tenant_id, data_id)),
            )
        except Exception as exc:
            log.warning("trash: qdrant delete failed on %s data_id=%s: %s", collection, data_id, exc)


async def _qdrant_delete_block(qdrant: Any, block_id: str) -> None:
    try:
        await qdrant.get_client().delete(
            collection_name=lr_cfg.QDRANT_COLLECTION,
            points_selector=qmodels.PointIdsList(points=[block_id]),
        )
    except Exception as exc:
        log.warning("trash: qdrant delete failed for block_id=%s: %s", block_id, exc)


def _preview(text: str | None) -> str:
    t = (text or "").strip().replace("\n", " ")
    return t[:_PREVIEW] + ("…" if len(t) > _PREVIEW else "")


# ── List ──────────────────────────────────────────────────────────────────────
async def list_trash(postgres: Any, level: str, tenant_id: str | None) -> dict[str, Any]:
    """Soft-deleted items at the given level (newest first), scoped to the tenant."""
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
                    "data_id":      str(r.data_id),
                    "name":         r.name,
                    "source_type":  r.source_type,
                    "extension":    r.extension,
                    "current_tier": r.current_tier,
                    "added_by":     r.added_by,
                    "added_on":     str(r.added_on or ""),
                    "abstract":     r.abstract,
                    "metadata":     r.doc_metadata,
                    "language":     r.language,
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
                "id":          str(r.block_id),
                "block_id":    str(r.block_id),
                "data_id":     str(r.owner_id),
                "doc_name":    r.name,
                "block_index": r.block_index,
                "preview":     _preview(r.content),
            } for r in rows]
            return {"level": level, "items": items, "total": len(items)}

        # version
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
            "id":             str(r.version_id),
            "version_id":     str(r.version_id),
            "block_id":       str(r.block_id),
            "version_number": r.version_number,
            "is_active":      bool(r.is_active),
            "doc_name":       r.name,
            "block_index":    r.block_index,
            "preview":        _preview(r.content),
        } for r in rows]
        return {"level": level, "items": items, "total": len(items)}


# ── Restore (reverse the is_deleted tag in Postgres + Qdrant) ─────────────────
async def restore(postgres: Any, qdrant: Any, level: str, item_id: str, tenant_id: str | None) -> None:
    if level not in LEVELS:
        raise HTTPException(status_code=422, detail=f"Unknown trash level: {level}")
    iid = uuid.UUID(item_id)

    async with postgres.get_client() as session:
        if level == "document":
            res = await session.execute(
                sa_update(KBDataORM)
                .where(KBDataORM.data_id == iid, KBDataORM.is_deleted.is_(True))
                .values(is_deleted=False)
            )
        elif level == "chunk":
            res = await session.execute(
                sa_update(KBTextBlockORM)
                .where(KBTextBlockORM.block_id == iid, KBTextBlockORM.is_deleted.is_(True))
                .values(is_deleted=False)
            )
        else:  # version
            res = await session.execute(
                sa_update(KBTextBlockVersionORM)
                .where(KBTextBlockVersionORM.version_id == iid, KBTextBlockVersionORM.is_deleted.is_(True))
                .values(is_deleted=False)
            )
        await session.commit()

    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"{level} not in trash")

    # Mirror the un-delete onto Qdrant.
    if level == "document":
        await qdrant_set_deleted_by_data_id(qdrant, tenant_id, item_id, False)
    elif level == "chunk":
        await qdrant_set_deleted_by_block(qdrant, item_id, False)
    log.info("trash restore %s %s", level, item_id)


# ── Flush (permanent delete) ──────────────────────────────────────────────────
# Per-document Mongo collections keyed by data_id (silver staging + the two
# ingestion-progress streams). Cleared on a document flush so no orphan survives.
_DOC_MONGO_COLLECTIONS = ("kb_silver_staging", "kb_ingestion_logs", "pipeline_events")


async def flush(
    postgres: Any, qdrant: Any, neo4j: Any, level: str, item_id: str, tenant_id: str | None,
    minio: Any = None, mongo: Any = None,
) -> None:
    if level not in LEVELS:
        raise HTTPException(status_code=422, detail=f"Unknown trash level: {level}")
    iid = uuid.UUID(item_id)

    # Existence guard. A parent flush cascades its children away — a document
    # flush hard-deletes its KBTextBlock rows, which FK-cascade to versions and
    # tables. If the user trashed a chunk/version AND its parent document, then
    # flushed the parent first, the child row is already gone while still listed
    # in the trash. Re-check the level's own table (document→KBData,
    # chunk→KBTextBlock, version→KBTextBlockVersion) and no-op if it's missing,
    # so a second flush can't fire Qdrant/Neo4j/Mongo side-effects on a phantom row.
    async with postgres.get_client() as session:
        if level == "document":
            still_exists = await session.scalar(
                sa_select(KBDataORM.data_id).where(KBDataORM.data_id == iid)
            )
        elif level == "chunk":
            still_exists = await session.scalar(
                sa_select(KBTextBlockORM.block_id).where(KBTextBlockORM.block_id == iid)
            )
        else:  # version
            still_exists = await session.scalar(
                sa_select(KBTextBlockVersionORM.version_id).where(KBTextBlockVersionORM.version_id == iid)
            )
    if not still_exists:
        log.info(
            "trash flush %s %s — row already gone (cascaded by a parent flush); skipping",
            level, item_id,
        )
        return

    if level == "document":
        # Read the MinIO object key before the KBData row is gone.
        async with postgres.get_client() as session:
            object_key = await session.scalar(
                sa_select(KBDataORM.path).where(KBDataORM.data_id == iid)
            )

        await _qdrant_delete_by_data_id(qdrant, tenant_id, item_id)
        # Drop this document's graph edges; KB-shared entity nodes survive.
        try:
            async with neo4j.get_client().session() as session:
                await (await session.run(
                    "MATCH ()-[r:RELATES {data_id: $did}]-() DELETE r", did=item_id)).consume()
        except Exception as exc:
            log.warning("trash flush: neo4j cleanup failed data_id=%s: %s", item_id, exc)
        async with postgres.get_client() as session:
            # blocks cascade to versions+tables (FK ON DELETE CASCADE); owner FKs do not.
            await session.execute(sa_delete(KBTextBlockORM).where(KBTextBlockORM.owner_id == iid))
            await session.execute(sa_delete(KBTableORM).where(KBTableORM.owner_id == iid))
            await session.execute(sa_delete(KBLifecycleHistoryORM).where(KBLifecycleHistoryORM.data_id == iid))
            await session.execute(sa_delete(KBDataORM).where(KBDataORM.data_id == iid))
            await session.commit()

        # MinIO source object — best-effort; a missing/failed delete must not leave
        # the document half-flushed (the DB rows are already gone).
        if minio is not None and object_key:
            try:
                await minio.get_client().delete(MINIO_BUCKET, object_key)
            except Exception as exc:
                log.warning("trash flush: minio delete failed key=%s: %s", object_key, exc)

        # Per-document Mongo staging + progress streams (keyed by data_id).
        if mongo is not None:
            db = mongo.get_client()
            for coll in _DOC_MONGO_COLLECTIONS:
                try:
                    await db[coll].delete_many({"data_id": item_id})
                except Exception as exc:
                    log.warning("trash flush: mongo delete failed coll=%s data_id=%s: %s", coll, item_id, exc)

    elif level == "chunk":
        await _qdrant_delete_block(qdrant, item_id)
        async with postgres.get_client() as session:
            await session.execute(sa_delete(KBTextBlockORM).where(KBTextBlockORM.block_id == iid))
            await session.commit()

    else:  # version — Postgres only
        async with postgres.get_client() as session:
            await session.execute(sa_delete(KBTextBlockVersionORM).where(KBTextBlockVersionORM.version_id == iid))
            await session.commit()

    log.info("trash flush %s %s (permanent)", level, item_id)
