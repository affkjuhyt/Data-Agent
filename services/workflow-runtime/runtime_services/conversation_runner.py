import asyncio
import json
import logging
import re
import time
from collections import OrderedDict

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from config_loader import load_agent_config
from db import create_conversation, get_messages, save_message, save_trace, validate_agent_tenant
from redis_cache import cache_get_messages, cache_append_messages, cache_set_messages
from graph import build_graph
from memory_middleware import (
    _DEV_AGENT_ID,
    _DEV_TENANT_ID,
    apply_memory_policy,
    retrieve_episodic_past,
    retrieve_memories,
    retrieve_pending_reminders,
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

# Shared checkpointer for workflows that have a human_approval node.
_checkpointer = MemorySaver()

# conv_id → {agent_id, query, interrupt_payload, suspended_at}
_pending_reviews: dict[str, dict] = {}

_SKIP_TRACE_NODES = frozenset({"LangGraph", "join_retrieval", "join_end"})

def _skip_trace(name: str) -> bool:
    return name in _SKIP_TRACE_NODES or name.startswith(("_route", "route_"))


async def _get_or_build_graph(
    cache_key: str,
    flow_nodes: list,
    flow_edges: list | None = None,
    checkpointer=None,
) -> object:
    graph = _graph_cache.get(cache_key)
    if graph is not None:
        return graph
    lock = _build_locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        # Re-check under the lock: the request we waited on may have built it.
        graph = _graph_cache.get(cache_key)
        if graph is None:
            graph = build_graph(flow_nodes, flow_edges, checkpointer=checkpointer)
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
    tenant_id = request.headers.get("X-Tenant-ID", _DEV_TENANT_ID)
    cfg = await load_agent_config(req.agent_id, tenant_id=tenant_id)
    if cfg is None:
        async def _err():
            yield "data: [ERROR] Agent chưa được publish.\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")
    agent_id = cfg.get("agent_id", _DEV_AGENT_ID)

    flow_nodes = cfg.get("flow_nodes") or []
    flow_edges = cfg.get("flow_edges") or None
    if flow_nodes:
        # Key by version so publishing a new flow version rebuilds the graph.
        # Falls back to 'default' when no version is known (e.g. DEV_CONFIG).
        cache_key = f"{req.agent_id}:{cfg.get('workflow_version_id') or 'default'}"
        needs_checkpointer = any(
            n.get("data", {}).get("nodeType") == "human_approval"
            for n in flow_nodes
        )
        graph = await _get_or_build_graph(
            cache_key, flow_nodes, flow_edges,
            checkpointer=_checkpointer if needs_checkpointer else None,
        )
    else:
        graph = _default_graph
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
        if not req.conversation_id:
            # New session — surface due reminders + recent episodic history once (doc §IV).
            reminders, episodic_past = await asyncio.gather(
                retrieve_pending_reminders(tenant_id=tenant_id, agent_id=agent_id, user_ref=user_ref),
                retrieve_episodic_past(tenant_id=tenant_id, agent_id=agent_id, user_ref=user_ref),
            )
            memory_context = [
                {**r, "memory_type": "episodic_future"} for r in reminders
            ] + [
                {**e, "memory_type": "episodic_past"} for e in episodic_past
            ] + memory_context

    # Load history: Redis → DB → client fallback
    if req.conversation_id:
        history = await cache_get_messages(req.conversation_id)
        if history is None:
            history = await get_messages(req.conversation_id, tenant_id=tenant_id)
            if history:
                await cache_set_messages(req.conversation_id, history)
        history = history or [m.model_dump() for m in req.messages]
    else:
        history = [m.model_dump() for m in req.messages]

    conv_id = req.conversation_id or await create_conversation(
        agent_version_id=cfg.get("agent_version_id", None),
        user_ref=user_ref,
        channel=request.headers.get("X-Channel", "web"),
        tenant_id=tenant_id,
    )

    state = {
        "query": req.query,
        "original_query": None,
        "messages": history,
        "memory_context": memory_context,
        "tool_calls": [],
        "kb_chunks": [],
        "mcp_results": [],
        "tool_results": [],
        "tool_errors": [],
        "loop_iteration": 0,
        "judge_verdict": None,
        "judge_error_type": None,
        "judge_reason": None,
        "human_approved": None,
        "human_note": None,
        "rrf_results": [],
        "reranked_chunks": [],
        "response": "",
        "suggestions": [],
        "guardrail_triggered": False,
        "guardrail_message": "",
        "guardrail_stage": None,
        "guardrail_reason": None,
        "intent": None,
        "language": None,
        "config": cfg,
    }

    handler = get_langfuse_handler(
        run_name=f"conv-{conv_id or 'new'}",
        session_id=conv_id,
        user_id=user_ref,
        metadata={
            "agent_id": agent_id,
            "agent_version_id": cfg.get("agent_version_id"),
            "tenant_id": tenant_id,
            "channel": request.headers.get("X-Channel", "web"),
            "responder_model": cfg.get("responder_model"),
            "planner_model": cfg.get("planner_model"),
        },
    )
    callbacks = [handler] if handler else []

    _OPTIONS_OPEN  = "<OPTIONS>"
    _OPTIONS_CLOSE = "</OPTIONS>"
    _THINK_OPEN    = "<think>"
    _THINK_CLOSE   = "</think>"

    async def event_stream():
        full_response: list[str] = []
        guardrail_triggered = False
        guardrail_msg = ""
        guardrail_stage = None
        guardrail_reason = None
        # selective buffering để chặn <OPTIONS> tag khỏi frontend
        pending = ""           # giữ tạm token cuối để detect prefix của <OPTIONS>
        options_mode = False   # True khi đã qua <OPTIONS>
        options_buffer = ""    # nội dung sau <OPTIONS>
        think_mode = False     # True khi đang trong <think>...</think>
        think_buffer = ""      # buffer think content (bị discard)
        started_at = time.monotonic()

        pending_suggestions: list[str] | None = None

        # per-node trace tracking
        node_start_times: dict[str, float] = {}
        node_traces: list[dict] = []
        detected_language: str | None = None
        detected_intent: str | None = None

        lg_config = {"callbacks": callbacks, "configurable": {"thread_id": conv_id}}

        try:
            async for event in graph.astream_events(state, version="v2", config=lg_config):
                kind = event["event"]
                node = event.get("metadata", {}).get("langgraph_node", "")

                if kind == "on_chain_start" and node and not _skip_trace(node):
                    node_start_times[node] = time.monotonic()

                elif kind == "on_chain_end" and node and not _skip_trace(node):
                    if node in node_start_times:
                        node_lat = int((time.monotonic() - node_start_times.pop(node)) * 1000)
                        out = event["data"].get("output", {})
                        inp = event["data"].get("input", {})
                        node_traces.append({
                            "tool_name": node,
                            "input":     inp if isinstance(inp, dict) else {},
                            "output":    out if isinstance(out, dict) else {},
                            "latency_ms": node_lat,
                        })
                        # Expand per-tool spans for tool_executor
                        if node == "tool_executor" and isinstance(out, dict):
                            for tt in out.get("_tool_traces", []):
                                node_traces.append({
                                    "tool_name": f"tool/{tt['tool_name']}",
                                    "input":     {"query": tt.get("query", ""), "arguments": tt.get("arguments", {})},
                                    "output":    {"result_id": tt.get("result_id"), "error": tt.get("error")},
                                    "latency_ms": tt["latency_ms"],
                                    "status":     tt.get("status", "success"),
                                })
                    # node-specific logic
                    if node == "guardrail_input":
                        out = event["data"].get("output", {})
                        if isinstance(out, dict) and out.get("guardrail_triggered"):
                            guardrail_triggered = True
                            guardrail_msg    = out.get("guardrail_message", "")
                            guardrail_stage  = out.get("guardrail_stage")
                            guardrail_reason = out.get("guardrail_reason")
                    elif node == "suggestion":
                        out = event["data"].get("output", {})
                        if isinstance(out, dict):
                            topics = out.get("suggestions", [])
                            if topics:
                                pending_suggestions = topics
                    elif node == "planner":
                        out = event["data"].get("output", {})
                        if isinstance(out, dict):
                            detected_language = out.get("language")
                            detected_intent   = out.get("intent")

                elif kind == "on_chat_model_stream" and node == "responder":
                    token = event["data"]["chunk"].content or ""
                    if not token:
                        continue

                    # Strip <think>...</think> — discard toàn bộ thinking block
                    if think_mode:
                        think_buffer += token
                        if _THINK_CLOSE in think_buffer:
                            token = think_buffer.split(_THINK_CLOSE, 1)[1]
                            think_mode = False
                            think_buffer = ""
                        else:
                            continue
                        if not token:
                            continue
                    elif _THINK_OPEN in (pending + token):
                        combined = pending + token
                        idx = combined.find(_THINK_OPEN)
                        before = combined[:idx]
                        if before:
                            full_response.append(before)
                            yield f"data: {json.dumps(before)}\n\n"
                        pending = ""
                        think_mode = True
                        think_buffer = combined[idx + len(_THINK_OPEN):]
                        continue

                    if options_mode:
                        options_buffer += token
                    else:
                        pending += token
                        tag_pos = pending.find(_OPTIONS_OPEN)
                        if tag_pos != -1:
                            # flush text trước tag, bắt đầu buffer options
                            before = pending[:tag_pos]
                            if before:
                                full_response.append(before)
                                yield f"data: {json.dumps(before)}\n\n"
                            options_mode = True
                            options_buffer = pending[tag_pos + len(_OPTIONS_OPEN):]
                            pending = ""
                        else:
                            # flush phần chắc chắn không phải prefix của <OPTIONS>
                            max_hold = len(_OPTIONS_OPEN) - 1
                            flush_to = len(pending)
                            for hold in range(min(max_hold, len(pending)), 0, -1):
                                if _OPTIONS_OPEN.startswith(pending[-hold:]):
                                    flush_to = len(pending) - hold
                                    break
                            to_send = pending[:flush_to]
                            if to_send:
                                full_response.append(to_send)
                                yield f"data: {json.dumps(to_send)}\n\n"
                            pending = pending[flush_to:]

            # flush pending còn lại (không phải <OPTIONS>)
            if pending:
                full_response.append(pending)
                yield f"data: {json.dumps(pending)}\n\n"

            # parse responder options nếu có
            responder_options: list[str] | None = None
            if options_mode:
                close_pos = options_buffer.find(_OPTIONS_CLOSE)
                if close_pos != -1:
                    try:
                        parsed = json.loads(options_buffer[:close_pos].strip())
                        if isinstance(parsed, list) and parsed:
                            responder_options = [str(o) for o in parsed[:3]]
                    except (json.JSONDecodeError, Exception):
                        pass
                # fallback: </OPTIONS> bị thiếu (LLM cắt giữa chừng / max_tokens hit)
                if not responder_options:
                    fallback = options_buffer.replace(_OPTIONS_CLOSE, "").strip()
                    if fallback:
                        full_response.append(fallback)
                        yield f"data: {json.dumps(fallback)}\n\n"

            # Detect interrupt (human_approval node đang chờ reviewer)
            try:
                snap = graph.get_state(lg_config)
                if snap.next:
                    interrupt_payload = None
                    for task in snap.tasks:
                        for intr in getattr(task, "interrupts", []):
                            interrupt_payload = intr.value
                            break
                    _pending_reviews[conv_id] = {
                        "agent_id":   req.agent_id,
                        "query":      req.query,
                        "payload":    interrupt_payload,
                        "suspended_at": time.monotonic(),
                    }
                    logger.info("human_approval | conv=%s suspended, waiting for review", conv_id)
                    yield f"data: {json.dumps({'__pending_review': {'conv_id': conv_id, 'payload': interrupt_payload}})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
            except Exception:
                pass  # graph không có checkpointer → skip

            if guardrail_triggered:
                yield f"data: {json.dumps('Xin lỗi, tôi không thể trả lời câu hỏi này.')}\n\n"
            elif responder_options:
                yield f"data: {json.dumps({'__options': responder_options})}\n\n"
            elif pending_suggestions:
                yield f"data: {json.dumps({'__suggestions': pending_suggestions})}\n\n"

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
                        },
                        "language": detected_language,
                        "intent": detected_intent,
                        "responder_model": cfg.get("responder_model"),
                        **({"pending_options": responder_options} if responder_options else {}),
                        **({"pending_approval": conv_id} if conv_id and conv_id in _pending_reviews else {}),
                    },
                )
                await cache_append_messages(conv_id, [
                    {"role": "user",      "content": req.query},
                    {"role": "assistant", "content": assistant_text},
                ])
                if assistant_msg_id:
                    if node_traces:
                        for idx, nt in enumerate(node_traces):
                            await save_trace(
                                message_id=assistant_msg_id,
                                trace_index=idx,
                                tool_name=nt["tool_name"],
                                input=nt["input"],
                                output=nt["output"],
                                latency_ms=nt["latency_ms"],
                                status=nt.get("status", "success"),
                            )
                    else:
                        await save_trace(
                            message_id=assistant_msg_id,
                            trace_index=0,
                            tool_name="responder",
                            input={"query": req.query},
                            output={"response": assistant_text, "guardrail_triggered": guardrail_triggered},
                            latency_ms=latency,
                        )

            if cfg.get("memory_enabled") and not guardrail_triggered and conv_id:
                final_state = {**state, "response": assistant_text}
                await apply_memory_policy(
                    final_state, agent_id, tenant_id, user_ref,
                    conversation_id=conv_id, message_id=assistant_msg_id,
                )

    response = StreamingResponse(event_stream(), media_type="text/event-stream")
    response.headers["X-Conversation-Id"] = conv_id or ""
    return response
