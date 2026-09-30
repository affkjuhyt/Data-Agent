from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from services.workflow_runtime.api.auth import get_current_user
from services.workflow_runtime.application.runtime_services import platform_queries
from services.workflow_runtime.application.runtime_services.conversation_runner import (
    run_conversation_stream,
)
from services.workflow_runtime.domain.schemas import RunRequest

router = APIRouter()


@router.post("/api/conversations/run", response_model=None)
async def run_conversation(
    req: RunRequest, request: Request, caller: dict[str, Any] = Depends(get_current_user),
) -> Any:
    return await run_conversation_stream(req, request, caller)


@router.get("/api/conversations")
async def list_conversations(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = None,
    q: str | None = None,
    agent_id: str | None = None,
    caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Tenant's conversations, most recent activity first: {items, total}."""
    tenant_id = platform_queries.resolve_tenant(caller)
    return await platform_queries.list_conversations(
        tenant_id, limit=limit, offset=offset, status=status or None, q=q or None, agent_id=agent_id or None,
    )


@router.get("/api/conversations/{conv_id}/messages")
async def get_conversation_messages(
    conv_id: str, request: Request, caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    # Verified tenant, not a header — otherwise any caller could read another
    # tenant's conversation by naming it.
    tenant_id = platform_queries.resolve_tenant(caller)
    messages = await platform_queries.get_messages_with_traces(conv_id, tenant_id=tenant_id)
    return {"messages": messages}
