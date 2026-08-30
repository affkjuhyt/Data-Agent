from typing import Any, Coroutine, cast
import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator

from cachetools import LRUCache

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from langgraph.checkpoint.memory import MemorySaver

from config_loader import load_agent_config
from db import create_conversation, get_last_slots, get_messages, save_message, save_trace, validate_agent_tenant
from redis_cache import (
    cache_append_messages,
    cache_get_messages,
    cache_get_slots,
    cache_set_messages,
    cache_set_slots,
)
from transcript_compression import compress_tool_results
from graph import build_graph
from libs.shared.auth import require_tenant
from memory_middleware import (
    _DEV_AGENT_ID,
    apply_memory_policy,
    retrieve_episodic_past,
    retrieve_memories,
    retrieve_pending_reminders,
)
from workflow_memory import record_workflow_outcome, retrieve_workflow_memory
from observability import get_langchain_callback_handler, flush_langfuse
from schemas import RunRequest
from libs.shared.guardrail_check import GuardrailCheckError, llm_guardrail_check

logger = logging.getLogger(__name__)

_FAKE_STREAM_WORD_DELAY = 0.02  # giây/từ — hiệu ứng gõ chữ khi replay text đã duyệt xong


def _strip_think_and_options(text: str) -> tuple[str, list[str] | None]:
    """Xử lý 1 lần trên TOÀN BỘ text (không cần incremental nữa — đã có sẵn cả
    chuỗi vì responder không còn stream live) — bỏ <think>...</think>, tách
    <OPTIONS>...</OPTIONS> ra riêng."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)  # thiếu close tag (bị cắt max_tokens)

    options: list[str] | None = None
    match = re.search(r"<OPTIONS>(.*?)</OPTIONS>", text, flags=re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1).strip())
            if isinstance(parsed, list) and parsed:
                options = [str(o) for o in parsed[:3]]
        except (json.JSONDecodeError, ValueError):
            pass
        text = text[:match.start()] + text[match.end():]
    else:
        open_match = re.search(r"<OPTIONS>.*", text, flags=re.DOTALL)
        if open_match:
            text = text[:open_match.start()]

    return text.strip(), options


def _chunk_words(text: str) -> list[str]:
    words = text.split(" ")
    return [w if i == 0 else " " + w for i, w in enumerate(words)]


class _GraphCache(LRUCache[str, Any]):
    """Bounded LRU for compiled graphs (``cachetools.LRUCache`` + suffix eviction).

    Keyed by (agent_id, workflow_version_id) so a newly published flow version
    gets a fresh key and the graph is rebuilt automatically — no restart, no
    manual invalidation. Bounded so stale version entries can't accumulate
    forever (the previous plain dict never evicted anything).

    ``get`` (inherited) returns ``None`` on a miss and refreshes recency on a
    hit — it routes through ``__getitem__``. Single event loop, no locks.
    """

    def __init__(self, maxsize: int = 64) -> None:
        super().__init__(maxsize=maxsize)

    def set(self, key: str, value: Any) -> None:
        self[key] = value

    def invalidate_suffix(self, suffix: str) -> int:
        """Drop every entry whose key ends with `suffix`. Returns count removed.

        Cache keys are `"{agent_id}:{workflow_version_id}"`, so passing
        `":{wv_id}"` evicts that version across all agents that reference it.
        """
        victims = [k for k in self if k.endswith(suffix)]
        for k in victims:
            del self[k]
        return len(victims)


_GRAPH_CACHE_MAXSIZE = 64
_graph_cache = _GraphCache(maxsize=_GRAPH_CACHE_MAXSIZE)
# Per-key build locks dedup concurrent first-time builds of the same graph: two
# requests racing on a new (agent, version) compile it once, not twice.
_build_locks: dict[str, asyncio.Lock] = {}
_default_graph = build_graph()

# Shared checkpointer for workflows that have a human_approval node.
_checkpointer = MemorySaver()

# conv_id → {agent_id, query, interrupt_payload, suspended_at}
_pending_reviews: dict[str, dict[str, Any]] = {}

# asyncio only holds a weak reference to a task created via create_task — if
# nothing else references it, it can be GC'd mid-execution. Keep a strong ref
# here until done, per the stdlib docs' own recommendation for fire-and-forget tasks.
_background_tasks: set[asyncio.Task[Any]] = set()


def _fire_and_forget(coro: Coroutine[Any, Any, Any]) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# doc §3.7 — Workflow Memory needs a task_type to filter on, but that's only
# known once the planner decides to call a data tool, which is *after* the
# point retrieval needs to happen. No table_name-style "task type taxonomy"
# step exists yet (see docs/STM_LTM_Memory_Implementation.md), so task_type
# is inferred from which data tools the agent has configured.
_DATA_TOOL_TASK_TYPE = {
    "text_to_sql":    "sql_query",
    "text_to_pandas": "report_gen",
}

_SKIP_TRACE_NODES = frozenset({"LangGraph", "join_retrieval", "join_end", "judge_phase1"})

def _skip_trace(name: str) -> bool:
    return name in _SKIP_TRACE_NODES or name.startswith(("_route", "route_"))


async def _get_or_build_graph(
    cache_key: str,
    flow_nodes: list[Any],
    flow_edges: list[Any] | None = None,
    checkpointer: Any = None,
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


def _slots_with_marker(
    slots: dict[str, Any], clarifications: list[dict[str, Any]],
) -> dict[str, Any]:
    """Stamp "a clarification is outstanding" onto the slots.

    Rides along with the slots rather than living in its own key so the Redis
    cache and the durable message metadata carry it identically — otherwise a
    cold cache would fall back to the DB copy, miss the marker, and ask the same
    question a second time right after the user answered it.

    AnalyticalSlots.from_dict ignores keys it does not know, so this is inert
    everywhere except the one place that reads it.
    """
    return {**slots, "_asked_clarification": bool(clarifications)}


async def run_conversation_stream(
    req: RunRequest, request: Request, caller: dict[str, Any] | None = None,
) -> StreamingResponse | JSONResponse:
    # `caller` is the verified identity from auth.get_current_user — either a
    # Kong-gated user or the orchestrator's signed assertion. Reading these
    # from raw headers (as this did) meant anything reaching port 8001 chose
    # its own tenant, user and roles.
    #
    # No dev-tenant fallback: a verified caller can still arrive without a
    # tenant (a token missing the claim, an assertion signed for a process), and
    # defaulting those to _DEV_TENANT_ID ran the agent — its tools, its queries,
    # its writes — against a real tenant's data on behalf of someone who was
    # never scoped to it. 403 instead.
    caller = caller or {}
    tenant_id = require_tenant(caller)
    cfg = await load_agent_config(req.agent_id, tenant_id=tenant_id)
    if cfg is None:
        async def _err() -> AsyncIterator[str]:
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
        graph = _graph_cache.get(cache_key)
        if graph is None:
            graph = await _get_or_build_graph(
                cache_key, flow_nodes, flow_edges,
                checkpointer=_checkpointer if needs_checkpointer else None,
            )
    else:
        graph = _default_graph

    cfg["tenant_id"] = tenant_id
    # doc §3.7 — gates which Schema Memory tables _text_to_sql_handler may
    # see. Kong-style convention: comma-separated roles already verified
    # upstream (see tests/integration/conftest.py headers_factory).
    #
    # ALSO the caller identity kb_search asserts to kb-backend. It used to send
    # a hardcoded admin role set instead, so kb-backend's document-level RBAC
    # never applied to agent traffic; these two lines are the real identity it
    # now signs and forwards. An empty list is meaningful — kb_search fails
    # closed on it rather than substituting a default.
    cfg["user_roles"] = list(caller.get("roles") or [])
    # Strip verbose build-time fields — không cần runtime, chỉ làm Langfuse input thêm to
    cfg.pop("flow_nodes", None)
    cfg.pop("flow_edges", None)
    if not await validate_agent_tenant(agent_id, tenant_id):
        return JSONResponse(status_code=403, content={"detail": "Agent not found or access denied"})

    user_ref = caller.get("user_id") or "anonymous"
    # kb_search signs this into its assertion to kb-backend. It read
    # cfg["user_ref"] before, but nothing ever set the key, so it always fell
    # through to a hardcoded UUID literal.
    cfg["user_ref"] = user_ref

    # orchestrated=True: orchestrator đã fetch memory và pass xuống, dùng luôn
    # orchestrated=False: single agent mode, tự fetch memory như bình thường
    if req.orchestrated:
        memory_context = list(req.memory_context)
        workflow_memory_context: list[dict[str, Any]] = []
    else:
        memory_context = []
        if cfg.get("memory_enabled"):
            memory_context = await retrieve_memories(
                tenant_id=tenant_id,
                agent_id=agent_id,
                user_ref=user_ref,
                query=req.query,
            )
            if not req.conversation_id:
                reminders, episodic_past = await asyncio.gather(
                    retrieve_pending_reminders(tenant_id=tenant_id, agent_id=agent_id, user_ref=user_ref),
                    retrieve_episodic_past(tenant_id=tenant_id, agent_id=agent_id, user_ref=user_ref),
                )
                memory_context = [
                    {**r, "memory_type": "episodic_future"} for r in reminders
                ] + [
                    {**e, "memory_type": "episodic_past"} for e in episodic_past
                ] + memory_context

        workflow_memory_context = []
        data_task_type = next(
            (_DATA_TOOL_TASK_TYPE[t] for t in cfg.get("allowed_tools", []) if t in _DATA_TOOL_TASK_TYPE),
            None,
        )
        if data_task_type and cfg.get("memory_enabled"):
            workflow_memory_context = await retrieve_workflow_memory(
                tenant_id=tenant_id,
                agent_id=agent_id,
                task_type=data_task_type,
                query=req.query,
            )

    # Load history: Redis → DB → client fallback
    # No previous turn on a fresh thread; the planner treats None as "start
    # from what this question says" rather than inheriting anything.
    previous_slots: dict[str, Any] | None = None
    if req.conversation_id:
        # Previous turn's analytical slots — Redis first, DB as the durable
        # fallback. Without this a follow-up ("chỉ lấy miền Bắc") arriving
        # after a cache eviction silently widens back to the whole country.
        previous_slots = await cache_get_slots(req.conversation_id)
        if previous_slots is None:
            previous_slots = await get_last_slots(req.conversation_id, tenant_id=tenant_id)
        history = await cache_get_messages(req.conversation_id)
        if history is None:
            history = await get_messages(req.conversation_id, tenant_id=tenant_id)
            if history:
                await cache_set_messages(req.conversation_id, history)
        history = history or [m.model_dump() for m in req.messages]
    else:
        history = [m.model_dump() for m in req.messages]

    # doc §1.8 — compress already-consumed tool results before they feed any
    # node's LLM call this turn. State still gets the full history below;
    # only this turn's view is compressed (see transcript_compression.py).
    history = compress_tool_results(history)

    conv_id = req.conversation_id or await create_conversation(
        user_ref=user_ref,
        channel=request.headers.get("X-Channel", "web"),
        tenant_id=tenant_id,
    )

    state = {
        "query": req.query,
        "original_query": None,
        "messages": history,
        "memory_context": memory_context,
        "workflow_memory_context": workflow_memory_context,
        "tool_calls": [],
        "kb_chunks": [],
        "mcp_results": [],
        "tool_results": [],
        "tool_errors": [],
        "loop_iteration": 0,
        "judge_verdict": None,
        "judge_error_type": None,
        "judge_reason": None,
        "judge_phase1_error": None,
        "human_approved": None,
        "human_note": None,
        "rrf_results": [],
        "reranked_chunks": [],
        "response": "",
        "citations": [],
        "suggestions": [],
        "intent": None,
        "analysis_type": None,
        "slots": None,
        "previous_slots": previous_slots,
        "clarifications": None,
        # Set when the PREVIOUS turn asked a clarification. This turn is the
        # user answering it, so the rules must not fire again — otherwise a
        # question they cannot fully resolve asks the same thing forever.
        "previous_clarification": bool((previous_slots or {}).get("_asked_clarification")),
        "language": None,
        "config": cfg,
    }

    # Nếu request đến từ orchestrator, traceparent header (W3C) đã được
    # FastAPIInstrumentor (app.py) extract thành OTEL context hiện hành —
    # get_langchain_callback_handler() tự nối span vào đúng trace đó. Nếu gọi
    # thẳng workflow-runtime (không qua orchestrator, vd. test agent lẻ trong
    # agent-builder), fallback tạo trace riêng với session_id=conv_id.
    handler = get_langchain_callback_handler(conv_id=conv_id)
    callbacks = [handler] if handler else []

    guardrail_cfg = req.guardrail_cfg.model_dump() if req.guardrail_cfg else {}

    async def event_stream() -> AsyncIterator[str]:
        full_response: list[str] = []
        started_at = time.monotonic()

        pending_suggestions: list[str] | None = None
        pending_charts: list[dict[str, Any]] | None = None
        pending_citations: list[dict[str, Any]] | None = None
        # Khai báo sớm (trước try) — nếu exception xảy ra trước khi được gán
        # lại bên dưới, khối finally vẫn tham chiếu được an toàn.
        responder_options: list[str] | None = None

        # per-node trace tracking
        node_start_times: dict[str, float] = {}
        node_traces: list[dict[str, Any]] = []
        detected_language: str | None = None
        detected_intent: str | None = None
        detected_analysis: str | None = None
        detected_slots: dict[str, Any] | None = None
        detected_clarify: list[dict[str, Any]] = []

        # Race Judge (tự chạy trong graph, có thể khiến responder chạy lại
        # nhiều lần) + Guardrail output check trên CÙNG 1 bản text — mỗi lần
        # responder sinh ra bản mới, huỷ task check của bản cũ (sắp lỗi thời
        # nếu Judge yêu cầu sinh lại), bắn task check mới cho bản hiện tại.
        last_responder_text = ""
        last_responder_citations: list[dict[str, Any]] = []
        last_responder_evidence: dict[str, Any] | None = None
        pending_evidence: dict[str, Any] | None = None
        guardrail_task: asyncio.Task[Any] | None = None

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
                    if node == "suggestion":
                        out = event["data"].get("output", {})
                        if isinstance(out, dict):
                            topics = out.get("suggestions", [])
                            if topics:
                                pending_suggestions = topics
                    elif node == "visualization":
                        out = event["data"].get("output", {})
                        if isinstance(out, dict) and out.get("chart_specs"):
                            pending_charts = out["chart_specs"]
                    elif node == "planner":
                        out = event["data"].get("output", {})
                        if isinstance(out, dict):
                            detected_language = out.get("language")
                            detected_intent   = out.get("intent")
                            detected_analysis = out.get("analysis_type")
                            detected_slots    = out.get("slots")
                            detected_clarify  = out.get("clarifications") or []
                    elif node == "responder":
                        out = event["data"].get("output", {})
                        text = out.get("response", "") if isinstance(out, dict) else ""
                        last_responder_text = text
                        last_responder_citations = out.get("citations", []) if isinstance(out, dict) else []
                        last_responder_evidence = out.get("evidence") if isinstance(out, dict) else None
                        # Bản trước (nếu có) sắp lỗi thời — Judge vừa cho responder
                        # chạy lại, huỷ kết quả check dở dang của bản cũ.
                        if guardrail_task and not guardrail_task.done():
                            guardrail_task.cancel()
                        guardrail_task = asyncio.create_task(
                            llm_guardrail_check(text, guardrail_cfg, check_type="output")
                        )

            # Graph đã chạy xong hẳn — nếu có Judge, nghĩa là đã PASS hoặc hết
            # loop_max_iterations (force-accept); last_responder_text là bản
            # duy nhất đáng tin. Không có gì được gửi ra SSE cho tới đây.

            # Detect interrupt (human_approval node đang chờ reviewer) — nếu có,
            # chưa tới lúc đánh giá output (chưa chắc đây là bản cuối).
            try:
                snap = graph.get_state(lg_config)
                if snap.next:
                    if guardrail_task and not guardrail_task.done():
                        guardrail_task.cancel()
                    interrupt_payload = None
                    for task in snap.tasks:
                        for intr in getattr(task, "interrupts", []):
                            interrupt_payload = intr.value
                            break
                    _pending_reviews[cast(str, conv_id)] = {
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

            if guardrail_task:
                try:
                    triggered, reason = await guardrail_task
                except asyncio.CancelledError:
                    # Không nên xảy ra (task cuối cùng luôn được để chạy hết),
                    # nhưng fail-closed nếu có.
                    triggered, reason = True, "Xin lỗi, tôi không thể tiếp tục trả lời câu hỏi này."
                except GuardrailCheckError as e:
                    logger.warning("conversation_runner | guardrail output check failed, fail-closed. error=%s", e)
                    triggered, reason = True, "Xin lỗi, tôi không thể tiếp tục trả lời câu hỏi này."

                if triggered:
                    assistant_text = reason or "Xin lỗi, tôi không thể trả lời câu hỏi này."
                    full_response.append(assistant_text)
                    yield f"data: {json.dumps(assistant_text)}\n\n"
                else:
                    clean_text, responder_options = _strip_think_and_options(last_responder_text)
                    pending_citations = last_responder_citations or None
                    # Only for an answer that is actually delivered — a refused
                    # turn must not leak the rows the guardrail just blocked.
                    pending_evidence = last_responder_evidence or None
                    # Ghép câu nhắc "có chart" KHÔNG qua LLM — Responder chạy song song với
                    # Visualization nên tự nó không biết chart có vẽ được hay không lúc sinh
                    # văn bản; đây là điểm DUY NHẤT đã biết chắc cả 2 nhánh đều xong.
                    if pending_charts:
                        suffix = "biểu đồ" if len(pending_charts) == 1 else f"{len(pending_charts)} biểu đồ"
                        clean_text = clean_text.rstrip() + f"\n\n📊 Đã tạo {suffix} minh hoạ bên dưới."
                    assistant_text = clean_text
                    full_response.append(clean_text)
                    for chunk in _chunk_words(clean_text):
                        yield f"data: {json.dumps(chunk)}\n\n"
                        await asyncio.sleep(_FAKE_STREAM_WORD_DELAY)

            # Evidence BEFORE options/suggestions: a client that stops reading at
            # the first "what next" frame still has the "why trust this" one.
            # Sent even when empty: "nothing ran" is a statement the UI shows
            # ("không đọc từ kho dữ liệu"), and its absence would be
            # indistinguishable from a broken wire.
            if pending_evidence is not None:
                yield f"data: {json.dumps({'__evidence': pending_evidence}, ensure_ascii=False, default=str)}\n\n"

            if responder_options:
                yield f"data: {json.dumps({'__options': responder_options})}\n\n"
            elif pending_suggestions:
                yield f"data: {json.dumps({'__suggestions': pending_suggestions})}\n\n"

            if pending_charts:
                yield f"data: {json.dumps({'__chart': pending_charts})}\n\n"

            if pending_citations:
                yield f"data: {json.dumps({'__citations': pending_citations})}\n\n"

            await asyncio.to_thread(flush_langfuse)
            yield "data: [DONE]\n\n"

        except Exception:
            logger.exception("event_stream error")
            yield "data: [ERROR] Đã xảy ra lỗi, vui lòng thử lại.\n\n"
        finally:
            if guardrail_task and not guardrail_task.done():
                guardrail_task.cancel()
            latency = int((time.monotonic() - started_at) * 1000)
            assistant_text = "".join(full_response)

            if conv_id:
                await save_message(conv_id, "user", req.query)
                assistant_msg_id = await save_message(
                    conv_id,
                    "assistant",
                    assistant_text,
                    latency_ms=latency,
                    metadata={
                        "agent_version_id": cfg.get("agent_version_id"),
                        "language": detected_language,
                        "intent": detected_intent,
                        # Recorded on the message so "how often is this platform
                        # asked to forecast?" is answerable from stored traffic
                        # rather than needing new instrumentation later.
                        "analysis_type": detected_analysis,
                        # Persisted on the message so the next turn can
                        # inherit them even if Redis has gone cold.
                        "slots": (
                            _slots_with_marker(detected_slots, detected_clarify)
                            if detected_slots else None
                        ),
                        **({"clarifications": detected_clarify} if detected_clarify else {}),
                        "responder_model": cfg.get("responder_model"),
                        **({"pending_options": responder_options} if responder_options else {}),
                        **({"pending_approval": conv_id} if conv_id and conv_id in _pending_reviews else {}),
                        **({"citations": pending_citations} if pending_citations else {}),
                    },
                )
                await cache_append_messages(conv_id, [
                    {"role": "user",      "content": req.query},
                    {"role": "assistant", "content": assistant_text},
                ])
                if detected_slots:
                    # Fast path for the next turn's refinement. The same slots
                    # went into the message metadata above, which is what makes
                    # this a cache rather than the source of truth.
                    #
                    # The clarification marker rides along with the slots rather
                    # than in its own key: the next turn reads exactly one thing
                    # to reconstruct context, so the two can never disagree about
                    # whether a question is currently outstanding.
                    await cache_set_slots(conv_id, _slots_with_marker(detected_slots, detected_clarify))
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
                            output={"response": assistant_text},
                            latency_ms=latency,
                        )

            # orchestrated=True: orchestrator sẽ ghi memory sau synthesize, agent không ghi
            if not req.orchestrated and cfg.get("memory_enabled") and conv_id:
                final_state = {**state, "response": assistant_text}
                await apply_memory_policy(
                    final_state, agent_id, tenant_id, user_ref,
                    conversation_id=conv_id, message_id=assistant_msg_id,
                )

            flush_langfuse()

            for nt in (node_traces if cfg.get("memory_enabled") and not req.orchestrated else []):
                if not nt["tool_name"].startswith("tool/"):
                    continue
                task_type = _DATA_TOOL_TASK_TYPE.get(nt["tool_name"][len("tool/"):])
                if not task_type:
                    continue
                _fire_and_forget(record_workflow_outcome(
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    task_type=task_type,
                    task_description=nt["input"].get("query") or "",
                    param_snapshot=nt["input"].get("arguments") or {},
                    outcome="success" if nt.get("status", "success") == "success" else "failed",
                    duration_ms=nt.get("latency_ms"),
                ))

    response = StreamingResponse(event_stream(), media_type="text/event-stream")
    response.headers["X-Conversation-Id"] = conv_id or ""
    return response
