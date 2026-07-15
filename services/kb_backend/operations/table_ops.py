"""Business logic for table read and inline cell editing."""
import logging
import uuid
from fastapi import HTTPException
from sqlalchemy import select as sa_select, update as sa_update, func as sa_func

from libs.basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest, WhereFilter,
)
from services.kb_backend.operations.pagination import PAGE_SIZE
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBDataORM, KBTextBlockORM, KBTextBlockVersionORM, KBTextTableORM,
)
from services.kb_backend.schemas import to_string

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


def _shape_silver_tables(staging: dict | None) -> list[dict]:
    """Shape Mongo kb_silver_staging chunk tables into the UI table shape.
    Silver tables aren't in Postgres yet (KBTextTable is written at Gold), so the
    staging chunk's `table` ({table_name, description, data:{headers, rows}}) is the
    source. Read-only at this tier — inline editing happens after Gold promotion."""
    out: list[dict] = []
    for c in (staging or {}).get("chunks") or []:
        if not c.get("table_involved") or not c.get("table"):
            continue
        tbl = c["table"] or {}
        data = tbl.get("data") or {}
        headers = data.get("headers", [])
        rows_raw = data.get("rows", [])
        idx = c.get("block_index", len(out))
        out.append({
            "id":          f"silver-{idx}",
            "name":        tbl.get("table_name") or f"table_{idx}",
            "description": tbl.get("description", ""),
            "columns":     [{"name": h, "type": "text", "nullable": True} for h in headers],
            "rows": [
                dict(zip(headers, r)) if isinstance(r, list) else r
                for r in rows_raw
            ],
            "read_only":   True,
        })
    return out


async def get_tables(
    postgres, mongo, doc_id: str, tenant_id: str,
    user_id: str, page: int = 1,
) -> dict:
    # Silver tables live only in Mongo staging; Postgres KBTextTable is written at
    # Gold. Read from whichever store matches the document's tier (mirrors get_chunks).
    tier = await _doc_tier(postgres, doc_id, tenant_id)
    if tier == "silver":
        staging = await mongo.get_client()["kb_silver_staging"].find_one({"data_id": doc_id})
        all_tables = _shape_silver_tables(staging)
        start = (page - 1) * PAGE_SIZE
        return {
            "items":     all_tables[start:start + PAGE_SIZE],
            "page":      page,
            "page_size": PAGE_SIZE,
            "has_next":  len(all_tables) > start + PAGE_SIZE,
        }

    # Gold: paginate at the TABLE level, not the block level. Tables are a SPARSE
    # subset of blocks (a 105-block doc may carry 8 tables, none in the first 50
    # blocks), so block-keyset pagination returned the wrong per-page count and a
    # bogus has_next (len(items)==PAGE_SIZE is never true for sparse tables). Query
    # KBTextTable directly — joined to its active, non-deleted block version — and
    # offset/limit on the tables themselves. Order by block_index for document order.
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
            "id":          to_string(tbl.version_id),
            "name":        tbl.table_name or "",
            "description": tbl.description or "",
            "columns":     [{"name": h, "type": "text", "nullable": True} for h in headers],
            "rows": [
                dict(zip(headers, r)) if isinstance(r, list) else r
                for r in rows_raw
            ],
        })
    return {
        "items":     tables,
        "page":      page,
        "page_size": PAGE_SIZE,
        "has_next":  (page - 1) * PAGE_SIZE + len(tables) < int(total),
    }


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
