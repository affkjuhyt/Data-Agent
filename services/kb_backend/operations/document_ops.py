"""Business logic for document listing, promotion, and deletion."""
import logging
import uuid
from fastapi import HTTPException
from sqlalchemy import select as sa_select, func as sa_func
from basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest, WhereFilter, OrderBy, OrderDirection, KBDataDelete, FilterOperator,
)
from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBLifecycleHistoryORM, KBDataORM, KBTextBlockORM,
)
from services.kb_backend.pipeline.ingestion import config as ing_cfg
from services.kb_backend.schemas import DocResponse, to_string
from services.kb_backend.operations.pagination import (
    PAGE_SIZE, decode_cursor, encode_cursor, build_cursor_page,
)
from services.kb_backend.operations.trash_ops import qdrant_set_deleted_by_data_id

log = logging.getLogger(__name__)


async def fetch_one(postgres, data_id: str, tenant_id: str | None) -> dict | None:
    resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBData"],
        filters=[WhereFilter(table_name="KBData", column_name="data_id", value=data_id)],
        limit=1,
    ))
    if resp.code == 200 and resp.data:
        return resp.data[0]
    return None


async def fetch_one_or_404(postgres, data_id: str, tenant_id: str | None) -> dict:
    """fetch_one plus a 404 guard, for routes that require the record to exist.
    Centralises the not-found contract so routers stay pass-throughs and every
    'missing document' returns a real 404 (+ message) instead of HTTP 200."""
    record = await fetch_one(postgres, data_id, tenant_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {data_id}")
    return record


async def name_exists(postgres, tenant_id: str, name: str) -> bool:
    """True if a KBData document with this (globally-unique) name already exists.
    Lets the router reject duplicates with a clear 409 instead of a generic insert 500."""
    resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBData"],
        filters=[WhereFilter(table_name="KBData", column_name="name", value=name)],
        limit=1,
    ))
    return resp.code == 200 and bool(resp.data)


async def get_doc_history(postgres, doc_id: str, tenant_id: str) -> list[dict]:
    """Lifecycle-history rows for a document, newest first, tenant-scoped.

    Raises HTTPException(400) on malformed ids so the router stays a thin pass-through.
    """
    try:
        data_uuid = uuid.UUID(doc_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid doc_id")
    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid tenant_id")

    async with postgres.get_client() as session:
        rows = (await session.execute(
            sa_select(KBLifecycleHistoryORM)
            .join(KBDataORM, KBLifecycleHistoryORM.data_id == KBDataORM.data_id)
            .where(
                KBLifecycleHistoryORM.data_id == data_uuid,
                KBDataORM.tenant_id == tenant_uuid,
                KBLifecycleHistoryORM.is_deleted == False,  # noqa: E712
            )
            .order_by(KBLifecycleHistoryORM.transitioned_at.desc())
        )).scalars().all()

    return [
        {
            "history_id":        to_string(row.history_id),
            "from_tier":         row.from_tier,
            "to_tier":           row.to_tier,
            "transitioned_at":   row.transitioned_at.isoformat() if row.transitioned_at else None,
            "approved_by":       row.approved_by,
            "notes":             row.notes,
            "pipeline_metadata": row.pipeline_metadata,
        }
        for row in rows
    ]


# Source-type filter categories (the UI chips) → KBData.source_type values. "warehouse"
# is NOT a KBData source_type (warehouses live in KBWarehouse), so it's handled
# separately by toggling whether warehouse rows are appended.
_SOURCE_CATEGORY_TYPES: dict[str, list[str]] = {
    "doc":   ["doc"],
    "media": ["image", "video"],
    "web":   ["web"],
}


async def list_documents(
    postgres,
    tenant_id: str | None,
    tier: str | None = None,
    cursor: str | None = None,
    source_types: list[str] | None = None,
) -> dict:
    """Stateless keyset page. The client holds the opaque `cursor` token (the keyset
    boundary the server handed back as `next_cursor`); page 1 omits it. The server
    keeps no per-user pagination state.

    `source_types` are the UI filter chips (`doc`/`media`/`web`/`warehouse`), multi-select.
    None  → no source filter (all sources, warehouses included).
    list  → `WHERE source_type IN (...)` over the mapped KBData types; warehouses are
            appended only when 'warehouse' is among the selected chips. An empty list
            selects nothing (the user deselected every chip)."""
    keyset = decode_cursor(cursor)  # {inserted_at, data_id} | None

    # Translate the selected chips into a KBData source_type IN-set + a warehouse flag.
    include_warehouses = source_types is None or "warehouse" in source_types
    kb_source_types: list[str] | None = None  # None ⇒ no source_type filter
    if source_types is not None:
        kb_source_types = [
            st for cat in source_types for st in _SOURCE_CATEGORY_TYPES.get(cat, [])
        ]

    filters = []
    if tier:
        filters.append(WhereFilter(table_name="KBData", column_name="current_tier", value=tier))
    # An explicit (possibly empty) source selection always constrains the query: an
    # empty IN-set yields zero KBData rows, which is the correct "nothing selected" result.
    if kb_source_types is not None:
        filters.append(WhereFilter(
            table_name="KBData", column_name="source_type",
            value=kb_source_types, operator=FilterOperator.IN,
        ))

    resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBData"],
        filters=filters,
        cursor=keyset,
        limit=PAGE_SIZE,
        # Newest documents first — the keyset comparison flips to (inserted_at, pk) <
        # cursor automatically for DESC (see _apply_keyset in postgres_connector).
        order_by=OrderBy(table_name="KBData", column="inserted_at", order=OrderDirection.DESC),
    ))
    if resp.code != 200:
        log.error("list_documents: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Failed to load documents")

    raw = resp.data or []
    has_next    = len(raw) == PAGE_SIZE
    next_cursor = encode_cursor(raw[-1]) if has_next and raw else None
    docs = [DocResponse.model_validate(r).model_dump() for r in raw]

    # Total row count (for the numbered pager) — same filters, no keyset/limit.
    count_resp = await postgres.read_count(ReadJoinRequest(
        tenant_id=tenant_id, joins_table=["KBData"], filters=filters,
    ))
    total = count_resp.data if count_resp.code == 200 else len(docs)

    # Warehouses live in KBWarehouse (no tenant column) — append on gold/all, first page only
    # (cursor is None), and only when the 'warehouse' chip is selected. They don't
    # participate in the keyset, so next_cursor still tracks KBData. `total` stays the
    # KBData count (stable across pages so the pager's page-count doesn't flicker); the
    # handful of warehouses just ride along on the first page.
    if include_warehouses and cursor is None and (not tier or tier == "gold"):
        existing_ids = {d["data_id"] for d in docs}
        wh_resp = await postgres.read(ReadJoinRequest(joins_table=["KBWarehouse"], limit=PAGE_SIZE))
        # KBWarehouse has no creator column — added_by lives on the active config.
        # Map warehouse_id → active config's created_by (the username) so the warehouse
        # row shows who added it instead of an empty field.
        cfg_resp = await postgres.read(ReadJoinRequest(joins_table=["KBWarehouseConfig"], limit=200))
        creator_by_wh = {
            str(c.get("warehouse_id")): (c.get("created_by") or "")
            for c in (cfg_resp.data or [])
            if c.get("is_active")
        }
        for wh in (wh_resp.data or []):
            if wh.get("is_deleted"):
                continue
            if str(wh["warehouse_id"]) in existing_ids:
                continue
            service = (wh.get("service") or "").lower()
            docs.append({
                "data_id":      str(wh["warehouse_id"]),
                "name":         wh.get("description") or f"Warehouse ({service.title()})",
                "source_type":  "warehouse",
                "extension":    None,
                "current_tier": "gold",
                "status":       "PUBLISHED",
                "added_by":     creator_by_wh.get(str(wh["warehouse_id"]), ""),
                "added_on":     str(wh.get("created_at", "")),
                "abstract":     None,
                "metadata": {
                    "doc_type":       f"Warehouse/{service}",
                    "language":       None,
                    "access_role":    None,
                    "warehouse_type": service,
                },
            })

    return build_cursor_page(docs, total, has_next, next_cursor)


async def promote_document(
    postgres, kafka_producer,
    data_id: str, target: str, status: str,
    ctx: dict, record: dict,
) -> dict:
    tenant_id = ctx["tenant_id"]
    if target == "silver":
        await kafka_producer.produce(
            topic=ing_cfg.KAFKA_TOPIC_SILVER,
            value={
                "event":       "promote_silver",
                "data_id":     data_id,
                "tenant_id":   tenant_id or str(record.get("tenant_id", "")),
                "source_type": record.get("source_type", "doc"),
                "extension":   record.get("extension", ""),
                "minio_path":  record.get("path", ""),
                "approved_by": ctx["user_name"],
            },
            key=data_id,
        )
    elif target == "gold":
        await kafka_producer.produce(
            topic=ing_cfg.KAFKA_TOPIC_GOLD,
            value={
                "event":       "promote_gold",
                "data_id":     data_id,
                "tenant_id":   tenant_id or str(record.get("tenant_id", "")),
                "neo4j_connection_id": "",
                "approved_by": ctx["user_name"],
            },
            key=data_id,
        )
    else:
        raise HTTPException(status_code=422, detail=f"Unknown target tier: {target}")

    log.info("promote_document %s → %s by %s", data_id, target, ctx["user_id"])
    # Rebuild the response from the (now-stale) record with the target tier
    # so the caller sees the post-promotion state without an extra DB read.
    record_after = dict(record)
    record_after["current_tier"] = target
    return DocResponse.model_validate(record_after).model_dump()


async def delete_document(postgres, qdrant, data_id: str, tenant_id: str | None, record: dict) -> None:
    """Soft-delete a document: flag KBData AND mirror the is_deleted tag onto the
    document's Qdrant points (chunk points by data_id + the doc-summary point), so a
    'deleted' document is no longer retrievable."""
    tid = tenant_id or str(record.get("tenant_id", ""))
    resp = await postgres.soft_delete(KBDataDelete(tenant_id=tid, data_id=data_id))
    if resp.code != 200:
        log.error("delete_document: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Delete failed")
    await qdrant_set_deleted_by_data_id(qdrant, tid, data_id, True)
    log.info("delete_document %s", data_id)
