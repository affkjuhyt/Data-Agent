from __future__ import annotations

import logging
import time
from contextlib import nullcontext

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END

from agent_catalog import fetch_published_agents
from executor import execute_plan
from langchain_core.callbacks.manager import adispatch_custom_event

from executor.agent_client import call_agent_stream, traceparent_of
from nodes.guardrail import run_guardrail
from nodes.input_refine import input_refine_node
from nodes.suggestion import run_suggestion
from observability import get_langfuse_client
from planner import create_plan, report_node_failure
from schemas import TargetResult, TaskResult
from session_cache import clear_cached_plan, load_cached_plan, save_cached_plan
from state import OrchestratorState
from summary import summarize_stream

logger = logging.getLogger(__name__)

_MAX_REPLAN = 2
_checkpointer = MemorySaver()


# ── Guardrail ────────────────────────────────────────────────────────────────

async def guardrail_node(state: OrchestratorState) -> dict:
    triggered, reason = await run_guardrail(state["query"], state.get("cfg", {}))
    return {"guardrail_triggered": triggered, "guardrail_reason": reason}


def _route_guardrail(state: OrchestratorState) -> str:
    return "blocked" if state.get("guardrail_triggered") else "input_refine"


# ── Route ─────────────────────────────────────────────────────────────────────

def route_node(state: OrchestratorState) -> dict:
    return {}


def _route_mode(state: OrchestratorState) -> str:
    return "single_agent" if state.get("agent_id") else "fetch_agents"


# ── Single agent path ─────────────────────────────────────────────────────────

async def single_agent_node(state: OrchestratorState) -> dict:
    t0 = time.monotonic()

    # Span thủ công tương đương "[task]" bên multi-agent — đây là parent mà
    # traceparent header (dựng thủ công từ span.trace_id/span.id, xem
    # agent_client.traceparent_of) sẽ trỏ tới khi call_agent_stream() gọi HTTP
    # sang workflow-runtime.
    lf = get_langfuse_client()
    ctx = lf.start_as_current_span(
        name="single-agent",
        input={"query": state["rewritten_query"], "agent_id": state["agent_id"]},
    ) if lf else nullcontext()

    buffer: list[str] = []
    conversation_id = None
    success = False
    with ctx as span:
        try:
            async for event_type, data in call_agent_stream(
                agent_id=state["agent_id"],
                prompt=state["rewritten_query"],
                user_id=state["user_id"],
                tenant_id=state["tenant_id"],
                traceparent=traceparent_of(span),
            ):
                if event_type == "token":
                    buffer.append(data)
                    await adispatch_custom_event("token", data)
                elif event_type == "conv_id":
                    conversation_id = data
                elif event_type == "pending_review":
                    await adispatch_custom_event("pending_review", data)
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

    task_result = TaskResult(
        task_index=0,
        task_title=state["rewritten_query"][:100],
        agent_id=state["agent_id"],
        output=response_text,
        success=success,
        conversation_id=conversation_id,
        latency_ms=latency_ms,
    )
    return {
        "final_response": response_text,
        "success": success,
        "executor_ms": latency_ms,
        "target_results": [TargetResult(
            node_id="single",
            description=state["rewritten_query"][:100],
            task_results=[task_result],
            success=success,
        )],
    }


# ── Multi agent path ──────────────────────────────────────────────────────────

async def fetch_agents_node(state: OrchestratorState) -> dict:
    agents = await fetch_published_agents(state["tenant_id"])
    return {"agents": agents}


async def tot_planner_node(state: OrchestratorState, config: RunnableConfig) -> dict:
    t0 = time.monotonic()
    redis = (config.get("configurable") or {}).get("redis")
    tenant_id, session_id, query = state["tenant_id"], state["session_id"], state["rewritten_query"]

    # Client retried the same session_id (e.g. timed out waiting for a slow
    # multi-agent run) — resume the live ToT tree instead of re-planning and
    # re-billing every agent call that already succeeded.
    cached = await load_cached_plan(redis, tenant_id, session_id, query)
    if cached is not None:
        process, plan, progress = cached
        logger.info("tot_planner | resumed session=%s plan=%s", session_id, plan.plan_id)
        return {
            "plan": plan,
            "process": process,
            "completed_node_ids": progress.get("completed_node_ids", []),
            "planner_ms": 0,
            "success": True,
        }

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


def _route_after_planner(state: OrchestratorState) -> str:
    return "execute" if state.get("plan") else "blocked"


async def execute_node(state: OrchestratorState, config: RunnableConfig) -> dict:
    plan = state["plan"]
    process = state["process"]
    redis = (config.get("configurable") or {}).get("redis")
    tenant_id, session_id, query = state["tenant_id"], state["session_id"], state["rewritten_query"]

    t0 = time.monotonic()
    all_target_results = []
    overall_success = False
    recovered = False
    completed_node_ids: set[str] = set(state.get("completed_node_ids") or [])

    for attempt in range(_MAX_REPLAN + 1):
        result = await execute_plan(
            plan,
            user_id=state["user_id"],
            original_query=query,
            tenant_id=tenant_id,
            agents=state["agents"],
            skip_node_ids=completed_node_ids,
            orchestrated=True,
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


async def summary_node(state: OrchestratorState) -> dict:
    t0 = time.monotonic()
    outputs = [
        tr.task_results[0].output
        for tr in state.get("target_results", [])
        if tr.success and tr.task_results
    ]
    buffer: list[str] = []
    async for token in summarize_stream(state["rewritten_query"], outputs):
        buffer.append(token)
        await adispatch_custom_event("token", token)
    return {
        "final_response": "".join(buffer),
        "summary_ms": round((time.monotonic() - t0) * 1000),
    }


async def suggestion_node(state: OrchestratorState) -> dict:
    suggestions = await run_suggestion(
        state["rewritten_query"],
        state.get("messages", []),
        state.get("final_response", ""),
        state.get("cfg", {}),
    )
    return {"suggestions": suggestions}


# ── Build graph ───────────────────────────────────────────────────────────────

def build_graph():
    builder = StateGraph(OrchestratorState)

    builder.add_node("guardrail",    guardrail_node)
    builder.add_node("input_refine", input_refine_node)
    builder.add_node("route",        route_node)
    builder.add_node("single_agent", single_agent_node)
    builder.add_node("fetch_agents", fetch_agents_node)
    builder.add_node("tot_planner",  tot_planner_node)
    builder.add_node("execute",      execute_node)
    builder.add_node("summary",      summary_node)
    builder.add_node("suggestion",   suggestion_node)

    builder.add_edge(START, "guardrail")
    builder.add_conditional_edges(
        "guardrail", _route_guardrail,
        {"blocked": END, "input_refine": "input_refine"},
    )
    builder.add_edge("input_refine", "route")
    builder.add_conditional_edges(
        "route", _route_mode,
        {"single_agent": "single_agent", "fetch_agents": "fetch_agents"},
    )

    builder.add_edge("single_agent", "suggestion")

    builder.add_edge("fetch_agents", "tot_planner")
    builder.add_conditional_edges(
        "tot_planner", _route_after_planner,
        {"execute": "execute", "blocked": END},
    )
    builder.add_edge("execute", "summary")
    builder.add_edge("summary", "suggestion")

    builder.add_edge("suggestion", END)

    return builder.compile(checkpointer=_checkpointer)


_graph = build_graph()


def get_graph():
    return _graph
