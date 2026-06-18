import asyncio
import logging
import time
from collections import OrderedDict

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from config_loader import load_agent_config
from db import create_conversation, save_message, save_trace, validate_agent_tenant
from graph import build_graph
from memory_middleware import (
    _DEV_AGENT_ID,
    _DEV_TENANT_ID,
    apply_memory_policy,
    retrieve_memories,
)
from observability import get_langfuse_handler
from schemas import RunRequest

logger = logging.getLogger(__name__)


class _LRUCache:
    """Tiny bounded LRU for compiled graphs.

    Keyed by (agent_id, workflow_version_id) so a newly published flow version
    gets a fresh key and the graph is rebuilt automatically — no restart, no
    manual invalidation. Bounded so stale version entries can't accumulate
    forever (the previous plain dict never evicted anything).
    """

    def __init__(self, maxsize: int = 64) -> None:
        self._maxsize = maxsize
        self._data: "OrderedDict[str, object]" = OrderedDict()

    def get(self, key: str):
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def set(self, key: str, value: object) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def invalidate_suffix(self, suffix: str) -> int:
        """Drop every entry whose key ends with `suffix`. Returns count removed.

        Cache keys are `"{agent_id}:{workflow_version_id}"`, so passing
        `":{wv_id}"` evicts that version across all agents that reference it.
        """
        victims = [k for k in self._data if k.endswith(suffix)]
        for k in victims:
            del self._data[k]
        return len(victims)


_GRAPH_CACHE_MAXSIZE = 64
_graph_cache = _LRUCache(maxsize=_GRAPH_CACHE_MAXSIZE)
# Per-key build locks dedup concurrent first-time builds of the same graph: two
# requests racing on a new (agent, version) compile it once, not twice.
_build_locks: dict[str, asyncio.Lock] = {}
_default_graph = build_graph()


async def _get_or_build_graph(cache_key: str, flow_nodes: list) -> object:
    graph = _graph_cache.get(cache_key)
    if graph is not None:
        return graph
    lock = _build_locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        # Re-check under the lock: the request we waited on may have built it.
        graph = _graph_cache.get(cache_key)
        if graph is None:
            graph = build_graph(flow_nodes)
            _graph_cache.set(cache_key, graph)
    return graph


def invalidate_graph_version(workflow_version_id: str) -> int:
    """Evict cached graphs for a workflow version (cross-replica, via pub/sub).

    Called by the Redis invalidation listener when flow-builder republishes a
    version in place (same id, changed content). New-version publishes don't need
    this — they already get a fresh cache key. Returns the number of entries dropped.
    """
    if not workflow_version_id:
        return 0
    suffix = f":{workflow_version_id}"
    removed = _graph_cache.invalidate_suffix(suffix)
    for k in [k for k in _build_locks if k.endswith(suffix)]:
        _build_locks.pop(k, None)  # best-effort lock cleanup
    if removed:
        logger.info("invalidated %d cached graph(s) for version %s", removed, workflow_version_id)
    return removed


async def run_conversation_stream(req: RunRequest, request: Request):
    cfg = await load_agent_config(req.agent_id)
    agent_id = cfg.get("agent_id", _DEV_AGENT_ID)

    flow_nodes = cfg.get("flow_nodes") or []
    if flow_nodes:
        # Key by version so publishing a new flow version rebuilds the graph.
        # Falls back to 'default' when no version is known (e.g. DEV_CONFIG).
        cache_key = f"{req.agent_id}:{cfg.get('workflow_version_id') or 'default'}"
        graph = await _get_or_build_graph(cache_key, flow_nodes)
    else:
        graph = _default_graph

    tenant_id = request.headers.get("X-Tenant-ID", _DEV_TENANT_ID)
    if not await validate_agent_tenant(agent_id, tenant_id):
        return JSONResponse(status_code=403, content={"detail": "Agent not found or access denied"})

    user_ref = request.headers.get("X-User-ID", "anonymous")

    memory_context = []
    if cfg.get("memory_enabled"):
        memory_context = await retrieve_memories(
            tenant_id=tenant_id,
            agent_id=agent_id,
            user_ref=user_ref,
            query=req.query,
        )

    conv_id = req.conversation_id or await create_conversation(
        agent_version_id=cfg.get("agent_version_id", None),
        user_ref=user_ref,
        channel=request.headers.get("X-Channel", "web"),
        tenant_id=tenant_id,
    )

    state = {
        "query": req.query,
        "messages": [message.model_dump() for message in req.messages],
        "memory_context": memory_context,
        "tool_calls": [],
        "kb_chunks": [],
        "mcp_results": [],
        "rrf_results": [],
        "reranked_chunks": [],
        "response": "",
        "guardrail_triggered": False,
        "guardrail_message": "",
        "guardrail_stage": None,
        "guardrail_reason": None,
        "config": cfg,
    }

    handler = get_langfuse_handler(
        run_name=f"conv-{conv_id or 'new'}",
        session_id=conv_id,
        user_id=user_ref,
        metadata={
            "agent_id": agent_id,
            "tenant_id": tenant_id,
            "responder_model": cfg.get("responder_model"),
            "planner_model": cfg.get("planner_model"),
        },
    )
    callbacks = [handler] if handler else []
    has_output_guardrail = bool(cfg.get("guardrail_output_patterns"))

    async def event_stream():
        full_response: list[str] = []
        guardrail_triggered = False
        guardrail_msg = ""
        guardrail_stage = None
        guardrail_reason = None
        started_at = time.monotonic()

        try:
            async for event in graph.astream_events(state, version="v2", config={"callbacks": callbacks}):
                kind = event["event"]
                node = event.get("metadata", {}).get("langgraph_node", "")

                if kind == "on_chain_end" and node in ("guardrail_input", "guardrail_output"):
                    output = event["data"].get("output", {})
                    if isinstance(output, dict) and output.get("guardrail_triggered"):
                        guardrail_triggered = True
                        guardrail_msg = output.get("guardrail_message", "")
                        guardrail_stage = output.get("guardrail_stage")
                        guardrail_reason = output.get("guardrail_reason")

                elif kind == "on_chat_model_stream" and node == "responder":
                    token = event["data"]["chunk"].content or ""
                    if token:
                        full_response.append(token)
                        if not has_output_guardrail:
                            yield f"data: {token}\n\n"

            if guardrail_triggered:
                yield f"data: {guardrail_msg}\n\n"
            elif has_output_guardrail:
                combined = "".join(full_response)
                if combined:
                    yield f"data: {combined}\n\n"

            yield "data: [DONE]\n\n"

        except Exception:
            logger.exception("event_stream error")
            yield "data: [ERROR] Đã xảy ra lỗi, vui lòng thử lại.\n\n"
        finally:
            latency = int((time.monotonic() - started_at) * 1000)
            assistant_text = guardrail_msg if guardrail_triggered else "".join(full_response)

            if conv_id:
                await save_message(conv_id, "user", req.query)
                assistant_msg_id = await save_message(
                    conv_id,
                    "assistant",
                    assistant_text,
                    latency_ms=latency,
                    metadata={
                        "guardrail": {
                            "triggered": guardrail_triggered,
                            "stage": guardrail_stage,
                            "reason": guardrail_reason,
                        }
                    },
                )
                if assistant_msg_id:
                    await save_trace(
                        message_id=assistant_msg_id,
                        trace_index=0,
                        tool_name="responder",
                        input={"query": req.query},
                        output={"response": assistant_text, "guardrail_triggered": guardrail_triggered},
                        latency_ms=latency,
                    )

            if cfg.get("memory_enabled") and not guardrail_triggered:
                final_state = {**state, "response": assistant_text}
                await apply_memory_policy(final_state, agent_id, tenant_id, user_ref)

    response = StreamingResponse(event_stream(), media_type="text/event-stream")
    response.headers["X-Conversation-Id"] = conv_id or ""
    return response
