from __future__ import annotations

from typing import Any
import asyncio
import json
import logging
import os
from datetime import datetime

import litellm
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from services.workflow_runtime.orchestrator.state import OrchestratorState
from services.workflow_runtime.runtime_services import llm_call

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:8500")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY", "sk-dev")

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")
_SYSTEM = open(os.path.join(_PROMPTS_DIR, "input_refine_system.txt"), encoding="utf-8").read()

# Business calendar, as the agent planner uses (nodes/model_harness/planner.py).
_BUSINESS_TZ = os.environ.get("BUSINESS_TIMEZONE", "Asia/Ho_Chi_Minh")


def _today_line() -> str:
    # Without today's date the model called 2026 "the future" and asked whether
    # data for it existed — a clarification round trip on a complete question.
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(_BUSINESS_TZ))
    except Exception:
        now = datetime.now()
    return f"Hôm nay là ngày {now:%d/%m/%Y}."


def merge_clarification(query: str, clarification: str) -> str:
    """The question the agent works on once the user answered a clarification."""
    return f"{query}\n\nThông tin bổ sung: {clarification}"


async def run_input_refine(
    query: str,
    messages: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> tuple[str, list[str]]:
    """
    Standalone function cho SSE endpoint.
    Returns:
        (refined_query, clarification_questions)
        — nếu clarification_questions rỗng → tiếp tục với refined_query
        — nếu không rỗng → cần hỏi user
    """
    history_lines = []
    for m in messages[-6:]:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()[:300]
        if role in ("user", "assistant"):
            history_lines.append(f"{role.capitalize()}: {content}")

    user_content = _today_line() + "\n\n"
    if history_lines:
        user_content += "Lịch sử hội thoại:\n" + "\n".join(history_lines) + "\n\n"
    user_content += f"Câu hỏi hiện tại: {query}"

    model = cfg.get("input_refine_model", "openai/orchestrator-planner")
    messages_payload = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",   "content": user_content},
    ]
    try:
        response = await llm_call.acompletion(
            "llm:input_refine", litellm.acompletion,
            model=model,
            api_base=_LITELLM_BASE,
            api_key=_LITELLM_KEY,
            messages=messages_payload,
            temperature=0,
            max_tokens=300,
            response_format={"type": "json_object"},
            timeout=cfg.get("input_refine_timeout", 20),
        )
        output = response.choices[0].message.content or ""
        result = json.loads(output or "{}")
        action = result.get("action", "keep")

        if action == "clarify":
            questions = result.get("questions", [])[:3]
            return query, questions

        refined = result.get("query", query).strip() or query
        return refined, []

    except Exception as e:
        logger.warning("input_refine | LLM failed, using original query: %s", e)
        return query, []


async def input_refine_node(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    """LangGraph node — rewrite hoặc hỏi clarification trong 1 LLM call.

    Đua với guardrail_task (bắn nền từ routers/stream.py ngay từ t=0, xem
    graph.py) bằng asyncio.wait — nếu guardrail xong TRƯỚC và báo bị chặn thì
    HUỶ LUÔN LLM call của input_refine (dù đang chạy dở), kết thúc graph ngay
    tại đây, không cần đợi input_refine viết lại xong rồi mới biết bị chặn.
    Trước đây guardrail chỉ được check ở gate cuối (single_agent_node/
    execute_node) — nghĩa là dù guardrail xong sau 1ms (hard-block regex),
    vẫn phải đợi hết input_refine (1 LLM call ~6s, không liên quan gì tới
    guardrail) mới biết kết quả.

    Nếu input_refine xong trước, hoặc guardrail cho pass, hoặc guardrail chưa
    xong khi input_refine đã xong → chạy tiếp bình thường như cũ, guardrail
    vẫn được check lại ở gate cuối để không bỏ sót case guardrail xong SAU."""
    if state.get("clarification_response"):
        merged = merge_clarification(state["query"], state["clarification_response"])
        return {"rewritten_query": merged, "clarification_needed": False}

    query = state.get("query", "")
    guardrail_task = (config.get("configurable") or {}).get("guardrail_task")

    if guardrail_task:
        llm_task = asyncio.create_task(
            run_input_refine(query, state.get("messages", []), state.get("cfg", {}))
        )
        await asyncio.wait({llm_task, guardrail_task}, return_when=asyncio.FIRST_COMPLETED)

        if guardrail_task.done():
            triggered, reason = guardrail_task.result()
            if triggered:
                llm_task.cancel()
                refusal = reason or "Xin lỗi, tôi không thể trả lời câu hỏi này."
                await adispatch_custom_event("token", refusal)
                return {
                    "final_response": refusal,
                    "success": False,
                    "guardrail_triggered": True,
                    "guardrail_reason": reason,
                }

        refined, questions = llm_task.result() if llm_task.done() else await llm_task
    else:
        refined, questions = await run_input_refine(
            query, state.get("messages", []), state.get("cfg", {})
        )

    if questions:
        logger.info("input_refine | clarification needed, questions=%d", len(questions))
        interrupt({"clarification_questions": questions})

    return {
        "rewritten_query": refined,
        "clarification_needed": bool(questions),
        "clarification_questions": questions,
    }
