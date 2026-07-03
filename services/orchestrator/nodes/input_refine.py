from __future__ import annotations

import json
import logging
import os

import litellm
from langgraph.types import interrupt

from state import OrchestratorState

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY", "sk-dev")

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")
_SYSTEM = open(os.path.join(_PROMPTS_DIR, "input_refine_system.txt"), encoding="utf-8").read()


async def run_input_refine(
    query: str,
    messages: list[dict],
    cfg: dict,
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

    user_content = ""
    if history_lines:
        user_content += "Lịch sử hội thoại:\n" + "\n".join(history_lines) + "\n\n"
    user_content += f"Câu hỏi hiện tại: {query}"

    try:
        response = await litellm.acompletion(
            model=cfg.get("input_refine_model", "openai/orchestrator-planner"),
            api_base=_LITELLM_BASE,
            api_key=_LITELLM_KEY,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_content},
            ],
            temperature=0,
            max_tokens=300,
            response_format={"type": "json_object"},
            timeout=cfg.get("input_refine_timeout", 8),
        )
        result = json.loads(response.choices[0].message.content or "{}")
        action = result.get("action", "keep")

        if action == "clarify":
            questions = result.get("questions", [])[:3]
            return query, questions

        refined = result.get("query", query).strip() or query
        return refined, []

    except Exception as e:
        logger.warning("input_refine | LLM failed, using original query: %s", e)
        return query, []


async def input_refine_node(state: OrchestratorState) -> dict:
    """LangGraph node — rewrite hoặc hỏi clarification trong 1 LLM call."""

    if state.get("clarification_response"):
        merged = f"{state['query']}\n\nThông tin bổ sung: {state['clarification_response']}"
        return {"rewritten_query": merged, "clarification_needed": False}

    query = state.get("query", "")
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
