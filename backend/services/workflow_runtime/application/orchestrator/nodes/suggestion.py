import json
import logging
import os
from typing import Any

import litellm

from services.workflow_runtime.application.runtime_services import llm_call

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:8500")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY", "sk-dev")

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")
_SYSTEM = open(os.path.join(_PROMPTS_DIR, "suggestion_system.txt"), encoding="utf-8").read()


async def run_suggestion(query: str, messages: list[dict[str, Any]], final_response: str, cfg: dict[str, Any]) -> list[str]:
    """
    Gen follow-up suggestions từ query + response.
    Chạy sau khi có final_response để suggestions liên quan hơn.
    Trả về list tối đa 3 suggestions.
    """
    history_lines = []
    for m in messages[-4:]:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()[:200]
        if role in ("user", "assistant"):
            history_lines.append(f"{role.capitalize()}: {content}")

    parts = []
    if history_lines:
        parts.append("Lịch sử hội thoại:\n" + "\n".join(history_lines))
    parts.append(f"Câu hỏi hiện tại: {query}")
    if final_response:
        parts.append(f"Câu trả lời: {final_response[:600]}")

    model = cfg.get("suggestion_model", "openai/suggester")
    messages_payload = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",   "content": "\n\n".join(parts)},
    ]
    try:
        response = await llm_call.acompletion(
            "llm:suggestion", litellm.acompletion,
            model=model,
            api_base=_LITELLM_BASE,
            api_key=_LITELLM_KEY,
            messages=messages_payload,
            temperature=0.3,
            max_tokens=150,
            response_format={"type": "json_object"},
            # Suggestions sit on the graph's critical path before the stream
            # closes (see orchestrator/graph.py's suggestion_node) — the
            # answer has already fully streamed to the user by the time this
            # call starts, so a slow/overloaded model here shows up as dead
            # silence rather than visible progress. 150 max_tokens should
            # answer in a couple seconds on a healthy backend; bounding this
            # at 10s (was 30s) caps the worst case without changing behavior
            # on the common, fast path — a timeout still degrades to no
            # suggestions (caught below), never an error shown to the user.
            timeout=cfg.get("suggestion_timeout", 10),
        )
        output = response.choices[0].message.content or ""
        result = json.loads(output or "{}")
        topics = result.get("topics", [])
        if isinstance(topics, list):
            return [str(t).strip() for t in topics[:3] if t]
    except Exception as e:
        logger.warning("suggestion | failed: %s", e)

    return []
