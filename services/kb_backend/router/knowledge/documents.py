"""
/api/knowledge/documents/{id}/chunks|tables|configs
Serves AssetDetailWorkspace and KnowledgeHub (chunk/version management).
"""
from fastapi import APIRouter, Depends, Query, Request

from libs.basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.schemas import (
    ActivateChunkVersionRequest,
    UpdateTableRowRequest,
    CreateDocConfigRequest,
    CreateChunkVersionRequest,
)
from services.kb_backend.dependencies.auth import authenticate
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.operations.chunk_ops import (
    get_chunks, delete_chunk, activate_chunk_version,
    delete_chunk_version, create_chunk_version,
)
from services.kb_backend.operations.table_ops import get_tables, update_table_row
from services.kb_backend.operations.warehouse_ops import (
    get_warehouse_configs, create_warehouse_config, activate_warehouse_config,
)
from services.kb_backend.operations.document_ops import get_doc_history

router = APIRouter(prefix="/api/knowledge/documents", tags=["Knowledge-Documents"])


# ── Chunks ────────────────────────────────────────────────────────────────────

@router.get("/{doc_id}/chunks", response_model=ResponseModel)
async def get_chunks_route(
    doc_id: str, request: Request, ctx: dict = Depends(authenticate),
    page: int = Query(1, ge=1),
):
    result = await get_chunks(
        svc(request, "postgres"), svc(request, "mongo"), doc_id,
        tenant_id=ctx["tenant_id"], user_id=ctx["user_id"], page=page,
    )
    return ResponseModel(code=200, data=result)


@router.delete("/{doc_id}/chunks/{chunk_id}", response_model=ResponseModel)
async def delete_chunk_route(
    doc_id: str, chunk_id: str,
    request: Request, ctx: dict = Depends(require_perm("delete_data")),
):
    await delete_chunk(svc(request, "postgres"), chunk_id, doc_id=doc_id, tenant_id=ctx["tenant_id"],
                       qdrant=svc(request, "qdrant"))
    return ResponseModel(code=200, data=None)


@router.patch("/{doc_id}/chunks/{chunk_id}/activate", response_model=ResponseModel)
async def activate_chunk_version_route(
    doc_id: str, chunk_id: str,
    body: ActivateChunkVersionRequest,
    request: Request, ctx: dict = Depends(require_perm("add_chunk_version")),
):
    result = await activate_chunk_version(
        postgres=svc(request, "postgres"),
        chunk_id=chunk_id,
        version_number=body.version_number,
        doc_id=doc_id,
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


@router.delete("/{doc_id}/chunks/{chunk_id}/versions/{version_number}", response_model=ResponseModel)
async def delete_chunk_version_route(
    doc_id: str, chunk_id: str, version_number: int,
    request: Request, ctx: dict = Depends(require_perm("delete_data")),
):
    await delete_chunk_version(
        svc(request, "postgres"), chunk_id, version_number,
        doc_id=doc_id, tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=None)


@router.post("/{doc_id}/chunks/{chunk_id}/versions", response_model=ResponseModel)
async def create_chunk_version_route(
    doc_id: str, chunk_id: str,
    body: CreateChunkVersionRequest,
    request: Request, ctx: dict = Depends(require_perm("add_chunk_version")),
):
    result = await create_chunk_version(
        postgres=svc(request, "postgres"),
        chunk_id=chunk_id,
        text=body.text,
        entities=body.entities,
        intents=body.intents,
        created_by=ctx["user_name"],
        doc_id=doc_id,
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


# ── Tables ────────────────────────────────────────────────────────────────────

@router.get("/{doc_id}/tables", response_model=ResponseModel)
async def get_tables_route(
    doc_id: str, request: Request, ctx: dict = Depends(authenticate),
    page: int = Query(1, ge=1),
):
    result = await get_tables(
        svc(request, "postgres"), svc(request, "mongo"), doc_id,
        tenant_id=ctx["tenant_id"], user_id=ctx["user_id"], page=page,
    )
    return ResponseModel(code=200, data=result)


@router.patch("/{doc_id}/tables/{table_id}/rows/{row_index}", response_model=ResponseModel)
async def update_table_row_route(
    doc_id: str, table_id: str, row_index: int,
    body: UpdateTableRowRequest,
    request: Request, ctx: dict = Depends(authenticate),
):
    result = await update_table_row(
        svc(request, "postgres"), table_id, row_index, body.model_dump(),
        doc_id=doc_id, tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


# ── Configs (aliases for AssetDetailWorkspace warehouse docs) ─────────────────

@router.get("/{doc_id}/configs", response_model=ResponseModel)
async def get_doc_configs(doc_id: str, request: Request, ctx: dict = Depends(authenticate)):
    result = await get_warehouse_configs(svc(request, "postgres"), doc_id, tenant_id=ctx["tenant_id"])
    return ResponseModel(code=200, data=result)


@router.post("/{doc_id}/configs", response_model=ResponseModel)
async def create_doc_config(
    doc_id: str, body: CreateDocConfigRequest,
    request: Request, ctx: dict = Depends(require_perm("add_warehouse_config")),
):
    result = await create_warehouse_config(
        postgres=svc(request, "postgres"),
        warehouse_id=doc_id,
        body=body.model_dump(),
        created_by=ctx["user_name"],
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


@router.patch("/{doc_id}/configs/{config_id}/activate", response_model=ResponseModel)
async def activate_doc_config(
    doc_id: str, config_id: str,
    request: Request, ctx: dict = Depends(require_perm("edit_warehouse_config")),
):
    result = await activate_warehouse_config(
        svc(request, "postgres"), doc_id, config_id, tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


# ── Lifecycle History ──────────────────────────────────────────────────────────

@router.get("/{doc_id}/history", response_model=ResponseModel)
async def get_doc_history_route(doc_id: str, request: Request, ctx: dict = Depends(authenticate)):
    result = await get_doc_history(svc(request, "postgres"), doc_id, tenant_id=ctx["tenant_id"])
    return ResponseModel(code=200, data=result)

