"""Business logic for KnowledgeBase CRUD.

A knowledge base is the "kind" of knowledge a document belongs to (e.g. finance,
legal, engineering). A tenant has many knowledge bases; documents reference one so
retrieval can be scoped to it.
"""
import logging
import uuid

from fastapi import HTTPException
from sqlalchemy import update as sa_update

from libs.basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest,
    KBKnowledgeBaseInsert,
    KBKnowledgeBaseDelete,
)
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBDataORM,
)
from services.kb_backend.schemas import to_string

# The seeded default knowledge base — cannot be deleted.
_DEFAULT_KB_ID = uuid.UUID("00000000-0000-0003-0000-000000000001")

log = logging.getLogger(__name__)


def _to_knowledge_base(row: dict) -> dict:
    return {
        "id":          to_string(row.get("knowledge_base_id")),
        "name":        row.get("name", ""),
        "description": row.get("description") or "",
        "active":      bool(row.get("is_active", True)),
        "added_by":    row.get("created_by") or "",
        "added_when":  to_string(row.get("created_at", ""))[:10],
    }


async def list_knowledge_bases(postgres, tenant_id: str | None) -> list:
    resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id, joins_table=["KBKnowledgeBase"], limit=500,
    ))
    if resp.code != 200:
        log.error("list_knowledge_bases: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Failed to load knowledge bases")
    rows = [r for r in (resp.data or []) if not r.get("is_deleted")]
    # Newest first for a stable management view.
    rows.sort(key=lambda r: to_string(r.get("created_at", "")), reverse=True)
    return [_to_knowledge_base(r) for r in rows]


async def create_knowledge_base(
    postgres,
    name: str,
    description: str,
    tenant_id: str,
    created_by: str,
) -> dict:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Knowledge base name is required")
    resp = await postgres.insert(KBKnowledgeBaseInsert(
        tenant_id=tenant_id,
        name=name,
        description=(description or "").strip() or None,
        is_active=True,
        created_by=created_by,
    ))
    if resp.code == 409:
        raise HTTPException(status_code=409, detail=f"Knowledge base '{name}' already exists")
    if resp.code != 200:
        # Unique (tenant_id, name) violation surfaces here too.
        log.error("create_knowledge_base: DB error: %s", resp.error)
        raise HTTPException(status_code=400, detail="Failed to create knowledge base")
    return {"knowledge_base_id": resp.data.get("knowledge_base_id"), "name": name}


async def delete_knowledge_base(postgres, knowledge_base_id: str, tenant_id: str) -> None:
    try:
        kb_uuid = uuid.UUID(knowledge_base_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid knowledge_base_id")

    if kb_uuid == _DEFAULT_KB_ID:
        raise HTTPException(status_code=403, detail="The default knowledge base cannot be deleted")

    tenant_uuid = uuid.UUID(tenant_id)

    # Reassign all documents that belong to this group back to the default KB.
    async with postgres.get_client(tenant_id=tenant_id) as session:
        await session.execute(
            sa_update(KBDataORM)
            .where(
                KBDataORM.tenant_id == tenant_uuid,
                KBDataORM.knowledge_base_id == kb_uuid,
                KBDataORM.is_deleted == False,
            )
            .values(knowledge_base_id=_DEFAULT_KB_ID)
        )
        await session.commit()
    log.info(
        "delete_knowledge_base: reassigned documents from %s to default KB", knowledge_base_id
    )

    resp = await postgres.soft_delete(KBKnowledgeBaseDelete(
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
    ))
    if resp.code == 404:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if resp.code != 200:
        log.error("delete_knowledge_base: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Failed to delete knowledge base")
