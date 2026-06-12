import os
import json
import logging
import litellm

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY",  "sk-dev")

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "prompts")
_SYSTEM = open(os.path.join(_PROMPTS_DIR, "suggestion_system.txt")).read()


async def generate_suggestions(query: str, messages: list[dict], cfg: dict) -> list[str]:
    history_lines = []
    for m in messages[-4:]:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()[:200]
        if role in ("user", "assistant"):
            history_lines.append(f"{role.capitalize()}: {content}")

    history_block = "\n".join(history_lines)
    user_content = f"Lịch sử hội thoại:\n{history_block}\n\nCâu hỏi hiện tại: {query}" if history_block else f"Câu hỏi: {query}"

    try:
        response = await litellm.acompletion(
            model=cfg.get("suggestion_model", "suggester"),
            api_base=_LITELLM_BASE,
            api_key=_LITELLM_KEY,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_content},
            ],
            temperature=0.3,
            max_tokens=120,
            response_format={"type": "json_object"},
            timeout=cfg.get("suggestion_timeout", 8),
        )
        raw = response.choices[0].message.content or ""
        result = json.loads(raw)
        topics = result.get("topics", [])
        if isinstance(topics, list):
            return [str(t).strip() for t in topics[:3] if t]
    except Exception as e:
        logger.warning("suggestion | failed: %s", e)

    return []
