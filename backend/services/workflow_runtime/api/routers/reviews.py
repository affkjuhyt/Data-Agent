import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from langgraph.types import Command
from pydantic import BaseModel

from libs.shared.auth import require_tenant
from services.workflow_runtime.api.auth import get_current_user
from services.workflow_runtime.application.runtime_services.conversation_runner import (
    get_graph_for_agent,
)
from services.workflow_runtime.infrastructure.db import save_message
from services.workflow_runtime.infrastructure.pending_reviews import (
    delete_pending_review,
    get_pending_review,
    list_pending_reviews,
)

router = APIRouter()
logger = logging.getLogger(__name__)


class ResumeRequest(BaseModel):
    approved: bool
    note: str = ""


def _waiting_secs(suspended_at: datetime) -> int:
    if suspended_at.tzinfo is None:
        suspended_at = suspended_at.replace(tzinfo=UTC)
    return max(0, int((datetime.now(UTC) - suspended_at).total_seconds()))


async def _own_entry(conv_id: str, tenant_id: str) -> dict[str, Any]:
    entry = await get_pending_review(conv_id, tenant_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Review not found or already resolved")
    return entry


@router.get("/api/reviews/pending")
async def list_pending(caller: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    tenant_id = require_tenant(caller)
    return [
        {
            "conv_id": str(v["conversation_id"]),
            "agent_id": str(v["agent_id"]),
            "query": v["query"],
            "payload": v["payload"],
            "waiting_secs": _waiting_secs(v["suspended_at"]),
        }
        for v in await list_pending_reviews(tenant_id)
    ]


@router.get("/api/reviews/{conv_id}")
async def get_pending(conv_id: str, caller: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    entry = await _own_entry(conv_id, require_tenant(caller))
    return {
        "conv_id": conv_id,
        "agent_id": str(entry["agent_id"]),
        "query": entry["query"],
        "payload": entry["payload"],
        "waiting_secs": _waiting_secs(entry["suspended_at"]),
    }


@router.post("/api/reviews/{conv_id}/resume")
async def resume_review(
    conv_id: str, body: ResumeRequest, caller: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    tenant_id = require_tenant(caller)
    entry = await _own_entry(conv_id, tenant_id)
    graph = await get_graph_for_agent(str(entry["agent_id"]), tenant_id)
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph unavailable")

    lg_config = {"configurable": {"thread_id": f"{tenant_id}:{conv_id}"}}
    command = Command(resume={"approved": body.approved, "note": body.note})
    final_response = ""
    try:
        async for event in graph.astream_events(None, version="v2", config=lg_config, command=command):
            kind = event["event"]
            node = event.get("metadata", {}).get("langgraph_node", "")
            if kind == "on_chat_model_stream" and node == "responder":
                final_response += event["data"]["chunk"].content or ""
            elif kind == "on_chain_end" and node == "responder":
                out = event["data"].get("output", {})
                if isinstance(out, dict) and out.get("response"):
                    final_response = out["response"]
    except Exception as e:
        logger.exception("resume_review | error resuming conv=%s", conv_id)
        raise HTTPException(status_code=500, detail=str(e)) from e

    if final_response:
        await save_message(conv_id, "assistant", final_response, tenant_id=tenant_id)
    await delete_pending_review(conv_id, tenant_id)
    logger.info("resume_review | conv=%s approved=%s", conv_id, body.approved)
    return {"conv_id": conv_id, "approved": body.approved, "response": final_response}
