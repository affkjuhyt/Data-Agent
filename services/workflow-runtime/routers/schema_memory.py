"""Admin CRUD for Schema Memory (doc §3.7).

The agent only ever reads Schema Memory (see workflow_memory.retrieve_schema_memory,
called from the text_to_sql handler) — these endpoints are the only write path,
used by an Admin when a data source's schema changes.
"""
import logging
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, select

from basemodel.services_databaseconnector.postgres_orm.workflow_orm import SchemaMemoryORM
from routers.catalog import _resolve_tenant

logger = logging.getLogger(__name__)
router = APIRouter()


class ColumnMeta(BaseModel):
    name: str
    type: str
    description: Optional[str] = None
    sample_values: Optional[list] = None
    is_nullable: bool = True


class SchemaMemoryUpsert(BaseModel):
    data_source: str
    table_name: str
    column_metadata: list[ColumnMeta]
    business_description: str
    access_roles: list[str] = []


@router.get("/api/schema-memory")
async def list_schema_memory(request: Request, data_source: Optional[str] = None):
    tenant_id = _resolve_tenant(request.headers.get("X-Tenant-ID"))
    from services.database_connector.postgres_connector import client
    if not client.is_connected():
        raise HTTPException(status_code=503, detail="database not connected")

    async with client.get_client(tenant_id=tenant_id) as session:
        q = select(SchemaMemoryORM).where(SchemaMemoryORM.tenant_id == UUID(tenant_id))
        if data_source:
            q = q.where(SchemaMemoryORM.data_source == data_source)
        rows = (await session.execute(q)).scalars().all()
        # Build the response inside the session block — get_client()'s
        # finally clause rolls back (which expires loaded instances) before
        # closing, so attribute access after this `async with` exits raises
        # DetachedInstanceError.
        return [
            {
                "id": str(r.id),
                "data_source": r.data_source,
                "table_name": r.table_name,
                "column_metadata": r.column_metadata,
                "business_description": r.business_description,
                "access_roles": r.access_roles,
                "updated_at": r.updated_at.isoformat(),
            }
            for r in rows
        ]


@router.put("/api/schema-memory")
async def upsert_schema_memory(payload: SchemaMemoryUpsert, request: Request):
    """Upsert by (tenant_id, data_source, table_name) — matches uq_schemamemory_table."""
    tenant_id = _resolve_tenant(request.headers.get("X-Tenant-ID"))
    from services.database_connector.postgres_connector import client
    if not client.is_connected():
        raise HTTPException(status_code=503, detail="database not connected")

    column_metadata = [c.model_dump() for c in payload.column_metadata]

    async with client.get_client(tenant_id=tenant_id) as session:
        existing = (await session.execute(
            select(SchemaMemoryORM)
            .where(SchemaMemoryORM.tenant_id == UUID(tenant_id))
            .where(SchemaMemoryORM.data_source == payload.data_source)
            .where(SchemaMemoryORM.table_name == payload.table_name)
        )).scalar_one_or_none()

        if existing:
            existing.column_metadata = column_metadata
            existing.business_description = payload.business_description
            existing.access_roles = payload.access_roles
            row_id = existing.id
        else:
            row_id = uuid4()
            session.add(SchemaMemoryORM(
                id=row_id,
                tenant_id=UUID(tenant_id),
                data_source=payload.data_source,
                table_name=payload.table_name,
                column_metadata=column_metadata,
                business_description=payload.business_description,
                access_roles=payload.access_roles,
            ))
        await session.commit()

    logger.info("schema_memory | upserted table=%s data_source=%s tenant=%s",
                payload.table_name, payload.data_source, tenant_id)
    return {"id": str(row_id)}


@router.delete("/api/schema-memory/{schema_memory_id}")
async def delete_schema_memory(schema_memory_id: str, request: Request):
    tenant_id = _resolve_tenant(request.headers.get("X-Tenant-ID"))
    from services.database_connector.postgres_connector import client
    if not client.is_connected():
        raise HTTPException(status_code=503, detail="database not connected")

    async with client.get_client(tenant_id=tenant_id) as session:
        result = await session.execute(
            delete(SchemaMemoryORM)
            .where(SchemaMemoryORM.tenant_id == UUID(tenant_id))
            .where(SchemaMemoryORM.id == UUID(schema_memory_id))
        )
        await session.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="not found")
    return {"deleted": True}
