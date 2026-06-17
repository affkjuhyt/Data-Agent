"""/api/knowledge/knowledge-bases — KnowledgeBase CRUD.

A tenant has many knowledge bases (the "kind" of knowledge a document belongs to).
Documents reference one at ingestion; retrieval can be scoped to it.
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.dependencies.auth import authenticate
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.pipeline.kb_ui_operation.knowledge_base_ops import (
    list_knowledge_bases, create_knowledge_base, delete_knowledge_base,
)

router = APIRouter(prefix="/api/knowledge/knowledge-bases", tags=["Knowledge-Bases"])

_DEFAULT_TENANT = "a0000000-0000-0000-0000-000000000001"


class RequestCreateKnowledgeBase(BaseModel):
    name: str
    description: str = ""


@router.get("", response_model=ResponseModel)
async def list_knowledge_bases_route(request: Request, ctx: dict = Depends(authenticate)):
    result = await list_knowledge_bases(
        postgres=svc(request, "postgres"),
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


@router.post("", response_model=ResponseModel)
async def create_knowledge_base_route(
    body: RequestCreateKnowledgeBase, request: Request, ctx: dict = Depends(require_perm("add_data")),
):
    result = await create_knowledge_base(
        postgres=svc(request, "postgres"),
        name=body.name,
        description=body.description,
        tenant_id=ctx["tenant_id"] or _DEFAULT_TENANT,
        user_id=ctx["user_id"],
    )
    return ResponseModel(code=200, data=result)


@router.delete("/{knowledge_base_id}", response_model=ResponseModel)
async def delete_knowledge_base_route(
    knowledge_base_id: str, request: Request, ctx: dict = Depends(require_perm("delete_data")),
):
    await delete_knowledge_base(
        postgres=svc(request, "postgres"),
        knowledge_base_id=knowledge_base_id,
        tenant_id=ctx["tenant_id"] or _DEFAULT_TENANT,
    )
    return ResponseModel(code=200, data=None)
