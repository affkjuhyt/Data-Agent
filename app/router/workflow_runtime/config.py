from fastapi import APIRouter, Request
from typing import Optional
from sqlalchemy import select
from uuid import UUID

from app.pipeline.workflow_runtime.node_registry import NODE_REGISTRY
from services.database_connector.postgres_connector import client
from basemodel.services_databaseconnector.postgres_orm.workflow_orm import LLMProvidersORM, SystemPromptsORM

router = APIRouter()


@router.get("/nodes/registry")
def get_node_registry():
    return NODE_REGISTRY


@router.get("/llm-providers")
async def get_llm_providers(type: Optional[str] = None):
    if not client.is_connected():
        return []
    async with client.get_client() as session:
        stmt = select(LLMProvidersORM).where(LLMProvidersORM.is_active.is_(True))
        if type:
            stmt = stmt.where(LLMProvidersORM.type == type)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {"id": str(r.id), "name": r.name, "model_id": r.model_id,
         "type": r.type, "is_default": r.is_default}
        for r in rows
    ]


@router.get("/kb-connections")
def get_kb_connections(request: Request):
    return []


@router.get("/system-prompts")
async def get_system_prompts(request: Request):
    tenant_id = request.headers.get("X-Tenant-ID", "00000000-0000-0000-0000-000000000001")
    if not client.is_connected():
        return []
    async with client.get_client() as session:
        rows = (await session.execute(
            select(SystemPromptsORM)
            .where(SystemPromptsORM.tenant_id == UUID(tenant_id))
            .where(SystemPromptsORM.is_active.is_(True))
        )).scalars().all()
    return [
        {"id": str(r.id), "name": r.name, "content": r.content,
         "tenant_id": str(r.tenant_id)}
        for r in rows
    ]
