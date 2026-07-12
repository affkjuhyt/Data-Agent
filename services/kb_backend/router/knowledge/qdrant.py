"""/api/knowledge/qdrant — collection management + semantic search."""
from fastapi import APIRouter, Depends, Request

from basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.schemas import ToggleQdrantCollectionRequest, QdrantSearchRequest
from services.kb_backend.dependencies.auth import authenticate
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.operations.qdrant_ops import (
    list_collections, toggle_collection, semantic_search,
)

router = APIRouter(prefix="/api/knowledge/qdrant", tags=["Knowledge-Qdrant"])


@router.get("/collections", response_model=ResponseModel)
async def list_collections_route(request: Request, ctx: dict = Depends(authenticate)):
    result = await list_collections(
        postgres=svc(request, "postgres"),
        qdrant=svc(request, "qdrant"),
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


@router.patch("/collections/{collection_id}", response_model=ResponseModel)
async def toggle_collection_route(
    collection_id: str, body: ToggleQdrantCollectionRequest,
    request: Request, ctx: dict = Depends(require_perm("toggle_qdrant")),
):
    result = await toggle_collection(
        postgres=svc(request, "postgres"),
        collection_id=collection_id,
        is_active=body.active,
    )
    return ResponseModel(code=200, data=result)


@router.post("/collections/{collection_id}/search", response_model=ResponseModel)
async def semantic_search_route(
    collection_id: str, body: QdrantSearchRequest,
    request: Request, ctx: dict = Depends(authenticate),
):
    result = await semantic_search(
        qdrant=svc(request, "qdrant"),
        postgres=svc(request, "postgres"),
        model=svc(request, "model").get_client(),
        collection_id=collection_id,
        query=body.query,
        tenant_id=body.tenant_id or ctx["tenant_id"] or "",
        limit=body.limit,
    )
    return ResponseModel(code=200, data=result)
