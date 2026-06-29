import logging
import os
import time

import litellm

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY", "sk-dev")
_MIN_CHARS_TO_SKIP = 60

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")
_SYSTEM = open(os.path.join(_PROMPTS_DIR, "rewrite_system.txt"), encoding="utf-8").read()


def _build_history_block(messages: list[dict]) -> str:
    lines = []
    for m in messages[-6:]:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        if role == "user":
            lines.append(f"User: {content}")
        elif role == "assistant":
            lines.append(f"Assistant: {content[:300]}")
    return "\n".join(lines)


async def run_rewrite(query: str, messages: list[dict], cfg: dict) -> str:
    """
    Trả về query đã được rewrite (hoặc query gốc nếu skip/fail).
    """
    query = query.strip()

    if not messages or len(query) >= _MIN_CHARS_TO_SKIP:
        return query

    history_block = _build_history_block(messages)
    user_content = f"Conversation history:\n{history_block}\n\nLatest message: {query}"

    t0 = time.monotonic()
    try:
        response = litellm.completion(
            model=cfg.get("rewriter_model", "rewriter"),
            api_base=_LITELLM_BASE,
            api_key=_LITELLM_KEY,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_content},
            ],
            temperature=0,
            max_tokens=150,
            timeout=cfg.get("rewriter_timeout", 8),
        )
        rewritten = (response.choices[0].message.content or "").strip()
        if not rewritten or len(rewritten) > 500:
            return query
        logger.info("rewrite | latency=%dms", int((time.monotonic() - t0) * 1000))
        return rewritten
    except Exception as e:
        logger.warning("rewrite | failed, fallback to original. error=%s", e)
        return query
