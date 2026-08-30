from __future__ import annotations

from typing import Any
import asyncio
import logging
import time
from contextlib import nullcontext

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import StateGraph, START, END

from orchestrator import config
from orchestrator.agent_catalog import fetch_published_agents
from orchestrator.executor import execute_plan
from langchain_core.callbacks.manager import adispatch_custom_event

from orchestrator.executor.agent_client import call_agent_stream, extract_guardrail_cfg
from orchestrator.nodes.input_refine import input_refine_node
from orchestrator.nodes.suggestion import run_suggestion
from observability import get_langfuse_client
from orchestrator.planner import create_plan, report_node_failure
from orchestrator.schemas import TargetResult, TaskResult
from libs.shared.guardrail_check import GuardrailCheckError, llm_guardrail_check
from orchestrator.session_cache import clear_cached_plan, load_cached_plan, save_cached_plan
from orchestrator.state import OrchestratorState
from orchestrator.summary import summarize, synthesis_judge_check

logger = logging.getLogger(__name__)

_MAX_REPLAN = 2
# The state holds a live PlanningProcess (needed for report_node_failure) which the
# default msgpack serde can't encode → checkpoint writes crashed the multi-agent
# turn after the answer already streamed. pickle_fallback pickles such objects.
_checkpointer = MemorySaver(serde=JsonPlusSerializer(pickle_fallback=True))
_FAKE_STREAM_WORD_DELAY = 0.02  # giây/từ — hiệu ứng gõ chữ khi replay text đã duyệt xong


# ── Route ─────────────────────────────────────────────────────────────────────

def route_node(state: OrchestratorState) -> dict[str, Any]:
    return {}


def _route_mode(state: OrchestratorState) -> str:
    return "single_agent" if state.get("agent_id") else "fetch_agents"


# ── Single agent path ─────────────────────────────────────────────────────────

async def single_agent_node(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    # Gate input guardrail — await task đã bắn nền từ routers/stream.py, ngay
    # trước khi gọi ra ngoài (side-effect thật, workflow-runtime có thể chạy
    # bash/SQL). Nếu guardrail đã xong từ sớm (thường vậy) thì không mất thêm
    # latency gì cả.
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
            }

    t0 = time.monotonic()

    # Span thủ công tương đương "[task]" bên multi-agent — parent của mọi
    # span workflow-runtime tạo trong call_agent_stream() (cùng process, OTEL
    # context là ambient nên không cần traceparent header nữa).
    lf = get_langfuse_client()
    ctx = lf.start_as_current_span(
        name="single-agent",
        input={"query": state["rewritten_query"], "agent_id": state["agent_id"]},
    ) if lf else nullcontext()

    buffer: list[str] = []
    conversation_id = None
    citations: list[dict] = []
    success = False
    with ctx as span:
        try:
            async for event_type, data in call_agent_stream(
                agent_id=state["agent_id"],
                prompt=state["rewritten_query"],
                user_id=state["user_id"],
                tenant_id=state["tenant_id"],
                user_roles=state.get("user_roles") or [],
                guardrail_cfg=extract_guardrail_cfg(state.get("cfg", {})),
            ):
                if event_type == "token":
                    buffer.append(data)
                    await adispatch_custom_event("token", data)
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

    task_result = TaskResult(
        task_index=0,
        task_title=state["rewritten_query"][:100],
        agent_id=state["agent_id"],
        output=response_text,
        success=success,
        conversation_id=conversation_id,
        citations=citations,
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

async def fetch_agents_node(state: OrchestratorState) -> dict[str, Any]:
    agents = await fetch_published_agents(state["tenant_id"])
    return {"agents": agents}


async def tot_planner_node(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
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

    # Pass — "giả streaming": chia theo từ, thả dần ra SSE với độ trễ nhân tạo
    # nhỏ để giữ lại hiệu ứng gõ chữ, dù text đã sinh xong từ trước.
    words = text.split(" ")
    for i, word in enumerate(words):
        chunk = word if i == 0 else " " + word
        await adispatch_custom_event("token", chunk)
        await asyncio.sleep(_FAKE_STREAM_WORD_DELAY)

    if citations:
        await adispatch_custom_event("citations", citations)

    return {
        "final_response": text,
        "citations": citations,
        "summary_ms": round((time.monotonic() - t0) * 1000),
    }


async def suggestion_node(state: OrchestratorState) -> dict[str, Any]:
    suggestions = await run_suggestion(
        state["rewritten_query"],
        state.get("messages", []),
        state.get("final_response", ""),
        state.get("cfg", {}),
    )
    return {"suggestions": suggestions}


# ── Build graph ───────────────────────────────────────────────────────────────

def build_graph() -> Any:
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
    builder.add_edge(START, "input_refine")
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

    return builder.compile(checkpointer=_checkpointer)


_graph = build_graph()


def get_graph() -> Any:
    return _graph
