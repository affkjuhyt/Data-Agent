from typing import Any, cast
import logging
import uuid
from fastapi import HTTPException
from sqlalchemy import select as sa_select
from libs.basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest, WhereFilter, OrderBy, OrderDirection, KBDataDelete, FilterOperator,
)
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBLifecycleHistoryORM, KBDataORM,
)
from services.kb_backend.pipeline.ingestion import config as ing_cfg
from services.kb_backend.schemas import DocResponse, to_string
from services.kb_backend.operations.pagination import (
    PAGE_SIZE, decode_cursor, encode_cursor, build_cursor_page,
)
from services.kb_backend.operations.trash_ops import (
    qdrant_set_deleted_by_data_id,
    set_postgres_deleted,
)
from services.kb_backend.operations.access import assert_document_access, document_role_filter

log = logging.getLogger(__name__)


async def fetch_one(postgres: Any, data_id: str, tenant_id: str | None, role_id: str | None = None) -> dict[
                                                                                                           str, Any] | None:
    resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBData"],
        filters=[
            WhereFilter(table_name="KBData", column_name="data_id", value=data_id),
            *document_role_filter(role_id),
        ],
        limit=1,
    ))
    if resp.code == 200 and resp.data:
        return cast(dict[str, Any], resp.data[0])
    return None


async def fetch_one_or_404(postgres: Any, data_id: str, tenant_id: str | None, role_id: str | None = None) -> dict[
    str, Any]:
    record = await fetch_one(postgres, data_id, tenant_id, role_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {data_id}")
    return record


async def name_exists(postgres: Any, tenant_id: str, name: str) -> bool:
    resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBData"],
        filters=[WhereFilter(table_name="KBData", column_name="name", value=name)],
        limit=1,
    ))
    return resp.code == 200 and bool(resp.data)


async def get_doc_history(postgres: Any, doc_id: str, tenant_id: str, role_id: str | None = None) -> list[
    dict[str, Any]]:
    try:
        data_uuid = uuid.UUID(doc_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid doc_id")
    try:
        uuid.UUID(tenant_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid tenant_id")

    await assert_document_access(postgres, doc_id, tenant_id, role_id)

    async with postgres.get_client() as session:
        rows = (await session.execute(
            sa_select(KBLifecycleHistoryORM)
            .where(
                KBLifecycleHistoryORM.data_id == data_uuid,
                KBLifecycleHistoryORM.is_deleted == False,  # noqa: E712
            )
            .order_by(KBLifecycleHistoryORM.transitioned_at.desc())
        )).scalars().all()

    return [
        {
            "history_id": to_string(row.history_id),
            "from_tier": row.from_tier,
            "to_tier": row.to_tier,
            "transitioned_at": row.transitioned_at.isoformat() if row.transitioned_at else None,
            "approved_by": row.approved_by,
            "notes": row.notes,
            "pipeline_metadata": row.pipeline_metadata,
        }
        for row in rows
    ]


_SOURCE_CATEGORY_TYPES: dict[str, list[str]] = {
    "doc": ["doc"],
    "media": ["image", "video"],
    "web": ["web"],
}


async def list_documents(
        postgres: Any,
        tenant_id: str | None,
        role_id: str | None = None,
        tier: str | None = None,
        cursor: str | None = None,
        source_types: list[str] | None = None,
) -> dict[str, Any]:
    keyset = decode_cursor(cursor)

    include_warehouses = source_types is None or "warehouse" in source_types
    kb_source_types: list[str] | None = None
    if source_types is not None:
        kb_source_types = [
            st for cat in source_types for st in _SOURCE_CATEGORY_TYPES.get(cat, [])
        ]

    filters = list(document_role_filter(role_id))
    if tier:
        filters.append(WhereFilter(table_name="KBData", column_name="current_tier", value=tier))
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
        order_by=OrderBy(table_name="KBData", column="inserted_at", order=OrderDirection.DESC),
    ))
    if resp.code != 200:
        log.error("list_documents: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Failed to load documents")

    raw = resp.data or []
    has_next = len(raw) == PAGE_SIZE
    next_cursor = encode_cursor(raw[-1]) if has_next and raw else None
    docs = [DocResponse.model_validate(r).model_dump() for r in raw]

    count_resp = await postgres.read_count(ReadJoinRequest(
        tenant_id=tenant_id, joins_table=["KBData"], filters=filters,
    ))
    total = count_resp.data if count_resp.code == 200 else len(docs)

    if include_warehouses and cursor is None and (not tier or tier == "gold"):
        existing_ids = {d["data_id"] for d in docs}
        wh_resp = await postgres.read(ReadJoinRequest(joins_table=["KBWarehouse"], limit=PAGE_SIZE))
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
                "data_id": str(wh["warehouse_id"]),
                "name": wh.get("description") or f"Warehouse ({service.title()})",
                "source_type": "warehouse",
                "extension": None,
                "current_tier": "gold",
                "status": "PUBLISHED",
                "added_by": creator_by_wh.get(str(wh["warehouse_id"]), ""),
                "added_on": str(wh.get("created_at", "")),
                "abstract": None,
                "metadata": {
                    "doc_type": f"Warehouse/{service}",
                    "language": None,
                    "access_role": None,
                    "warehouse_type": service,
                },
            })

    return build_cursor_page(docs, total, has_next, next_cursor)


async def promote_document(
        postgres: Any, kafka_producer: Any,
        data_id: str, target: str, status: str,
        ctx: dict[str, Any], record: dict[str, Any],
) -> dict[str, Any]:
    tenant_id = ctx["tenant_id"]
    if target == "silver":
        await kafka_producer.produce(
            topic=ing_cfg.KAFKA_TOPIC_SILVER,
            value={
                "event": "promote_silver",
                "data_id": data_id,
                "tenant_id": tenant_id or str(record.get("tenant_id", "")),
                "source_type": record.get("source_type", "doc"),
                "extension": record.get("extension", ""),
                "minio_path": record.get("path", ""),
                "approved_by": ctx["user_name"],
            },
            key=data_id,
        )
    elif target == "gold":
        await kafka_producer.produce(
            topic=ing_cfg.KAFKA_TOPIC_GOLD,
            value={
                "event": "promote_gold",
                "data_id": data_id,
                "tenant_id": tenant_id or str(record.get("tenant_id", "")),
                "neo4j_connection_id": "",
                "approved_by": ctx["user_name"],
            },
            key=data_id,
        )
    else:
        raise HTTPException(status_code=422, detail=f"Unknown target tier: {target}")

    log.info("promote_document %s → %s by %s", data_id, target, ctx["user_id"])
    record_after = dict(record)
    record_after["current_tier"] = target
    return cast(dict[str, Any], DocResponse.model_validate(record_after).model_dump())


async def reprocess_gold_documents(
        postgres: Any, kafka_producer: Any, ctx: dict[str, Any],
) -> dict[str, Any]:
    tenant_id = ctx["tenant_id"]
    tenant_uuid = uuid.UUID(tenant_id)
    async with postgres.get_client() as session:
        result = await session.execute(
            sa_select(KBDataORM.data_id).where(
                KBDataORM.tenant_id == tenant_uuid,
                KBDataORM.current_tier == "gold",
                KBDataORM.is_deleted.is_(False),
            )
        )
        data_ids = [str(row[0]) for row in result.all()]

    enqueued: list[str] = []
    failed: list[str] = []
    for data_id in data_ids:
        try:
            await kafka_producer.produce(
                topic=ing_cfg.KAFKA_TOPIC_GOLD,
                value={
                    "event": "promote_gold",
                    "data_id": data_id,
                    "tenant_id": tenant_id,
                    "approved_by": ctx["user_name"],
                },
                key=data_id,
            )
            enqueued.append(data_id)
        except Exception as exc:
            log.error("reprocess_gold: enqueue failed data_id=%s: %s", data_id, exc)
            failed.append(data_id)

    log.info(
        "reprocess_gold tenant=%s total=%d enqueued=%d failed=%d by=%s",
        tenant_id, len(data_ids), len(enqueued), len(failed), ctx["user_id"],
    )
    return {
        "total": len(data_ids),
        "enqueued": len(enqueued),
        "failed": len(failed),
        "data_ids": enqueued,
        "failed_ids": failed,
    }


async def delete_document(postgres: Any, qdrant: Any, data_id: str, tenant_id: str | None,
                          record: dict[str, Any]) -> None:
    document_tenant_id = tenant_id or str(record.get("tenant_id", ""))
    response = await postgres.soft_delete(KBDataDelete(tenant_id=document_tenant_id, data_id=data_id))
    if response.code != 200:
        log.error("delete_document: DB error: %s", response.error)
        raise HTTPException(status_code=500, detail="Delete failed")

    try:
        await qdrant_set_deleted_by_data_id(qdrant, document_tenant_id, data_id, True)
    except Exception as exc:
        log.error("delete_document %s: Qdrant mirror failed (%s) — restoring", data_id, exc)
        try:
            await set_postgres_deleted(postgres, "document", uuid.UUID(data_id), False)
        except Exception as rollback_exc:
            log.error("delete_document %s: ROLLBACK FAILED (%s) — deleted in Postgres, "
                      "still searchable", data_id, rollback_exc)
        raise HTTPException(status_code=503, detail="Delete failed — vector store unavailable")
    log.info("delete_document %s", data_id)
