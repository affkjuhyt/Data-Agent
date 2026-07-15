"""Business logic for chunk and chunk-version CRUD."""
import logging
import uuid
from fastapi import HTTPException
from sqlalchemy import select as sa_select, update as sa_update

from libs.basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest, SelectInLoadRequest, WhereFilter, OrderBy, OrderDirection,
    KBTextBlockDelete, KBTextBlockVersionDelete, KBTextBlockVersionInsert,
)
from services.kb_backend.operations.pagination import (
    PAGE_SIZE, get_cursor, advance, build_page,
)
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBDataORM, KBTextBlockVersionORM,
)
from services.kb_backend.schemas import (
    ChunkResponse, ChunkVersion, to_string, coerce_jsonb_dict,
)

log = logging.getLogger(__name__)


async def _assert_tenant(postgres, doc_id: str, tenant_id: str) -> None:
    """Raise 404 if doc_id does not belong to tenant_id."""
    async with postgres.get_client() as session:
        row = await session.scalar(
            sa_select(KBDataORM.data_id)
            .where(
                KBDataORM.data_id == uuid.UUID(doc_id),
                KBDataORM.tenant_id == uuid.UUID(tenant_id),
            )
            .limit(1)
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")


async def _doc_tier(postgres, doc_id: str, tenant_id: str) -> str:
    """Return the document's current tier (bronze|silver|gold), 404 if not owned."""
    async with postgres.get_client() as session:
        tier = await session.scalar(
            sa_select(KBDataORM.current_tier)
            .where(
                KBDataORM.data_id == uuid.UUID(doc_id),
                KBDataORM.tenant_id == uuid.UUID(tenant_id),
            )
            .limit(1)
        )
    if tier is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return str(getattr(tier, "value", tier)).lower()


def _shape_silver_chunks(staging: dict | None) -> list[dict]:
    """Shape Mongo kb_silver_staging chunks into the UI Chunk shape. Silver chunks
    aren't versioned yet (versions are created at Gold), so each becomes a single
    active version carrying the parsed text + the silver summary's intents."""
    out: list[dict] = []
    for c in (staging or {}).get("chunks") or []:
        idx = c.get("block_index", len(out))
        content = c.get("content") or ""
        intents = c.get("intents") or []
        out.append({
            "id":    f"silver-{idx}",
            "title": f"Chunk {idx + 1}",
            "text":  content,
            "versions": [{
                "version_number":   "1",
                "create_at":        "",
                "status":           "active",
                "embedding_models": "",
                "entities":         [],
                "intent":           ", ".join(intents) if isinstance(intents, list) else str(intents),
                "text":             content,
            }],
        })
    return out


async def _get_silver_chunks(mongo, doc_id: str, page: int) -> dict:
    """Silver chunks live in Mongo staging (not yet promoted to Postgres KBTextBlock)."""
    staging = await mongo.get_client()["kb_silver_staging"].find_one({"data_id": doc_id})
    all_chunks = _shape_silver_chunks(staging)
    start = (page - 1) * PAGE_SIZE
    return {
        "items":     all_chunks[start:start + PAGE_SIZE],
        "page":      page,
        "page_size": PAGE_SIZE,
        "has_next":  len(all_chunks) > start + PAGE_SIZE,
    }


def _shape_chunks(rows: list[dict]) -> list[ChunkResponse]:
    """Group flat (block, version) rows into nested ChunkResponse list.
    Each block gets all its versions; the active version's text becomes
    the block's display text. `entities` come from the per-chunk version payload.
    intents are a DOCUMENT-level concept now (KBData.doc_metadata), NOT per-chunk,
    so the version `intent` is left blank — the document's intents are surfaced on
    the document itself (DocumentMetadata.intents)."""
    blocks: dict[str, dict] = {}
    for r in rows:
        bid = to_string(r["block_id"])
        if bid not in blocks:
            blocks[bid] = {"id": bid, "title": f"Chunk {r['block_index'] + 1}", "text": "", "versions": []}
        if r.get("version_id") is None:
            continue
        payload = coerce_jsonb_dict(r.get("payload"))
        blocks[bid]["versions"].append(ChunkVersion(
            version_number=to_string(r["version_number"]),
            create_at=to_string(r.get("created_at", "")),
            status="active" if r.get("is_active") else "inactive",
            embedding_models=to_string(r.get("embedding_model_id", "")),
            entities=payload.get("entities", []),
            intent="",
            text=r.get("content") or "",
        ))
        if r.get("is_active"):
            blocks[bid]["text"] = r.get("content") or ""
    return [ChunkResponse(**b) for b in blocks.values()]


async def get_chunks(
    postgres, mongo, doc_id: str, tenant_id: str,
    user_id: str, page: int = 1,
) -> dict:
    # Silver chunks are staged in Mongo; only Gold promotes them into Postgres
    # KBTextBlock. Read from whichever store matches the document's tier.
    tier = await _doc_tier(postgres, doc_id, tenant_id)
    if tier == "silver":
        return await _get_silver_chunks(mongo, doc_id, page)

    cursor_key = f"chunks:{doc_id}"
    cursor_ts  = get_cursor(user_id, cursor_key, page)

    resp = await postgres.read_deep(SelectInLoadRequest(
        table="KBTextBlock",
        load_paths=["KBTextBlockVersion"],
        filters=[WhereFilter(table_name="KBTextBlock", column_name="owner_id", value=doc_id)],
        cursor=cursor_ts,
        limit=PAGE_SIZE,
        order_by=OrderBy(table_name="KBTextBlock", column="inserted_at", order=OrderDirection.ASC),
    ))
    if resp.code != 200:
        log.error("get_chunks: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Failed to load chunks")

    raw = resp.data or []
    advance(user_id, cursor_key, page, raw)

    flat: list[dict] = []
    for block in raw:
        for v in (block.get("KBTextBlockVersion") or []):
            flat.append({
                "block_id":           block.get("block_id"),
                "block_index":        block.get("block_index") or 0,
                "inserted_at":        block.get("inserted_at"),
                "version_id":         v.get("version_id"),
                "version_number":     v.get("version_number"),
                "content":            v.get("content"),
                "is_active":          v.get("is_active"),
                "created_at":         v.get("created_at"),
                "payload":            v.get("payload"),
                "embedding_model_id": v.get("embedding_model_id"),
            })
    return build_page([c.model_dump() for c in _shape_chunks(flat)], page)


async def delete_chunk(postgres, chunk_id: str, doc_id: str, tenant_id: str, qdrant=None) -> None:
    await _assert_tenant(postgres, doc_id, tenant_id)
    resp = await postgres.soft_delete(KBTextBlockDelete(block_id=chunk_id))
    if resp.code == 404:
        raise HTTPException(status_code=404, detail="Chunk not found")
    if resp.code != 200:
        log.error("delete_chunk: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Failed to delete chunk")
    # Mirror the flag onto the chunk's Qdrant point (point id == block_id).
    if qdrant is not None:
        from services.kb_backend.operations.trash_ops import qdrant_set_deleted_by_block
        await qdrant_set_deleted_by_block(qdrant, chunk_id, True)


async def activate_chunk_version(postgres, chunk_id: str, version_number, doc_id: str, tenant_id: str) -> dict:
    await _assert_tenant(postgres, doc_id, tenant_id)
    block_uuid = uuid.UUID(chunk_id) if isinstance(chunk_id, str) else chunk_id
    async with postgres.get_client() as session:
        await session.execute(
            sa_update(KBTextBlockVersionORM)
            .where(KBTextBlockVersionORM.block_id == block_uuid)
            .values(is_active=False)
        )
        if version_number is not None:
            await session.execute(
                sa_update(KBTextBlockVersionORM)
                .where(KBTextBlockVersionORM.block_id == block_uuid)
                .where(KBTextBlockVersionORM.version_number == int(version_number))
                .values(is_active=True)
            )
        await session.commit()
    return {"status": "ok"}


async def delete_chunk_version(postgres, chunk_id: str, version_number: int, doc_id: str, tenant_id: str) -> None:
    await _assert_tenant(postgres, doc_id, tenant_id)
    resp = await postgres.read(ReadJoinRequest(
        joins_table=["KBTextBlockVersion"],
        filters=[
            WhereFilter(table_name="KBTextBlockVersion", column_name="block_id", value=chunk_id),
            WhereFilter(table_name="KBTextBlockVersion", column_name="version_number", value=version_number),
        ],
        limit=1,
    ))
    if resp.code != 200 or not resp.data:
        raise HTTPException(status_code=404, detail="Version not found")
    version_id = to_string(resp.data[0].get("version_id"))
    del_resp = await postgres.soft_delete(KBTextBlockVersionDelete(version_id=version_id))
    if del_resp.code != 200:
        log.error("delete_chunk_version: DB error: %s", del_resp.error)
        raise HTTPException(status_code=500, detail="Failed to delete chunk version")


async def create_chunk_version(
    postgres, chunk_id: str,
    text: str, entities: list, intents: list,
    created_by: str, doc_id: str, tenant_id: str,
) -> dict:
    await _assert_tenant(postgres, doc_id, tenant_id)
    resp = await postgres.read(ReadJoinRequest(
        joins_table=["KBTextBlockVersion"],
        filters=[WhereFilter(table_name="KBTextBlockVersion", column_name="block_id", value=chunk_id)],
        limit=100,
    ))
    existing = resp.data or []
    max_v = max((r.get("version_number") or 0 for r in existing), default=0)

    ins = await postgres.insert(KBTextBlockVersionInsert(
        block_id=chunk_id,
        version_number=max_v + 1,
        content=text,
        created_by=created_by,
        table_involved=False,
        payload={"entities": entities, "intents": intents},
        is_active=False,
    ))
    if ins.code != 200:
        log.error("create_chunk_version: DB error: %s", ins.error)
        raise HTTPException(status_code=500, detail="Failed to create chunk version")
    return {"version_id": ins.data.get("version_id"), "version_number": max_v + 1}
