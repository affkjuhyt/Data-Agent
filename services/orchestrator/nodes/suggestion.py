import json
import logging
import os

import litellm

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY", "sk-dev")

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")
_SYSTEM = open(os.path.join(_PROMPTS_DIR, "suggestion_system.txt"), encoding="utf-8").read()


async def run_suggestion(query: str, messages: list[dict], final_response: str, cfg: dict) -> list[str]:
    """
    Gen follow-up suggestions từ final synthesized response.
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
        parts.append(f"Câu trả lời: {final_response[:500]}")

    try:
        response = await litellm.acompletion(
            model=cfg.get("suggestion_model", "openai/suggester"),
            api_base=_LITELLM_BASE,
            api_key=_LITELLM_KEY,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": "\n\n".join(parts)},
            ],
            temperature=0.3,
            max_tokens=150,
            response_format={"type": "json_object"},
            timeout=cfg.get("suggestion_timeout", 8),
        )
        result = json.loads(response.choices[0].message.content or "{}")
        topics = result.get("topics", [])
        if isinstance(topics, list):
            return [str(t).strip() for t in topics[:3] if t]
    except Exception as e:
        logger.warning("suggestion | failed: %s", e)

    return []
