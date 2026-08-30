import time
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from langgraph.types import Command

from runtime_services.conversation_runner import _pending_reviews, _graph_cache
from db import save_message

router = APIRouter()
logger = logging.getLogger(__name__)


class ResumeRequest(BaseModel):
    approved: bool
    note: str = ""


@router.get("/api/reviews/pending")
async def list_pending() -> list[dict[str, Any]]:
    return [
        {
            "conv_id":      cid,
            "agent_id":     v["agent_id"],
            "query":        v["query"],
            "payload":      v["payload"],
            "waiting_secs": int(time.monotonic() - v["suspended_at"]),
        }
        for cid, v in _pending_reviews.items()
    ]


@router.get("/api/reviews/{conv_id}")
async def get_pending(conv_id: str) -> dict[str, Any]:
    entry = _pending_reviews.get(conv_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Review not found or already resolved")
    return {
        "conv_id":      conv_id,
        "agent_id":     entry["agent_id"],
        "query":        entry["query"],
        "payload":      entry["payload"],
        "waiting_secs": int(time.monotonic() - entry["suspended_at"]),
    }


@router.post("/api/reviews/{conv_id}/resume")
async def resume_review(conv_id: str, body: ResumeRequest) -> dict[str, Any]:
    entry = _pending_reviews.get(conv_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Review not found or already resolved")

    agent_id  = entry["agent_id"]
    cache_key = agent_id
    graph     = _graph_cache.get(cache_key)
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not cached — restart workflow-runtime")

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
        await save_message(conv_id, "assistant", final_response)

    logger.info("resume_review | conv=%s approved=%s", conv_id, body.approved)
    return {"conv_id": conv_id, "approved": body.approved, "response": final_response}
