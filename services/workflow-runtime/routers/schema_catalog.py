"""Governance Catalog read endpoints for Schema Catalog (sandbox_schema_docs.docx
§2.6 — "cung cấp dữ liệu cho giao diện quản lý catalog của platform, nơi
operator có thể xem danh sách bảng, trạng thái crawl, số hàng, và thời điểm
cập nhật cuối").

services/schema-crawler-worker is the only write path (doc §2.2 — worker is
the write path, agents/operators are the read path); there is no PUT/DELETE
here, unlike routers/schema_memory.py's Admin CRUD.
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from basemodel.services_databaseconnector.postgres_orm.workflow_orm import SchemaCatalogORM
from routers.catalog import _resolve_tenant

logger = logging.getLogger(__name__)
router = APIRouter()


def _serialize(row: SchemaCatalogORM) -> dict:
    return {
        "id": str(row.id),
        "warehouse_id": str(row.warehouse_id),
        "schema_name": row.schema_name,
        "table_name": row.table_name,
        "columns": row.columns,
        "row_count": row.row_count,
        "last_altered": row.last_altered.isoformat() if row.last_altered else None,
        "text_description": row.text_description,
        "join_hints": row.join_hints,
        "schema_hash": row.schema_hash,
        "last_crawled_at": row.last_crawled_at.isoformat(),
    }


@router.get("/api/schema-catalog")
async def list_schema_catalog(
    request: Request,
    warehouse_id: Optional[str] = None,
    schema_name: Optional[str] = None,
):
    tenant_id = _resolve_tenant(request.headers.get("X-Tenant-ID"))
    from services.database_connector.postgres_connector import client
    if not client.is_connected():
        raise HTTPException(status_code=503, detail="database not connected")

    async with client.get_client(tenant_id=tenant_id) as session:
        q = select(SchemaCatalogORM).where(SchemaCatalogORM.tenant_id == UUID(tenant_id))
        if warehouse_id:
            q = q.where(SchemaCatalogORM.warehouse_id == UUID(warehouse_id))
        if schema_name:
            q = q.where(SchemaCatalogORM.schema_name == schema_name)
        rows = (await session.execute(q)).scalars().all()
        # Build the response inside the session block — get_client()'s
        # finally clause rolls back (expiring loaded instances) before
        # closing, so attribute access after this `async with` exits raises
        # DetachedInstanceError (same convention as routers/schema_memory.py).
        return [_serialize(r) for r in rows]


@router.get("/api/schema-catalog/{catalog_id}")
async def get_schema_catalog_entry(catalog_id: str, request: Request):
    tenant_id = _resolve_tenant(request.headers.get("X-Tenant-ID"))
    from services.database_connector.postgres_connector import client
    if not client.is_connected():
        raise HTTPException(status_code=503, detail="database not connected")

    async with client.get_client(tenant_id=tenant_id) as session:
        row = (await session.execute(
            select(SchemaCatalogORM)
            .where(SchemaCatalogORM.tenant_id == UUID(tenant_id))
            .where(SchemaCatalogORM.id == UUID(catalog_id))
        )).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        return _serialize(row)
