"""Business logic for table read and inline cell editing."""
import logging
import uuid
from fastapi import HTTPException
from sqlalchemy import select as sa_select, update as sa_update

from basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest, SelectInLoadRequest, WhereFilter, OrderBy, OrderDirection,
)
from services.backend.pipeline.kb_ui_operation.pagination import (
    PAGE_SIZE, get_cursor, advance, build_page,
)
from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBDataORM, KBTextTableORM,
)
from services.backend.UI_model.response import to_string

log = logging.getLogger(__name__)


async def _assert_tenant(postgres, doc_id: str, tenant_id: str) -> None:
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


async def get_tables(
    postgres, doc_id: str, tenant_id: str,
    user_id: str = "dev-user", page: int = 1,
) -> dict:
    await _assert_tenant(postgres, doc_id, tenant_id)
    cursor_key = f"tables:{doc_id}"
    cursor_ts  = get_cursor(user_id, cursor_key, page)

    resp = await postgres.read_deep(SelectInLoadRequest(
        table="KBTextBlock",
        load_paths=["KBTextBlockVersion.KBTextTable"],
        filters=[WhereFilter(table_name="KBTextBlock", column_name="owner_id", value=doc_id)],
        cursor=cursor_ts,
        limit=PAGE_SIZE,
        order_by=OrderBy(table_name="KBTextBlock", column="inserted_at", order=OrderDirection.ASC),
    ))
    if resp.code != 200:
        log.error("get_tables: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Failed to load tables")

    raw = resp.data or []
    advance(user_id, cursor_key, page, raw)

    tables = []
    for block in raw:
        for version in (block.get("KBTextBlockVersion") or []):
            tbl = version.get("KBTextTable")
            if not tbl:
                continue
            data = tbl.get("data") or {}
            headers = data.get("headers", [])
            rows_raw = data.get("rows", [])
            tables.append({
                "id":          to_string(tbl.get("version_id")),
                "name":        tbl.get("table_name", ""),
                "description": tbl.get("description", ""),
                "columns":     [{"name": h, "type": "text", "nullable": True} for h in headers],
                "rows": [
                    dict(zip(headers, r)) if isinstance(r, list) else r
                    for r in rows_raw
                ],
            })
    return build_page(tables, page)


async def update_table_row(postgres, table_id: str, row_index: int, updates: dict, doc_id: str, tenant_id: str) -> dict:
    await _assert_tenant(postgres, doc_id, tenant_id)
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
