from typing import Any

from services.workflow_runtime.api.auth import get_current_user
from fastapi import APIRouter, Depends, Query
from services.workflow_runtime.application.runtime_services import platform_queries
from services.workflow_runtime.application.runtime_services.langfuse_proxy import get_trace_observations

router = APIRouter()


# Declared before /api/traces/{conv_id}: FastAPI matches in registration
# order, and the bare collection path must not be swallowed by the param route.
@router.get("/api/traces")
async def list_traces(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    q: str | None = None,
    caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Tenant's AgentTraces rows (newest first) joined to their conversation: {items, total}."""
    tenant_id = platform_queries.resolve_tenant(caller)
    return await platform_queries.list_traces(
        tenant_id, limit=limit, offset=offset, status=status or None, q=q or None,
    )


@router.get("/api/traces/{conv_id}", response_model=None)
async def get_traces_by_conv(
    conv_id: str,
    trace_id: str | None = None,
) -> Any:
    return await get_trace_observations(conv_id, trace_id=trace_id)
