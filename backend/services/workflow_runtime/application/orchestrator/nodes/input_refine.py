from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any

import litellm
from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from services.workflow_runtime.application.orchestrator.state import OrchestratorState
from services.workflow_runtime.application.runtime_services import llm_call

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


_MAX_QUESTIONS = 3
_MAX_OPTIONS = 4
_MAX_OPTION_CHARS = 80
# The card always offers its own "Other"; one from the model would be a second.
_OTHER_OPTIONS = frozenset({"other", "khác", "khac", "khác...", "other..."})


def normalize_questions(raw: Any) -> list[dict[str, Any]]:
    """Clarification questions as `{text, options}`, whatever shape the model used.

    The prompt asks for options per question so the card can offer choices
    (plus its own "Other"). A model that still returns plain strings gives
    questions without options — a free-text card, as before — rather than an
    error. Options are model output shown as buttons: trimmed, de-duplicated,
    capped, and never an "Other" of their own.
    """
    questions: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            text, options = item, []
        elif isinstance(item, dict):
            text, options = item.get("text") or item.get("question") or "", item.get("options") or []
        else:
            continue
        text = str(text).strip()
        if not text:
            continue
        cleaned: list[str] = []
        for option in options if isinstance(options, list) else []:
            label = str(option).strip()
            if (
                label and len(label) <= _MAX_OPTION_CHARS
                and label.casefold() not in _OTHER_OPTIONS
                and label not in cleaned
            ):
                cleaned.append(label)
        questions.append({"text": text, "options": cleaned[:_MAX_OPTIONS]})
    return questions[:_MAX_QUESTIONS]


def clarification_payload(questions: Any) -> dict[str, Any]:
    """What an interrupt carries: the texts (the long-standing field every
    client reads) and the structured questions with their options."""
    details = normalize_questions(questions)
    return {
        "clarification_questions": [q["text"] for q in details],
        "clarification_details": details,
    }


# Enough turns to reach the entity a pronoun points back to ("ông" two turns
# after the name, with a clarification in between), and enough of each turn
# to keep it: 300 characters cut assistant answers before the part that named
# it. User turns are short and kept nearly whole.
_HISTORY_MESSAGES = 8
_HISTORY_CHARS = {"user": 500, "assistant": 600}

# Marks a question that already carries the user's answer to a clarification.
# The system prompt names this exact line (input_refine_system.txt).
ANSWERED_MARKER = "NGƯỜI DÙNG VỪA TRẢ LỜI CÂU HỎI LÀM RÕ"


def history_lines(messages: list[dict[str, Any]]) -> list[str]:
    """The conversation as the model sees it, most recent turns last."""
    lines: list[str] = []
    for m in messages[-_HISTORY_MESSAGES:]:
        role = m.get("role", "")
        limit = _HISTORY_CHARS.get(role)
        if limit is None:
            continue
        content = " ".join((m.get("content") or "").split())[:limit]
        if content:
            lines.append(f"{role.capitalize()}: {content}")
    return lines


async def run_input_refine(
    query: str,
    messages: list[dict[str, Any]],
    cfg: dict[str, Any],
    *,
    allow_clarify: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Standalone function cho SSE endpoint.
    Returns:
        (refined_query, clarification_questions)
        — nếu clarification_questions rỗng → tiếp tục với refined_query
        — nếu không rỗng → cần hỏi user; mỗi câu là {text, options}

    `allow_clarify=False` is rewrite-only, for a question that already merges
    the user's answer to a clarification: it may still say "ông" or "nó", and
    the agent sees only this question — never the conversation — so it must
    come out standing on its own. Asking again there is refused.
    """
    lines = history_lines(messages)

    user_content = _today_line() + "\n\n"
    if lines:
        user_content += "Lịch sử hội thoại:\n" + "\n".join(lines) + "\n\n"
    if not allow_clarify:
        user_content += f"{ANSWERED_MARKER}.\n\n"
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
            if not allow_clarify:
                # The user just answered; asking again is the loop this mode
                # exists to end. Keep the merged question as it is.
                logger.info("input_refine | clarify refused after an answer — keeping the merged question")
                return query, []
            questions = normalize_questions(result.get("questions"))
            if questions:
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
    messages, cfg = state.get("messages", []), state.get("cfg", {})
    if state.get("clarification_response"):
        # The answer is merged into the question that was asked, which may
        # still say "ông". The planner and agents below see only this question,
        # so it is rewritten against the history first — never asked again.
        merged = merge_clarification(state["query"], state["clarification_response"])
        refined, _ = await run_input_refine(merged, messages, cfg, allow_clarify=False)
        return {"rewritten_query": refined, "clarification_needed": False}

    query = state.get("query", "")
    guardrail_task = (config.get("configurable") or {}).get("guardrail_task")
    # Picks from a clarification card are an answer too: rewrite, do not ask.
    allow_clarify = not state.get("clarification_answers")

    if guardrail_task:
        llm_task = asyncio.create_task(
            run_input_refine(query, messages, cfg, allow_clarify=allow_clarify)
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
        refined, questions = await run_input_refine(query, messages, cfg, allow_clarify=allow_clarify)

    if questions:
        logger.info("input_refine | clarification needed, questions=%d", len(questions))
        interrupt(clarification_payload(questions))

    return {
        "rewritten_query": refined,
        "clarification_needed": bool(questions),
        "clarification_questions": clarification_payload(questions)["clarification_questions"],
    }
