"""Business logic for document listing, promotion, and deletion."""
import logging
from fastapi import HTTPException
from basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest, WhereFilter, OrderBy, OrderDirection, KBDataDelete,
)
from services.backend.pipeline.ingestion import config as ing_cfg
from services.backend.UI_model.response import DocResponse
from services.backend.pipeline.kb_ui_operation.pagination import (
    PAGE_SIZE, get_cursor, advance, build_page, clear_cursors,
)

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


async def list_documents(
    postgres,
    tenant_id: str | None,
    user_id: str = "dev-user",
    tier: str | None = None,
    page: int = 1,
) -> dict:
    cursor_key = f"docs:{tier or 'all'}"
    cursor_ts  = get_cursor(user_id, cursor_key, page)

    filters = []
    if tier:
        filters.append(WhereFilter(table_name="KBData", column_name="current_tier", value=tier))

    resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBData"],
        filters=filters,
        cursor=cursor_ts,
        limit=PAGE_SIZE,
        order_by=OrderBy(table_name="KBData", column="inserted_at", order=OrderDirection.ASC),
    ))
    if resp.code != 200:
        log.error("list_documents: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Failed to load documents")

    raw = resp.data or []
    advance(user_id, cursor_key, page, raw)
    docs = [DocResponse.model_validate(r).model_dump() for r in raw]

    # Warehouses live in KBWarehouse (no tenant column) — append on gold/all, page 1 only
    if page == 1 and (not tier or tier == "gold"):
        existing_ids = {d["data_id"] for d in docs}
        wh_resp = await postgres.read(ReadJoinRequest(joins_table=["KBWarehouse"], limit=PAGE_SIZE))
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
                "added_by":     None,
                "added_on":     str(wh.get("created_at", "")),
                "abstract":     None,
                "metadata": {
                    "doc_type":       f"Warehouse/{service}",
                    "language":       None,
                    "access_role":    None,
                    "warehouse_type": service,
                },
            })

    return build_page(docs, page)


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
                "approved_by": ctx["user_id"],
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
                "approved_by": ctx["user_id"],
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


async def delete_document(postgres, data_id: str, tenant_id: str | None, record: dict) -> None:
    resp = await postgres.soft_delete(KBDataDelete(
        tenant_id=tenant_id or str(record.get("tenant_id", "")),
        data_id=data_id,
    ))
    if resp.code != 200:
        log.error("delete_document: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Delete failed")
    log.info("delete_document %s", data_id)
