"""Business logic for KnowledgeBase CRUD.

A knowledge base is the "kind" of knowledge a document belongs to (e.g. finance,
legal, engineering). A tenant has many knowledge bases; documents reference one so
retrieval can be scoped to it.
"""
import logging

from fastapi import HTTPException

from basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest,
    KBKnowledgeBaseInsert,
    KBKnowledgeBaseDelete,
)
from services.kb_backend.UI_model.response import to_string

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
    user_id: str,
) -> dict:
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Knowledge base name is required")
    resp = await postgres.insert(KBKnowledgeBaseInsert(
        tenant_id=tenant_id,
        name=name,
        description=(description or "").strip() or None,
        is_active=True,
        created_by=user_id,
    ))
    if resp.code == 409:
        raise HTTPException(status_code=409, detail=f"Knowledge base '{name}' already exists")
    if resp.code != 200:
        # Unique (tenant_id, name) violation surfaces here too.
        log.error("create_knowledge_base: DB error: %s", resp.error)
        raise HTTPException(status_code=400, detail="Failed to create knowledge base")
    return {"knowledge_base_id": resp.data.get("knowledge_base_id"), "name": name}


async def delete_knowledge_base(postgres, knowledge_base_id: str, tenant_id: str) -> None:
    resp = await postgres.soft_delete(KBKnowledgeBaseDelete(
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
    ))
    if resp.code == 404:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if resp.code != 200:
        log.error("delete_knowledge_base: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Failed to delete knowledge base")
