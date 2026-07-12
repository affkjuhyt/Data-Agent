"""
/api/data/* — document list, promote, delete.
These are the primary routes consumed by AppStateContext and AssetInventory.
"""
from fastapi import APIRouter, Depends, Query, Request

from basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.schemas import UpdateDocumentRequest
from services.kb_backend.dependencies.auth import authenticate
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.operations.document_ops import (
    fetch_one_or_404, list_documents, promote_document, delete_document,
)

router = APIRouter(prefix="/api/data", tags=["Data"])


@router.get("/documents", response_model=ResponseModel)
async def list_documents_route(
    request: Request,
    ctx: dict = Depends(authenticate),
    tier: str | None = Query(None, description="bronze|silver|gold — omit for all"),
    cursor: str | None = Query(None, description="opaque keyset cursor; omit for first page"),
    source: str | None = Query(
        None,
        description="comma-separated source chips (doc,media,web,warehouse). "
                    "Omit for all sources; empty string selects nothing.",
    ),
):
    # None → no source filter; otherwise split (an empty string → [] → selects nothing).
    source_types = None if source is None else [s for s in source.split(",") if s]
    result = await list_documents(
        postgres=svc(request, "postgres"),
        tenant_id=ctx["tenant_id"],
        tier=tier,
        cursor=cursor,
        source_types=source_types,
    )
    return ResponseModel(code=200, data=result)


@router.patch("/documents/{data_id}", response_model=ResponseModel)
async def promote_document_route(
    data_id: str,
    body: UpdateDocumentRequest,
    request: Request,
    ctx: dict = Depends(require_perm("process_layer")),
):
    postgres = svc(request, "postgres")
    record = await fetch_one_or_404(postgres, data_id, ctx["tenant_id"])
    result = await promote_document(
        postgres=postgres,
        kafka_producer=svc(request, "kafka_producer"),
        data_id=data_id,
        target=body.current_tier.lower() if body.current_tier else "",
        status=body.status or "",
        ctx=ctx,
        record=record,
    )
    return ResponseModel(code=200, data=result)


@router.delete("/documents/{data_id}", response_model=ResponseModel)
async def delete_document_route(
    data_id: str,
    request: Request,
    ctx: dict = Depends(require_perm("delete_data")),
):
    postgres = svc(request, "postgres")
    record = await fetch_one_or_404(postgres, data_id, ctx["tenant_id"])
    await delete_document(
        postgres=postgres,
        qdrant=svc(request, "qdrant"),
        data_id=data_id,
        tenant_id=ctx["tenant_id"],
        record=record,
    )
    return ResponseModel(code=200, data=None)
