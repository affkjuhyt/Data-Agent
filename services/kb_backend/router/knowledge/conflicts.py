"""/api/knowledge/conflicts — ConflictWorkspace data."""
from typing import Any
from fastapi import APIRouter, Depends, Query, Request

from libs.basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.schemas import ResolveConflictRequest
from services.kb_backend.dependencies.auth import authenticate
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.operations.conflict_ops import (
    list_conflicts, get_conflict, resolve_conflict,
)

router = APIRouter(prefix="/api/knowledge/conflicts", tags=["Knowledge-Conflicts"])


@router.get("", response_model=ResponseModel)
async def list_conflicts_route(
    request: Request, ctx: dict[str, Any] = Depends(authenticate),
    pending_page:  int = Query(1, ge=1),
    awaiting_page: int = Query(1, ge=1),
    resolved_page: int = Query(1, ge=1),
) -> ResponseModel[Any]:
    result = await list_conflicts(
        postgres=svc(request, "postgres"),
        tenant_id=ctx["tenant_id"],
        user_id=ctx["user_id"],
        pending_page=pending_page,
        awaiting_page=awaiting_page,
        resolved_page=resolved_page,
    )
    return ResponseModel(code=200, data=result)


@router.get("/{conflict_id}", response_model=ResponseModel)
async def get_conflict_route(
    conflict_id: str, request: Request, ctx: dict[str, Any] = Depends(authenticate),
) -> ResponseModel[Any]:
    result = await get_conflict(
        postgres=svc(request, "postgres"),
        conflict_id=conflict_id,
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


@router.patch("/{conflict_id}", response_model=ResponseModel)
async def resolve_conflict_route(
    conflict_id: str, body: ResolveConflictRequest,
    request: Request, ctx: dict[str, Any] = Depends(require_perm("edit_conflict")),
) -> ResponseModel[Any]:
    result = await resolve_conflict(
        postgres=svc(request, "postgres"),
        conflict_id=conflict_id,
        method=body.selected_resolution_method,
        instruction=body.resolution_instruction or "",
        resolved_by=ctx["user_name"],
        kafka_producer=svc(request, "kafka_producer"),
        tenant_id=ctx.get("tenant_id"),
    )
    return ResponseModel(code=200, data=result)
