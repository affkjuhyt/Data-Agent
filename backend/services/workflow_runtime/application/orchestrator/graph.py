from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from contextlib import nullcontext, suppress
from typing import Any

from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from psycopg_pool import AsyncConnectionPool

from libs.shared.guardrail_check import GuardrailCheckError, llm_guardrail_check
from services.workflow_runtime.application.orchestrator import config
from services.workflow_runtime.application.orchestrator.agent_catalog import fetch_published_agents
from services.workflow_runtime.application.orchestrator.executor import execute_plan
from services.workflow_runtime.application.orchestrator.executor.agent_client import (
    call_agent_stream,
    extract_guardrail_cfg,
)
from services.workflow_runtime.application.orchestrator.nodes.input_refine import (
    clarification_payload,
    input_refine_node,
    merge_clarification,
    run_input_refine,
)
from services.workflow_runtime.application.orchestrator.nodes.suggestion import run_suggestion
from services.workflow_runtime.application.orchestrator.planner import (
    create_plan,
    report_node_failure,
)
from services.workflow_runtime.application.orchestrator.schemas import TargetResult, TaskResult
from services.workflow_runtime.application.orchestrator.session_cache import (
    clear_cached_plan,
    load_cached_plan,
    save_cached_plan,
)
from services.workflow_runtime.application.orchestrator.state import OrchestratorState
from services.workflow_runtime.application.orchestrator.summary import (
    summarize,
    synthesis_judge_check,
)
from services.workflow_runtime.application.runtime_services.answer_ready import answer_ready_hook
from services.workflow_runtime.infrastructure.observability import get_langfuse_client

logger = logging.getLogger(__name__)

_MAX_REPLAN = 2

# ── Checkpointer ──────────────────────────────────────────────────────────────
# Postgres-backed rather than the in-process MemorySaver it replaced: this
# service runs multiple replicas behind a plain ClusterIP Service (no session
# affinity), so a /resume call for a pending clarification can land on a pod
# that never saw /stream open that session — MemorySaver made that a 404
# ("Session không tồn tại hoặc đã hoàn thành") even though the session was
# genuinely still open, just on a different pod. Any replica can now see any
# session's state.
#
# psycopg (v3), not this codebase's usual asyncpg — that's the driver
# langgraph-checkpoint-postgres is built on. The checkpoint tables it creates
# (checkpoints/checkpoint_blobs/checkpoint_writes) have no tenant_id column,
# so they're outside anything a1b2c3d4e5f6_enable_tenant_rls.py's
# tenant_id-column scan would have found — no RLS policy applies to them,
# same as any other non-tenant-scoped table, and thread_id (the session/trace
# id) is already the effective partition key.
#
# The state holds a live PlanningProcess (needed for report_node_failure) which
# the default msgpack serde can't encode — checkpoint writes crashed the
# multi-agent turn after the answer already streamed. pickle_fallback pickles
# such objects; kept identically for the Postgres saver.
_CHECKPOINTER_SERDE = JsonPlusSerializer(pickle_fallback=True)
_ASYNC_DRIVER_RE = re.compile(r"^postgresql\+\w+://")

# The saver's tables live in their own schema. runtime_writer has no CREATE on
# public (Postgres 15+ default, and deploy/docker/sql/grant-roles.sql keeps it
# that way), but setup() issues CREATE TABLE IF NOT EXISTS on every start, so it
# gets CREATE on this schema instead. The saver's SQL is unqualified, so
# search_path is enough. A pooler in transaction mode would drop the `options`
# startup parameter; set search_path on the role there instead.
_CHECKPOINTER_SCHEMA = "langgraph"


def _checkpointer_dsn() -> str:
    url = os.environ.get("RUNTIME_DB_URL") or os.environ.get("DATABASE_URL") or ""
    if not url:
        raise RuntimeError(
            "RUNTIME_DB_URL/DATABASE_URL not set — cannot open the shared "
            "LangGraph checkpointer (Postgres)."
        )
    # This codebase's URLs are SQLAlchemy-style ("postgresql+asyncpg://...");
    # psycopg wants the plain scheme.
    return _ASYNC_DRIVER_RE.sub("postgresql://", url)


_graph_lock = asyncio.Lock()
_compiled_graph: Any = None
_checkpoint_pool: AsyncConnectionPool | None = None


async def _open_checkpointer() -> AsyncPostgresSaver:
    global _checkpoint_pool
    _checkpoint_pool = AsyncConnectionPool(
        conninfo=_checkpointer_dsn(),
        max_size=10,
        open=False,
        # autocommit: AsyncPostgresSaver manages its own transactions per call.
        # prepare_threshold=None: disables server-side prepared statements —
        # harmless direct-to-Postgres, and required if a pooler (e.g.
        # PgBouncer, provisioned elsewhere in this cluster) ever sits in front
        # of this DSN in transaction-pooling mode.
        kwargs={
            "autocommit": True,
            "prepare_threshold": None,
            "options": f"-c search_path={_CHECKPOINTER_SCHEMA}",
        },
    )
    await _checkpoint_pool.open()
    saver = AsyncPostgresSaver(_checkpoint_pool, serde=_CHECKPOINTER_SERDE)
    await saver.setup()
    return saver


# ── Route ─────────────────────────────────────────────────────────────────────

def route_node(state: OrchestratorState) -> dict[str, Any]:
    return {}


def _route_mode(state: OrchestratorState) -> str:
    return "single_agent" if state.get("agent_id") else "fetch_agents"


def _route_start(state: OrchestratorState) -> str:
    # A single-agent turn runs its input_refine check alongside the agent
    # (single_agent_node); only a multi-agent turn refines first.
    return "single_agent" if state.get("agent_id") else "input_refine"


def _same_question(a: str, b: str) -> bool:
    return " ".join(a.casefold().split()) == " ".join(b.casefold().split())


# ── Single agent path ─────────────────────────────────────────────────────────

_AGENT_STREAM_END = object()


async def single_agent_node(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    guardrail_task = (config.get("configurable") or {}).get("guardrail_task")
    query = state["query"]

    # The clarification/rewrite check (input_refine) runs ALONGSIDE the agent
    # instead of in front of it, where it held every question for one LLM call.
    # The agent starts on the user's words and its events are held until the
    # check decides: keep → release them, rewrite → restart on the rewritten
    # question, clarify → drop them and ask. Nothing from a dropped run reaches
    # the user.
    #
    # A turn that answers a clarification (resume, or picks from the agent's
    # card) is checked too, rewrite-only. The answer is merged into the
    # question that was asked, which may still say "ông" — and the agent sees
    # this question alone, not the conversation, so an unresolved pronoun was
    # searched as is and answered about whoever the search found. Asking again
    # is refused there: that is the loop the answer just ended.
    clarification_answers = state.get("clarification_answers") or []
    answered = bool(state.get("clarification_response") or clarification_answers)
    if state.get("clarification_response"):
        query = merge_clarification(state["query"], state["clarification_response"])
    refine_task: asyncio.Task[tuple[str, list[dict[str, Any]]]] | None = asyncio.create_task(
        run_input_refine(query, state.get("messages", []), state.get("cfg", {}), allow_clarify=not answered)
    )

    # Gate input guardrail — await task đã bắn nền từ routers/stream.py, ngay
    # trước khi gọi ra ngoài (side-effect thật, workflow_runtime có thể chạy
    # bash/SQL). Nếu guardrail đã xong từ sớm (thường vậy) thì không mất thêm
    # latency gì cả.
    if guardrail_task:
        triggered, reason = await guardrail_task
        if triggered:
            if refine_task is not None:
                refine_task.cancel()
            refusal = reason or "Xin lỗi, tôi không thể trả lời câu hỏi này."
            await adispatch_custom_event("token", refusal)
            return {
                "final_response": refusal,
                "success": False,
                "guardrail_triggered": True,
                "guardrail_reason": reason,
                "target_results": [],
            }

    t0 = time.monotonic()

    # Span thủ công tương đương "[task]" bên multi-agent — parent của mọi
    # span workflow_runtime tạo trong call_agent_stream() (cùng process, OTEL
    # context là ambient nên không cần traceparent header nữa).
    lf = get_langfuse_client()
    ctx = lf.start_as_current_span(
        name="single-agent",
        input={"query": query, "agent_id": state["agent_id"]},
    ) if lf else nullcontext()

    buffer: list[str] = []
    conversation_id = None
    citations: list[dict] = []
    success = False
    response_text = ""
    latency_ms = 0
    questions: list[dict[str, Any]] = []

    # Suggestions only need the approved answer. The runner announces it before
    # replaying it word by word (runtime_services/answer_ready.py), so the
    # suggestion call runs alongside the replay instead of after it.
    suggestion_task: asyncio.Task[list[str]] | None = None

    def _on_answer_ready(text: str) -> None:
        nonlocal suggestion_task
        if suggestion_task is None:
            suggestion_task = asyncio.create_task(run_suggestion(
                query, state.get("messages", []), text, state.get("cfg", {}),
            ))

    def start_agent(prompt: str) -> tuple[asyncio.Task[None], asyncio.Queue[Any]]:
        events: asyncio.Queue[Any] = asyncio.Queue()

        async def pump() -> None:
            try:
                async for item in call_agent_stream(
                    agent_id=state["agent_id"],
                    prompt=prompt,
                    user_id=state["user_id"],
                    tenant_id=state["tenant_id"],
                    user_roles=state.get("user_roles") or [],
                    guardrail_cfg=extract_guardrail_cfg(state.get("cfg", {})),
                    conversation_id=state.get("agent_conversation_id"),
                    clarification_answers=clarification_answers,
                ):
                    await events.put(item)
            except Exception as e:
                await events.put(("__failed__", e))
            finally:
                events.put_nowait(_AGENT_STREAM_END)

        return asyncio.create_task(pump()), events

    async def stop(task: asyncio.Task[None]) -> None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    agent_task: asyncio.Task[None] | None = None
    hook_token = answer_ready_hook.set(_on_answer_ready)
    try:
        with ctx as span:
            agent_task, events = start_agent(query)
            if refine_task is not None:
                refined, questions = await refine_task
                if questions or not _same_question(refined, query):
                    await stop(agent_task)
                    if suggestion_task is not None:
                        suggestion_task.cancel()
                        suggestion_task = None
                    if not questions:
                        logger.info("single_agent | input_refine rewrote the question — restarting the agent on it")
                        query = refined
                        agent_task, events = start_agent(query)

            if not questions:
                try:
                    while True:
                        item = await events.get()
                        if item is _AGENT_STREAM_END:
                            break
                        event_type, data = item
                        if event_type == "__failed__":
                            raise data
                        if event_type == "token":
                            buffer.append(data)
                            await adispatch_custom_event("token", data)
                        elif event_type == "reset":
                            buffer.clear()
                            await adispatch_custom_event("reset", None)
                        elif event_type == "conv_id":
                            conversation_id = data
                        elif event_type == "pending_review":
                            await adispatch_custom_event("pending_review", data)
                        elif event_type == "chart":
                            await adispatch_custom_event("chart", data)
                        elif event_type == "citations":
                            citations = data
                            await adispatch_custom_event("citations", data)
                        elif event_type == "options":
                            await adispatch_custom_event("options", data)
                        elif event_type == "evidence":
                            await adispatch_custom_event("evidence", data)
                    success = True
                except Exception as e:
                    logger.warning("single_agent | failed: %s", e)
                    err = "Đã xảy ra lỗi, vui lòng thử lại."
                    buffer = [err]

                response_text = "".join(buffer)
                latency_ms = round((time.monotonic() - t0) * 1000)
                if span is not None:
                    span.update(
                        output={"response": response_text[:2000], "conversation_id": conversation_id},
                        metadata={"latency_ms": latency_ms, "success": success},
                    )
    except BaseException:
        if agent_task is not None and not agent_task.done():
            agent_task.cancel()
        if refine_task is not None and not refine_task.done():
            refine_task.cancel()
        if suggestion_task is not None:
            suggestion_task.cancel()
        raise
    finally:
        answer_ready_hook.reset(hook_token)

    if questions:
        # Outside the span and the stream handling: interrupt() raises to pause
        # the graph. On resume this node runs again with the answer merged in.
        logger.info("input_refine | clarification needed, questions=%d", len(questions))
        interrupt(clarification_payload(questions))

    # Only an answer that fully arrived keeps its early suggestions; otherwise
    # suggestion_node runs on the final text as before.
    suggestions_update: dict[str, Any] = {}
    if suggestion_task is not None:
        if success:
            suggestions_update = {"suggestions": await suggestion_task, "suggestions_ready": True}
        else:
            suggestion_task.cancel()

    task_result = TaskResult(
        task_index=0,
        task_title=query[:100],
        agent_id=state["agent_id"],
        output=response_text,
        success=success,
        conversation_id=conversation_id,
        citations=citations,
        latency_ms=latency_ms,
    )
    return {
        "rewritten_query": query,
        "final_response": response_text,
        "success": success,
        "executor_ms": latency_ms,
        "target_results": [TargetResult(
            node_id="single",
            description=query[:100],
            task_results=[task_result],
            success=success,
        )],
        **suggestions_update,
    }


# ── Multi agent path ──────────────────────────────────────────────────────────

async def fetch_agents_node(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    # routers/stream.py starts this query alongside input_refine — it only needs
    # tenant_id. Query here when it did not (resume paths).
    prefetched = (config.get("configurable") or {}).get("agents_task")
    agents = await prefetched if prefetched is not None else await fetch_published_agents(state["tenant_id"])
    return {"agents": agents}


def discard_plan_speculation(speculation: dict[str, Any] | None) -> None:
    """Drop the ToT plan routers/stream.py started ahead of input_refine."""
    task = (speculation or {}).pop("task", None)
    if task is None:
        return
    if not task.done():
        task.cancel()
    elif not task.cancelled():
        task.exception()  # retrieve it, so an unused failed plan is not reported as unhandled


def _take_plan_speculation(speculation: dict[str, Any] | None, query: str) -> "asyncio.Task[Any] | None":
    """The plan started on the user's words, if input_refine kept those words."""
    if not speculation or "task" not in speculation:
        return None
    if not _same_question(speculation.get("query", ""), query):
        discard_plan_speculation(speculation)
        return None
    task: asyncio.Task[Any] = speculation.pop("task")
    return task


async def tot_planner_node(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    t0 = time.monotonic()
    redis = (config.get("configurable") or {}).get("redis")
    speculation = (config.get("configurable") or {}).get("plan_speculation")
    tenant_id, session_id, query = state["tenant_id"], state["session_id"], state["rewritten_query"]

    # Client retried the same session_id (e.g. timed out waiting for a slow
    # multi-agent run) — resume the live ToT tree instead of re-planning and
    # re-billing every agent call that already succeeded.
    cached = await load_cached_plan(redis, tenant_id, session_id, query)
    if cached is not None:
        discard_plan_speculation(speculation)
        process, plan, progress = cached
        logger.info("tot_planner | resumed session=%s plan=%s", session_id, plan.plan_id)
        return {
            "plan": plan,
            "process": process,
            "completed_node_ids": progress.get("completed_node_ids", []),
            "planner_ms": 0,
            "success": True,
        }

    # routers/stream.py started planning on the user's words while input_refine
    # ran; it is the same plan when the question was kept.
    prefetched = _take_plan_speculation(speculation, query)
    if prefetched is not None:
        logger.info("tot_planner | reusing the plan started alongside input_refine")
        plan_result = await prefetched
    else:
        plan_result = await create_plan(query, agents=state["agents"])
    planner_ms = round((time.monotonic() - t0) * 1000)
    if plan_result is None:
        return {"plan": None, "planner_ms": planner_ms, "success": False}

    process, plan = plan_result
    await save_cached_plan(redis, tenant_id, session_id, query, process, plan, [])
    return {
        "plan": plan,
        "process": process,
        "completed_node_ids": [],
        "planner_ms": planner_ms,
        "success": True,
    }


def _route_after_input_refine(state: OrchestratorState) -> str:
    return "blocked" if state.get("guardrail_triggered") else "route"


def _route_after_planner(state: OrchestratorState) -> str:
    return "execute" if state.get("plan") else "blocked"


def _route_after_single_agent(state: OrchestratorState) -> str:
    return "blocked" if state.get("guardrail_triggered") else "suggestion"


def _route_after_execute(state: OrchestratorState) -> str:
    return "blocked" if state.get("guardrail_triggered") else "summary"


def _route_after_summary(state: OrchestratorState) -> str:
    return "blocked" if state.get("guardrail_triggered") else "suggestion"


async def execute_node(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    # Gate input guardrail — cùng lý do như single_agent_node: đây là điểm
    # ngay trước khi gọi ra ngoài để thực thi các agent con thật.
    guardrail_task = (config.get("configurable") or {}).get("guardrail_task")
    if guardrail_task:
        triggered, reason = await guardrail_task
        if triggered:
            refusal = reason or "Xin lỗi, tôi không thể trả lời câu hỏi này."
            await adispatch_custom_event("token", refusal)
            return {
                "final_response": refusal,
                "success": False,
                "guardrail_triggered": True,
                "guardrail_reason": reason,
                "target_results": [],
                "executor_ms": 0,
            }

    plan = state["plan"]
    process = state["process"]
    redis = (config.get("configurable") or {}).get("redis")
    tenant_id, session_id, query = state["tenant_id"], state["session_id"], state["rewritten_query"]

    t0 = time.monotonic()
    all_target_results = []
    overall_success = False
    recovered = False
    completed_node_ids: set[str] = set(state.get("completed_node_ids") or [])

    guardrail_cfg = extract_guardrail_cfg(state.get("cfg", {}))
    for attempt in range(_MAX_REPLAN + 1):
        result = await execute_plan(
            plan,
            user_id=state["user_id"],
            user_roles=state.get("user_roles") or [],
            original_query=query,
            tenant_id=tenant_id,
            agents=state["agents"],
            skip_node_ids=completed_node_ids,
            orchestrated=True,
            guardrail_cfg=guardrail_cfg,
        )
        all_target_results.extend(result.target_results)
        completed_node_ids.update(tr.node_id for tr in result.target_results if tr.success)

        if result.success:
            overall_success = True
            break

        if attempt < _MAX_REPLAN and result.failed_node_id:
            recovery = await report_node_failure(process, result.failed_node_id)
            if recovery.get("recovered") and recovery.get("plan"):
                plan = recovery["plan"]
                recovered = True
                await save_cached_plan(redis, tenant_id, session_id, query, process, plan, completed_node_ids)
                continue

        break

    await clear_cached_plan(redis, tenant_id, session_id)

    # Dedup target results (giữ kết quả cuối cùng của mỗi node_id)
    seen: dict[str, int] = {}
    for i, tr in enumerate(all_target_results):
        seen[tr.node_id] = i
    deduped = [all_target_results[i] for i in sorted(seen.values())]

    return {
        "plan": plan,
        "target_results": deduped,
        "success": overall_success,
        "recovered": recovered,
        "executor_ms": round((time.monotonic() - t0) * 1000),
    }


async def _guardrail_output_check(text: str, cfg: dict[str, Any]) -> tuple[bool, str]:
    try:
        return await llm_guardrail_check(text, cfg, check_type="output")
    except GuardrailCheckError as e:
        # Fail-closed cho output (khác input) — lỗi/timeout coi như vi phạm.
        logger.warning("summary | guardrail output check failed, fail-closed. error=%s", e)
        return True, "Xin lỗi, tôi không thể tiếp tục trả lời câu hỏi này."


def _merge_citations(target_results: list) -> list[dict]:
    """Gộp citations từ tất cả agent con thành công thành 1 bảng nguồn duy nhất,
    dedupe theo source + renumber liên tục — bước tổng hợp (summarize()) viết
    lại toàn bộ câu trả lời nên KHÔNG giữ được vị trí [n] chính xác của từng
    agent con, chỉ có thể đảm bảo đúng ở mức "những nguồn nào đã được dùng".
    Chỉ lấy citations của task_results[0] mỗi target — khớp đúng với cách
    `outputs` (feed vào summarize()) cũng chỉ lấy task_results[0], để không
    hiện nguồn của 1 sub-task mà nội dung của nó chưa từng được đưa vào bản
    tổng hợp."""
    merged: list[dict] = []
    seen_sources: set[str] = set()
    for tr in target_results:
        if not (tr.success and tr.task_results):
            continue
        for c in tr.task_results[0].citations:
            key = c.get("source", "")
            if key in seen_sources:
                continue
            seen_sources.add(key)
            merged.append({**c, "index": len(merged) + 1})
    return merged


async def summary_node(state: OrchestratorState) -> dict[str, Any]:
    t0 = time.monotonic()
    target_results = state.get("target_results", [])
    outputs = [
        tr.task_results[0].output
        for tr in target_results
        if tr.success and tr.task_results
    ]
    citations = _merge_citations(target_results)
    cfg = state.get("cfg", {})
    query = state["rewritten_query"]

    # Sinh xong toàn bộ câu tổng hợp trước (không stream live) — để có thể
    # check guardrail trên TOÀN BỘ text, tránh rủi ro cắt cụt ý nghĩa nếu check
    # theo từng câu. Đổi hẳn latency-chồng-lấp của streaming thật lấy độ đúng
    # đắn — xem Context trong plan.
    #
    # synthesis_judge_check (đúng-đắn của bước GỘP, có retry) và guardrail
    # output check (an toàn, không retry) chạy SONG SONG trên cùng 1 bản text
    # mỗi lần thử — không tuần tự, để không cộng dồn latency 2 lệnh gọi LLM.
    # Nếu Judge FAIL, kết quả guardrail của bản đó bị bỏ (sắp lỗi thời), sinh
    # lại và race tiếp.
    text = ""
    triggered, reason = False, ""
    for attempt in range(config.MAX_SYNTHESIS_RETRY + 1):
        text = await summarize(query, outputs)
        (judge_passed, judge_reason), (triggered, reason) = await asyncio.gather(
            synthesis_judge_check(query, outputs, text),
            _guardrail_output_check(text, cfg),
        )
        if judge_passed or attempt == config.MAX_SYNTHESIS_RETRY:
            break
        logger.info("synthesis_judge | attempt=%d FAIL reason=%s — sinh lại tổng hợp", attempt, judge_reason)

    if triggered:
        refusal = reason or "Xin lỗi, tôi không thể trả lời câu hỏi này."
        await adispatch_custom_event("token", refusal)
        return {
            "final_response": refusal,
            "summary_ms": round((time.monotonic() - t0) * 1000),
            "guardrail_triggered": True,
            "guardrail_reason": reason,
        }

    # Suggestions only need the approved text — start them now so they run
    # alongside the rest of this node instead of after it (suggestion_node skips).
    suggestion_task = asyncio.create_task(run_suggestion(query, state.get("messages", []), text, cfg))
    try:
        # The approved text in one frame. It used to be replayed word by word
        # with an artificial delay, which only added seconds to an answer that
        # was already complete.
        await adispatch_custom_event("token", text)

        if citations:
            await adispatch_custom_event("citations", citations)
    except BaseException:
        suggestion_task.cancel()
        raise

    summary_ms = round((time.monotonic() - t0) * 1000)
    return {
        "final_response": text,
        "citations": citations,
        "suggestions": await suggestion_task,
        "suggestions_ready": True,
        "summary_ms": summary_ms,
    }


async def suggestion_node(state: OrchestratorState) -> dict[str, Any]:
    if state.get("suggestions_ready"):
        return {}
    suggestions = await run_suggestion(
        state["rewritten_query"],
        state.get("messages", []),
        state.get("final_response", ""),
        state.get("cfg", {}),
    )
    return {"suggestions": suggestions}


# ── Build graph ───────────────────────────────────────────────────────────────

def build_graph(checkpointer: Any) -> Any:
    builder = StateGraph(OrchestratorState)

    builder.add_node("input_refine", input_refine_node)
    builder.add_node("route",        route_node)
    builder.add_node("single_agent", single_agent_node)
    builder.add_node("fetch_agents", fetch_agents_node)
    builder.add_node("tot_planner",  tot_planner_node)
    builder.add_node("execute",      execute_node)
    builder.add_node("summary",      summary_node)
    builder.add_node("suggestion",   suggestion_node)

    # Không còn node guardrail riêng chặn đầu graph — LLM guardrail check chạy
    # nền song song (bắn từ routers/stream.py). input_refine_node tự đua với
    # guardrail_task (asyncio.wait FIRST_COMPLETED) — nếu guardrail xong trước
    # và bị chặn, huỷ luôn LLM call của input_refine, route thẳng "blocked"
    # ngay tại đây thay vì phải đợi hết input_refine mới biết. Gate cuối
    # (single_agent_node/execute_node) vẫn giữ nguyên, phòng case guardrail
    # xong SAU khi input_refine đã chạy xong.
    # Single-agent turns go straight to the agent, which runs the input_refine
    # check alongside itself (single_agent_node). Multi-agent turns refine first,
    # while routers/stream.py already plans on the user's words (tot_planner_node).
    builder.add_conditional_edges(
        START, _route_start,
        {"single_agent": "single_agent", "input_refine": "input_refine"},
    )
    builder.add_conditional_edges(
        "input_refine", _route_after_input_refine,
        {"route": "route", "blocked": END},
    )
    builder.add_conditional_edges(
        "route", _route_mode,
        {"single_agent": "single_agent", "fetch_agents": "fetch_agents"},
    )

    builder.add_conditional_edges(
        "single_agent", _route_after_single_agent,
        {"suggestion": "suggestion", "blocked": END},
    )

    builder.add_edge("fetch_agents", "tot_planner")
    builder.add_conditional_edges(
        "tot_planner", _route_after_planner,
        {"execute": "execute", "blocked": END},
    )
    builder.add_conditional_edges(
        "execute", _route_after_execute,
        {"summary": "summary", "blocked": END},
    )
    builder.add_conditional_edges(
        "summary", _route_after_summary,
        {"suggestion": "suggestion", "blocked": END},
    )

    builder.add_edge("suggestion", END)

    return builder.compile(checkpointer=checkpointer)


async def get_graph() -> Any:
    """Lazily opens the shared checkpointer and compiles the graph on first
    call, then returns the cached instance — compiling eagerly at import time
    (the previous shape) can't work here since opening the checkpointer's
    connection pool and running its one-time `.setup()` are both async."""
    global _compiled_graph
    if _compiled_graph is None:
        async with _graph_lock:
            if _compiled_graph is None:
                checkpointer = await _open_checkpointer()
                _compiled_graph = build_graph(checkpointer)
    return _compiled_graph
