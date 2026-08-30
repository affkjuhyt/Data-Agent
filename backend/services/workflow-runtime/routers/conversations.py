from typing import Any

from auth import get_current_user
from db import get_messages_ui
from fastapi import APIRouter, Depends, Request
from runtime_services.conversation_runner import run_conversation_stream
from schemas import RunRequest

router = APIRouter()


@router.post("/api/conversations/run", response_model=None)
async def run_conversation(
    req: RunRequest, request: Request, caller: dict[str, Any] = Depends(get_current_user),
) -> Any:
    return await run_conversation_stream(req, request, caller)


@router.get("/api/conversations/{conv_id}/messages")
async def get_conversation_messages(
    conv_id: str, request: Request, caller: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    # Verified tenant, not a header — otherwise any caller could read another
    # tenant's conversation by naming it.
    tenant_id = caller["tenant_id"] or "dev-tenant"
    messages = await get_messages_ui(conv_id, tenant_id=tenant_id)
    return {"messages": messages}
