from fastapi import APIRouter, Request

from runtime_services.conversation_runner import run_conversation_stream
from db import get_messages_ui
from schemas import RunRequest

router = APIRouter()


@router.post("/api/conversations/run")
async def run_conversation(req: RunRequest, request: Request):
    return await run_conversation_stream(req, request)


@router.get("/api/conversations/{conv_id}/messages")
async def get_conversation_messages(conv_id: str, request: Request):
    tenant_id = request.headers.get("X-Tenant-ID", "dev-tenant")
    messages = await get_messages_ui(conv_id, tenant_id=tenant_id)
    return {"messages": messages}
