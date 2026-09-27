"""
/api/knowledge/documents/{id}/chunks|tables|configs
Serves AssetDetailWorkspace and KnowledgeHub (chunk/version management).
"""
import mimetypes
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Header, Response
from fastapi.responses import Response

from libs.basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.schemas import (
    ActivateChunkVersionRequest,
    UpdateTableRowRequest,
    CreateDocConfigRequest,
    CreateChunkVersionRequest,
)
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import authenticate, require_perm
from services.kb_backend.pipeline.ingestion.config import MINIO_BUCKET
from services.kb_backend.operations.chunk_ops import (
    get_chunks, delete_chunk, activate_chunk_version,
    delete_chunk_version, create_chunk_version,
)
from services.kb_backend.operations.locking import require_if_match, etag
from services.kb_backend.operations.table_ops import get_tables, update_table_row
from services.kb_backend.operations.warehouse_ops import (
    get_warehouse_configs, create_warehouse_config, activate_warehouse_config,
)
from services.kb_backend.operations.document_ops import get_doc_history, fetch_one_or_404

router = APIRouter(prefix="/api/knowledge/documents", tags=["Knowledge-Documents"])


@router.get("/{doc_id}/chunks", response_model=ResponseModel)
async def get_chunks_route(
        doc_id: str,
        request: Request, ctx: dict[str, Any] = Depends(require_perm("viewDocuments")),
        page: int = Query(1, ge=1),
) -> ResponseModel[Any]:
    result = await get_chunks(
        svc(request, "postgres"), svc(request, "mongo"), doc_id,
        tenant_id=ctx["tenant_id"], role_id=ctx["role_id"], user_id=ctx["user_id"], page=page,
    )
    return ResponseModel(code=200, data=result)


@router.delete("/{doc_id}/chunks/{chunk_id}", response_model=ResponseModel)
async def delete_chunk_route(
        doc_id: str,
        chunk_id: str,
        request: Request, ctx: dict[str, Any] = Depends(require_perm("deleteData")),
) -> ResponseModel[Any]:
    await delete_chunk(svc(request, "postgres"), chunk_id, doc_id=doc_id, tenant_id=ctx["tenant_id"],
                       role_id=ctx["role_id"],
                       qdrant=svc(request, "qdrant"))
    return ResponseModel(code=200, data=None)


@router.patch("/{doc_id}/chunks/{chunk_id}/activate", response_model=ResponseModel)
async def activate_chunk_version_route(
        doc_id: str, chunk_id: str,
        body: ActivateChunkVersionRequest,
        request: Request, ctx: dict[str, Any] = Depends(require_perm("addChunkVersion")),
) -> ResponseModel[Any]:
    result = await activate_chunk_version(
        postgres=svc(request, "postgres"),
        chunk_id=chunk_id,
        version_number=body.version_number,
        doc_id=doc_id,
        tenant_id=ctx["tenant_id"], role_id=ctx["role_id"],
    )
    return ResponseModel(code=200, data=result)


@router.delete("/{doc_id}/chunks/{chunk_id}/versions/{version_number}", response_model=ResponseModel)
async def delete_chunk_version_route(
        doc_id: str,
        chunk_id: str,
        version_number: int,
        request: Request, ctx: dict[str, Any] = Depends(require_perm("deleteData")),
) -> ResponseModel[Any]:
    await delete_chunk_version(
        svc(request, "postgres"), chunk_id, version_number,
        doc_id=doc_id, tenant_id=ctx["tenant_id"], role_id=ctx["role_id"],
    )
    return ResponseModel(code=200, data=None)


@router.post("/{doc_id}/chunks/{chunk_id}/versions", response_model=ResponseModel)
async def create_chunk_version_route(
        doc_id: str, chunk_id: str,
        body: CreateChunkVersionRequest,
        request: Request, ctx: dict[str, Any] = Depends(require_perm("addChunkVersion")),
) -> ResponseModel[Any]:
    result = await create_chunk_version(
        postgres=svc(request, "postgres"),
        chunk_id=chunk_id,
        text=body.text,
        entities=body.entities,
        intents=body.intents,
        created_by=ctx["user_name"],
        doc_id=doc_id,
        tenant_id=ctx["tenant_id"], role_id=ctx["role_id"],
    )
    return ResponseModel(code=200, data=result)


@router.get("/{doc_id}/tables", response_model=ResponseModel)
async def get_tables_route(
        doc_id: str,
        request: Request, ctx: dict[str, Any] = Depends(require_perm("viewDocuments")),
        page: int = Query(1, ge=1),
) -> ResponseModel[Any]:
    result = await get_tables(
        svc(request, "postgres"), svc(request, "mongo"), doc_id,
        tenant_id=ctx["tenant_id"], role_id=ctx["role_id"], user_id=ctx["user_id"], page=page,
    )
    return ResponseModel(code=200, data=result)


@router.patch("/{doc_id}/tables/{table_id}/rows/{row_index}", response_model=ResponseModel)
async def update_table_row_route(
        doc_id: str,
        table_id: str,
        row_index: int,
        body: UpdateTableRowRequest,
        request: Request,
        response: Response,
        if_match: str = Header(default="", alias="If-Match"),
        ctx: dict[str, Any] = Depends(require_perm("editTableRow")),
) -> ResponseModel[Any]:
    result = await update_table_row(
        svc(request, "postgres"), table_id, row_index, body.model_dump(),
        doc_id=doc_id, tenant_id=ctx["tenant_id"], role_id=ctx["role_id"],
        expected_version=require_if_match(if_match),
    )
    response.headers["ETag"] = etag(result["row_version"])
    return ResponseModel(code=200, data=result)


@router.get("/{doc_id}/configs", response_model=ResponseModel)
async def get_doc_configs(
        doc_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewDocuments"))
) -> ResponseModel[Any]:
    result = await get_warehouse_configs(svc(request, "postgres"), doc_id, tenant_id=ctx["tenant_id"],
                                         role_id=ctx["role_id"])
    return ResponseModel(code=200, data=result)


@router.post("/{doc_id}/configs", response_model=ResponseModel)
async def create_doc_config(
        doc_id: str,
        body: CreateDocConfigRequest,
        request: Request, ctx: dict[str, Any] = Depends(require_perm("addWarehouseConfig")),
) -> ResponseModel[Any]:
    result = await create_warehouse_config(
        postgres=svc(request, "postgres"),
        warehouse_id=doc_id,
        body=body.model_dump(),
        created_by=ctx["user_name"],
        tenant_id=ctx["tenant_id"], role_id=ctx["role_id"],
    )
    return ResponseModel(code=200, data=result)


@router.patch("/{doc_id}/configs/{config_id}/activate", response_model=ResponseModel)
async def activate_doc_config(
        doc_id: str,
        config_id: str,
        request: Request, ctx: dict[str, Any] = Depends(require_perm("editWarehouseConfig")),
) -> ResponseModel[Any]:
    result = await activate_warehouse_config(
        svc(request, "postgres"), doc_id, config_id, tenant_id=ctx["tenant_id"], role_id=ctx["role_id"],
    )
    return ResponseModel(code=200, data=result)


@router.get("/{doc_id}/history", response_model=ResponseModel)
async def get_doc_history_route(
        doc_id: str,
        request: Request,
        ctx: dict[str, Any] = Depends(require_perm("viewDocuments"))) -> ResponseModel[Any]:
    result = await get_doc_history(svc(request, "postgres"), doc_id, tenant_id=ctx["tenant_id"], role_id=ctx["role_id"])
    return ResponseModel(code=200, data=result)


# ── Original file (citation "open source doc") ─────────────────────────────────

@router.get("/{doc_id}/file")
async def get_document_file_route(doc_id: str, request: Request, ctx: dict[str, Any] = Depends(authenticate)) -> Response:
    """Trả về file gốc đã upload (từ MinIO) — dùng cho tính năng citation (mở
    tài liệu KB gốc). Tenant isolation tái dùng đúng fetch_one_or_404 (404 nếu
    doc_id không thuộc tenant hiện tại) thay vì tự dựng check riêng."""
    record = await fetch_one_or_404(svc(request, "postgres"), doc_id, ctx["tenant_id"])
    data = await svc(request, "minio").download(MINIO_BUCKET, record["path"])
    # Scheduled-source documents are named after the source title (no file
    # extension), so the name alone can't tell PDF from DOCX — the KBData
    # `extension` column can. Guessing from the bare name gave
    # application/octet-stream: PDF preview stayed blank and downloads had
    # no ".pdf" suffix.
    ext = (record.get("extension") or "").lstrip(".").lower()
    file_name = record["name"]
    if ext and not file_name.lower().endswith(f".{ext}"):
        file_name = f"{file_name}.{ext}"
    content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    # HTTP header values are Latin-1 only — a Vietnamese-diacritic name (the
    # common case in this KB) would otherwise raise UnicodeEncodeError deep in
    # Starlette's header encoding and surface as a bare 500. RFC 5987's
    # filename* carries the real UTF-8 name; the plain filename is an
    # ASCII-safe fallback for clients that ignore filename*.
    ascii_name = file_name.encode("ascii", "ignore").decode() or (f"file.{ext}" if ext else "file")
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": (
                f'inline; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(file_name)}"
            )
        },
    )

