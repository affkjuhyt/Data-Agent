from typing import Any
import logging
import uuid
from fastapi import HTTPException
from sqlalchemy import select as sa_select, update as sa_update, func as sa_func

from libs.basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest, WhereFilter,
)
from services.kb_backend.operations.pagination import PAGE_SIZE
from services.kb_backend.operations.access import assert_document_access, document_tier
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBTextBlockORM, KBTextBlockVersionORM, KBTextTableORM,
)
from services.kb_backend.schemas import to_string

log = logging.getLogger(__name__)


async def _assert_table_belongs_to_document(postgres: Any, table_id: str, doc_id: str) -> None:
    async with postgres.get_client() as session:
        row = await session.scalar(
            sa_select(KBTextTableORM.version_id)
            .join(KBTextBlockVersionORM, KBTextTableORM.version_id == KBTextBlockVersionORM.version_id)
            .join(KBTextBlockORM, KBTextBlockVersionORM.block_id == KBTextBlockORM.block_id)
            .where(
                KBTextTableORM.version_id == uuid.UUID(table_id),
                KBTextBlockORM.owner_id == uuid.UUID(doc_id),
            )
            .limit(1)
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Table not found")


def _shape_silver_tables(staging: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in (staging or {}).get("chunks") or []:
        if not c.get("table_involved") or not c.get("table"):
            continue
        tbl = c["table"] or {}
        data = tbl.get("data") or {}
        headers = data.get("headers", [])
        rows_raw = data.get("rows", [])
        idx = c.get("block_index", len(out))
        out.append({
            "id": f"silver-{idx}",
            "name": tbl.get("table_name") or f"table_{idx}",
            "description": tbl.get("description", ""),
            "columns": [{"name": h, "type": "text", "nullable": True} for h in headers],
            "rows": [
                dict(zip(headers, r)) if isinstance(r, list) else r
                for r in rows_raw
            ],
            "read_only": True,
        })
    return out


async def get_tables(
        postgres: Any, mongo: Any, doc_id: str, tenant_id: str,
        user_id: str, page: int = 1, role_id: str | None = None,
) -> dict[str, Any]:
    tier = await document_tier(postgres, doc_id, tenant_id, role_id)
    if tier == "silver":
        staging = await mongo.get_client()["kb_silver_staging"].find_one({"data_id": doc_id})
        all_tables = _shape_silver_tables(staging)
        start = (page - 1) * PAGE_SIZE
        return {
            "items": all_tables[start:start + PAGE_SIZE],
            "page": page,
            "page_size": PAGE_SIZE,
            "has_next": len(all_tables) > start + PAGE_SIZE,
        }

    base_filters = (
        KBTextBlockORM.owner_id == uuid.UUID(doc_id),
        KBTextBlockORM.is_deleted.is_(False),
        KBTextBlockVersionORM.is_active.is_(True),
        KBTextBlockVersionORM.is_deleted.is_(False),
        KBTextTableORM.is_deleted.is_(False),
    )
    async with postgres.get_client() as session:
        total = await session.scalar(
            sa_select(sa_func.count())
            .select_from(KBTextTableORM)
            .join(KBTextBlockVersionORM, KBTextTableORM.version_id == KBTextBlockVersionORM.version_id)
            .join(KBTextBlockORM, KBTextBlockVersionORM.block_id == KBTextBlockORM.block_id)
            .where(*base_filters)
        ) or 0
        rows = (await session.execute(
            sa_select(KBTextTableORM)
            .join(KBTextBlockVersionORM, KBTextTableORM.version_id == KBTextBlockVersionORM.version_id)
            .join(KBTextBlockORM, KBTextBlockVersionORM.block_id == KBTextBlockORM.block_id)
            .where(*base_filters)
            .order_by(KBTextBlockORM.block_index.asc())
            .offset((page - 1) * PAGE_SIZE)
            .limit(PAGE_SIZE)
        )).scalars().all()

    tables = []
    for tbl in rows:
        data = tbl.data or {}
        headers = data.get("headers", [])
        rows_raw = data.get("rows", [])
        tables.append({
            "id": to_string(tbl.version_id),
            "name": tbl.table_name or "",
            "description": tbl.description or "",
            "columns": [{"name": h, "type": "text", "nullable": True} for h in headers],
            "rows": [
                dict(zip(headers, r)) if isinstance(r, list) else r
                for r in rows_raw
            ],
        })
    return {
        "items": tables,
        "page": page,
        "page_size": PAGE_SIZE,
        "has_next": (page - 1) * PAGE_SIZE + len(tables) < int(total),
    }


async def update_table_row(postgres: Any, table_id: str, row_index: int, updates: dict[str, Any], doc_id: str,
                           tenant_id: str, role_id: str | None = None) -> dict[str, Any]:
    await assert_document_access(postgres, doc_id, tenant_id, role_id)
    await _assert_table_belongs_to_document(postgres, table_id, doc_id)
    resp = await postgres.read(ReadJoinRequest(
        joins_table=["KBTextTable"],
        filters=[WhereFilter(table_name="KBTextTable", column_name="version_id", value=table_id)],
        limit=1,
    ))
    if resp.code != 200 or not resp.data:
        raise HTTPException(status_code=404, detail="Table not found")

    tbl = resp.data[0]
    data = tbl.get("data") or {}
    rows = list(data.get("rows", []))
    headers = data.get("headers", [])

    if row_index >= len(rows):
        raise HTTPException(status_code=404, detail=f"Row {row_index} not found")

    row = rows[row_index]
    if isinstance(row, list):
        row = dict(zip(headers, row))
    row.update(updates)
    rows[row_index] = row
    data["rows"] = rows

    table_uuid = uuid.UUID(table_id) if isinstance(table_id, str) else table_id
    async with postgres.get_client() as session:
        await session.execute(
            sa_update(KBTextTableORM)
            .where(KBTextTableORM.version_id == table_uuid)
            .values(data=data)
        )
        await session.commit()
    return {"status": "ok", "row": row}
