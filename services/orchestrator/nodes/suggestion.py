from typing import Any
import json
import logging
import os

import litellm

from observability import get_langfuse_client

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
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
        response = await litellm.acompletion(
            model=model,
            api_base=_LITELLM_BASE,
            api_key=_LITELLM_KEY,
            messages=messages_payload,
            temperature=0.3,
            max_tokens=150,
            response_format={"type": "json_object"},
            timeout=cfg.get("suggestion_timeout", 30),
        )
        output = response.choices[0].message.content or ""
        lf = get_langfuse_client()
        if lf:
            usage = response.usage or {}
            lf.update_current_generation(
                model=model,
                input=messages_payload,
                output=output,
                usage_details={
                    "input": getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0),
                    "output": getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0),
                },
            )
        result = json.loads(output or "{}")
        topics = result.get("topics", [])
        if isinstance(topics, list):
            return [str(t).strip() for t in topics[:3] if t]
    except Exception as e:
        logger.warning("suggestion | failed: %s", e)

    return []
