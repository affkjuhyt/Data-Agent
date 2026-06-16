"""
/api/knowledge/documents/{id}/chunks|tables|configs
Serves AssetDetailWorkspace and KnowledgeHub (chunk/version management).
"""
import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select as sa_select

from basemodel.services_databaseconnector.shared_model import ResponseModel
from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBLifecycleHistoryORM,
    KBDataORM,
)
from services.kb_backend.UI_model.knowledge import (
    RequestActivateChunkVersion,
    RequestTableRowUpdate,
    RequestDocConfig,
)
from services.kb_backend.dependencies.ui_context import get_ui_context, svc
from services.kb_backend.pipeline.kb_ui_operation.chunk_ops import (
    get_chunks, delete_chunk, activate_chunk_version,
    delete_chunk_version, create_chunk_version,
)
from services.kb_backend.pipeline.kb_ui_operation.table_ops import get_tables, update_table_row
from services.kb_backend.pipeline.kb_ui_operation.warehouse_ops import (
    get_warehouse_configs, create_warehouse_config, activate_warehouse_config,
)
from services.kb_backend.UI_model.response import to_string

router = APIRouter(prefix="/api/knowledge/documents", tags=["Knowledge-Documents"])


class NewVersionBody(BaseModel):
    text: str
    entities: list[str] = []
    intents: list[str] = []
    embedding_model: str = ""


# ── Chunks ────────────────────────────────────────────────────────────────────

@router.get("/{doc_id}/chunks", response_model=ResponseModel)
async def get_chunks_route(
    doc_id: str, request: Request, ctx: dict = Depends(get_ui_context),
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
    request: Request, ctx: dict = Depends(get_ui_context),
):
    await delete_chunk(svc(request, "postgres"), chunk_id, doc_id=doc_id, tenant_id=ctx["tenant_id"],
                       qdrant=svc(request, "qdrant"))
    return ResponseModel(code=200, data=None)


@router.patch("/{doc_id}/chunks/{chunk_id}/activate", response_model=ResponseModel)
async def activate_chunk_version_route(
    doc_id: str, chunk_id: str,
    body: RequestActivateChunkVersion,
    request: Request, ctx: dict = Depends(get_ui_context),
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
    request: Request, ctx: dict = Depends(get_ui_context),
):
    await delete_chunk_version(
        svc(request, "postgres"), chunk_id, version_number,
        doc_id=doc_id, tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=None)


@router.post("/{doc_id}/chunks/{chunk_id}/versions", response_model=ResponseModel)
async def create_chunk_version_route(
    doc_id: str, chunk_id: str,
    body: NewVersionBody,
    request: Request, ctx: dict = Depends(get_ui_context),
):
    result = await create_chunk_version(
        postgres=svc(request, "postgres"),
        chunk_id=chunk_id,
        text=body.text,
        entities=body.entities,
        intents=body.intents,
        user_id=ctx["user_id"],
        doc_id=doc_id,
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


# ── Tables ────────────────────────────────────────────────────────────────────

@router.get("/{doc_id}/tables", response_model=ResponseModel)
async def get_tables_route(
    doc_id: str, request: Request, ctx: dict = Depends(get_ui_context),
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
    body: RequestTableRowUpdate,
    request: Request, ctx: dict = Depends(get_ui_context),
):
    result = await update_table_row(
        svc(request, "postgres"), table_id, row_index, body.model_dump(),
        doc_id=doc_id, tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


# ── Configs (aliases for AssetDetailWorkspace warehouse docs) ─────────────────

@router.get("/{doc_id}/configs", response_model=ResponseModel)
async def get_doc_configs(doc_id: str, request: Request, ctx: dict = Depends(get_ui_context)):
    result = await get_warehouse_configs(svc(request, "postgres"), doc_id, tenant_id=ctx["tenant_id"])
    return ResponseModel(code=200, data=result)


@router.post("/{doc_id}/configs", response_model=ResponseModel)
async def create_doc_config(
    doc_id: str, body: RequestDocConfig,
    request: Request, ctx: dict = Depends(get_ui_context),
):
    result = await create_warehouse_config(
        postgres=svc(request, "postgres"),
        warehouse_id=doc_id,
        body=body.model_dump(),
        user_id=ctx["user_id"],
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


@router.patch("/{doc_id}/configs/{config_id}/activate", response_model=ResponseModel)
async def activate_doc_config(
    doc_id: str, config_id: str,
    request: Request, ctx: dict = Depends(get_ui_context),
):
    result = await activate_warehouse_config(
        svc(request, "postgres"), doc_id, config_id, tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


# ── Lifecycle History ──────────────────────────────────────────────────────────

@router.get("/{doc_id}/history", response_model=ResponseModel)
async def get_doc_history(doc_id: str, request: Request, ctx: dict = Depends(get_ui_context)):
    try:
        data_uuid = uuid.UUID(doc_id)
    except ValueError:
        return ResponseModel(code=400, data=None, error="Invalid doc_id")

    try:
        tenant_uuid = uuid.UUID(ctx["tenant_id"])
    except (ValueError, KeyError):
        return ResponseModel(code=400, data=None, error="Invalid tenant_id")

    postgres = svc(request, "postgres")
    async with postgres.get_client() as session:
        rows = (await session.execute(
            sa_select(KBLifecycleHistoryORM)
            .join(KBDataORM, KBLifecycleHistoryORM.data_id == KBDataORM.data_id)
            .where(
                KBLifecycleHistoryORM.data_id == data_uuid,
                KBDataORM.tenant_id == tenant_uuid,
                KBLifecycleHistoryORM.is_deleted == False,  # noqa: E712
            )
            .order_by(KBLifecycleHistoryORM.transitioned_at.desc())
        )).scalars().all()

    result = [
        {
            "history_id":        to_string(row.history_id),
            "from_tier":         row.from_tier,
            "to_tier":           row.to_tier,
            "transitioned_at":   row.transitioned_at.isoformat() if row.transitioned_at else None,
            "approved_by":       row.approved_by,
            "notes":             row.notes,
            "pipeline_metadata": row.pipeline_metadata,
        }
        for row in rows
    ]
    return ResponseModel(code=200, data=result)

