import os
import time
import logging
import litellm

from state import AgentState

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY",  "sk-dev")

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "prompts")
_SYSTEM = open(os.path.join(_PROMPTS_DIR, "rewrite_system.txt")).read()

# Query quá ngắn mới cần rewrite; query dài đã rõ ràng thì skip
_MIN_CHARS_TO_SKIP = 60


def _build_history_block(messages: list[dict]) -> str:
    lines = []
    for m in messages[-6:]:  # chỉ lấy 3 cặp gần nhất
        role = m.get("role", "")
        content = (m.get("content") or "").strip()
        if role == "user":
            lines.append(f"User: {content}")
        elif role == "assistant":
            lines.append(f"Assistant: {content[:300]}")  # cắt bớt assistant để tiết kiệm token
    return "\n".join(lines)


def rewrite_node(state: AgentState) -> dict:
    t0 = time.monotonic()
    query = state["query"].strip()
    messages = state.get("messages", [])

    # Skip: không có lịch sử → câu đầu tiên, không cần rewrite
    # Skip: query đã đủ dài → có thể đã rõ ràng
    if not messages or len(query) >= _MIN_CHARS_TO_SKIP:
        logger.info("rewrite | skipped (no_history=%s, len=%d)", not messages, len(query))
        return {"original_query": query}

    history_block = _build_history_block(messages)
    user_content = f"Conversation history:\n{history_block}\n\nLatest message: {query}"

    try:
        cfg = state.get("config", {})
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

        # Sanity check: nếu model trả về rỗng hoặc quá dài → dùng query gốc
        if not rewritten or len(rewritten) > 500:
            logger.warning("rewrite | bad output, fallback to original. raw=%r", rewritten)
            return {"original_query": query}

        latency_ms = int((time.monotonic() - t0) * 1000)
        if rewritten != query:
            logger.info("rewrite | latency=%dms original=%r rewritten=%r", latency_ms, query, rewritten)
        else:
            logger.info("rewrite | latency=%dms unchanged", latency_ms)

        return {"query": rewritten, "original_query": query}

    except Exception as e:
        logger.warning("rewrite | failed, fallback to original. error=%s", e)
        return {"original_query": query}
