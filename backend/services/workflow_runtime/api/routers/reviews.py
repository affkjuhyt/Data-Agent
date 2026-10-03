import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from langgraph.types import Command
from pydantic import BaseModel

from libs.shared.auth import require_tenant
from services.workflow_runtime.api.auth import get_current_user
from services.workflow_runtime.application.runtime_services.conversation_runner import (
    _graph_cache,
    _pending_reviews,
)
from services.workflow_runtime.infrastructure.db import save_message

router = APIRouter()
logger = logging.getLogger(__name__)


class ResumeRequest(BaseModel):
    approved: bool
    note: str = ""


def _own_entry(conv_id: str, tenant_id: str) -> dict[str, Any]:
    """The pending review, if the caller's tenant owns it. Another tenant's is a
    404 like a missing one: the router was authenticated but not tenant-scoped,
    so any user could list and resume every tenant's reviews."""
    entry = _pending_reviews.get(conv_id)
    if not entry or entry.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="Review not found or already resolved")
    return entry


@router.get("/api/reviews/pending")
async def list_pending(caller: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    tenant_id = require_tenant(caller)
    return [
        {
            "conv_id":      cid,
            "agent_id":     v["agent_id"],
            "query":        v["query"],
            "payload":      v["payload"],
            "waiting_secs": int(time.monotonic() - v["suspended_at"]),
        }
        for cid, v in _pending_reviews.items()
        if v.get("tenant_id") == tenant_id
    ]


@router.get("/api/reviews/{conv_id}")
async def get_pending(conv_id: str, caller: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    entry = _own_entry(conv_id, require_tenant(caller))
    return {
        "conv_id":      conv_id,
        "agent_id":     entry["agent_id"],
        "query":        entry["query"],
        "payload":      entry["payload"],
        "waiting_secs": int(time.monotonic() - entry["suspended_at"]),
    }


@router.post("/api/reviews/{conv_id}/resume")
async def resume_review(
    conv_id: str, body: ResumeRequest, caller: dict[str, Any] = Depends(get_current_user)
) -> dict[str, Any]:
    tenant_id = require_tenant(caller)
    entry = _own_entry(conv_id, tenant_id)

    agent_id  = entry["agent_id"]
    cache_key = agent_id
    graph     = _graph_cache.get(cache_key)
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not cached — restart workflow_runtime")

    lg_config = {"configurable": {"thread_id": conv_id}}
    command   = Command(resume={"approved": body.approved, "note": body.note})

    final_response = ""
    try:
        async for event in graph.astream_events(None, version="v2", config=lg_config, command=command):
            kind = event["event"]
            node = event.get("metadata", {}).get("langgraph_node", "")
            if kind == "on_chat_model_stream" and node == "responder":
                token = event["data"]["chunk"].content or ""
                final_response += token
            elif kind == "on_chain_end" and node == "responder":
                out = event["data"].get("output", {})
                if isinstance(out, dict) and out.get("response"):
                    final_response = out["response"]
    except Exception as e:
        logger.exception("resume_review | error resuming conv=%s", conv_id)
        raise HTTPException(status_code=500, detail=str(e))

    del _pending_reviews[conv_id]

    if final_response and conv_id:
        await save_message(conv_id, "assistant", final_response, tenant_id=tenant_id)

    logger.info("resume_review | conv=%s approved=%s", conv_id, body.approved)
    return {"conv_id": conv_id, "approved": body.approved, "response": final_response}
