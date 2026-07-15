import functools
import logging
import time
import unicodedata

from libs.shared.guardrail_check import GuardrailCheckError, llm_guardrail_check

try:
    import regex as re_lib
    _REGEX_TIMEOUT = 0.1
except ImportError:
    import re as re_lib
    _REGEX_TIMEOUT = None

logger = logging.getLogger(__name__)

_MAX_CHECK_CHARS = 2000
_HISTORY_TURNS = 6  # số lượt hội thoại gần nhất đưa vào ngữ cảnh check input


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re_lib.sub(r"\s+", " ", text).strip()
    return text.lower()


@functools.lru_cache(maxsize=64)
def _compile_patterns(patterns_tuple: tuple) -> list:
    compiled = []
    for pid, p, confidence in patterns_tuple:
        try:
            compiled.append((pid, p, confidence, re_lib.compile(p, re_lib.IGNORECASE)))
        except re_lib.error as e:
            logger.warning("guardrail | invalid pattern skipped id=%s error=%s", pid, e)
    return compiled


def _regex_check(text: str, patterns: list) -> tuple[bool, str, dict | None]:
    text = _normalize(text)[:_MAX_CHECK_CHARS]
    patterns_tuple = tuple((item["id"], item["pattern"], item.get("confidence", "low")) for item in patterns)
    for pid, p_str, confidence, compiled_re in _compile_patterns(patterns_tuple):
        try:
            match = (
                compiled_re.search(text, timeout=_REGEX_TIMEOUT)
                if _REGEX_TIMEOUT else compiled_re.search(text)
            )
            if match:
                return True, "Yêu cầu không được phép.", {"id": pid, "pattern": p_str, "confidence": confidence}
        except (TimeoutError, re_lib.error):
            pass
    return False, "", None


def _build_input_context(query: str, messages: list[dict] | None) -> str:
    """Ghép lịch sử hội thoại gần nhất + câu hỏi hiện tại làm ngữ cảnh cho
    LLM check — trước đây chỉ check mỗi query trần, thiếu ngữ cảnh hội thoại."""
    if not messages:
        return query
    recent = messages[-_HISTORY_TURNS:]
    lines = [f"{m.get('role', 'user')}: {m.get('content', '')}" for m in recent]
    lines.append(f"user: {query}")
    return "\n".join(lines)


async def run_guardrail(query: str, cfg: dict, messages: list[dict] | None = None) -> tuple[bool, str]:
    """
    Trả về (triggered, reason).
    cfg có thể truyền vào guardrail_input_patterns, guardrail_llm_enabled, guardrail_model, v.v.
    messages: lịch sử hội thoại gần nhất, dùng làm ngữ cảnh cho lớp LLM check
    (lớp regex chỉ check `query` — mẫu câu jailbreak/injection nhắm vào câu
    hỏi hiện tại, không cần lịch sử).
    """
    t0 = time.monotonic()

    regex_triggered, regex_message, regex_matched = _regex_check(
        query, cfg.get("guardrail_input_patterns", [])
    )

    if regex_triggered and regex_matched and regex_matched.get("confidence") == "high":
        logger.info("guardrail | latency=%dms triggered=True method=regex-hard", int((time.monotonic() - t0) * 1000))
        return True, regex_message

    triggered, message = False, ""
    if cfg.get("guardrail_llm_enabled", True):
        context = _build_input_context(query, messages)
        try:
            triggered, message = await llm_guardrail_check(context, cfg, check_type="input")
        except GuardrailCheckError as e:
            logger.warning("guardrail | LLM check failed, defaulting to pass (fail-open). error=%s", e)
            triggered, message = False, ""
    elif regex_triggered:
        triggered, message = regex_triggered, regex_message

    logger.info("guardrail | latency=%dms triggered=%s", int((time.monotonic() - t0) * 1000), triggered)
    return triggered, message
