import json
import os
import time
import logging
import functools
import litellm

import unicodedata

try:
    import regex as re_lib  # supports timeout param → prevents ReDoS
    _REGEX_TIMEOUT = 0.1    # seconds per pattern
except ImportError:
    import re as re_lib     # fallback, no timeout
    _REGEX_TIMEOUT = None

from state import AgentState

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY", "sk-dev")
_MAX_CHECK_CHARS = 2000  # giới hạn độ dài text trước khi check regex

_PROMPTS_DIR    = os.path.join(os.path.dirname(__file__), "..", "prompts")
_GUARDRAIL_SYSTEM = open(os.path.join(_PROMPTS_DIR, "guardrail_system.txt"), encoding="utf-8").read()


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)   # chuẩn hóa Unicode (tránh NFD bypass)
    text = re_lib.sub(r"\s+", " ", text).strip() # collapse whitespace
    return text.lower()


@functools.lru_cache(maxsize=64)
def _compile_patterns(patterns_tuple: tuple) -> list[tuple[str, str, str, object]]:
    """Compile và cache regex patterns lúc startup. patterns_tuple = ((id, pattern_str, confidence), ...)"""
    compiled = []
    for pid, p, confidence in patterns_tuple:
        try:
            compiled.append((pid, p, confidence, re_lib.compile(p, re_lib.IGNORECASE)))
        except re_lib.error as e:
            logger.warning("guardrail | invalid pattern skipped id=%s pattern=%r error=%s", pid, p, e)
    return compiled


def _regex_check(text: str, patterns: list) -> tuple[bool, str, dict | None]:
    text = _normalize(text)[:_MAX_CHECK_CHARS]
    patterns_tuple = tuple((item["id"], item["pattern"], item.get("confidence", "low")) for item in patterns)
    compiled = _compile_patterns(patterns_tuple)

    for pid, p_str, confidence, compiled_re in compiled:
        try:
            match = (
                compiled_re.search(text, timeout=_REGEX_TIMEOUT)
                if _REGEX_TIMEOUT else compiled_re.search(text)
            )
            if match:
                return True, "Yêu cầu không được phép.", {"id": pid, "pattern": p_str, "confidence": confidence}
        except TimeoutError:
            logger.warning("guardrail | regex timeout id=%s pattern=%r", pid, p_str)
        except re_lib.error as e:
            logger.warning("guardrail | regex error id=%s pattern=%r error=%s", pid, p_str, e)

    return False, "", None


def _llm_check(text: str, check_type: str, cfg: dict) -> tuple[bool, str, None]:
    allowed_topics = cfg.get("guardrail_allowed_topics", [])
    topics_block = ""
    if allowed_topics:
        topics_block = "\n\nCHỦ ĐỀ ĐƯỢC PHÉP:\n" + "\n".join(f"- {t}" for t in allowed_topics)

    system_prompt = _GUARDRAIL_SYSTEM + topics_block
    user_content = f"CHECK_TYPE: {check_type}\nCONTENT: {text[:_MAX_CHECK_CHARS]}"

    try:
        response = litellm.completion(
            model=cfg.get("guardrail_model", "guardrail"),
            api_base=_LITELLM_BASE,
            api_key=_LITELLM_KEY,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            max_tokens=cfg.get("guardrail_max_tokens", 64),
            response_format={"type": "json_object"},
            timeout=cfg.get("guardrail_timeout", 5),
        )
        raw = response.choices[0].message.content or ""
        result = json.loads(raw)
        triggered = bool(result.get("triggered", False))
        reason = result.get("reason", "")
        if triggered and not reason:
            reason = "Yêu cầu không được phép."
        return triggered, reason, None
    except Exception as e:
        logger.warning("guardrail | LLM check failed, defaulting to pass. error=%s", e)
        return False, "", None


def _build_reason(method: str, matched: dict | None, message: str) -> str:
    if matched:
        return f"[{method}] pattern_id={matched['id']!r} pattern={matched['pattern']!r} reason={message}"
    return f"[{method}] reason={message}"


def guardrail_input_node(state: AgentState) -> dict:
    t0 = time.monotonic()
    cfg = state.get("config", {})

    regex_triggered, regex_message, regex_matched = _regex_check(
        state["query"], cfg.get("guardrail_input_patterns", [])
    )

    # HIGH confidence → hard-block ngay, không cần LLM
    if regex_triggered and regex_matched and regex_matched.get("confidence") == "high":
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "guardrail_input | latency=%dms triggered=True method=regex-hard pattern_id=%r reason=%r",
            latency_ms, regex_matched["id"], regex_message,
        )
        return {
            "guardrail_triggered": True,
            "guardrail_message": regex_message,
            "guardrail_stage": "input",
            "guardrail_reason": _build_reason("regex-hard", regex_matched, regex_message),
        }

    # LOW confidence hoặc không match → LLM quyết định độc lập
    triggered, message, matched = False, "", None
    method = "none"

    if cfg.get("guardrail_llm_enabled", True):
        triggered, message, matched = _llm_check(state["query"], "input", cfg)
        method = "llm"
    elif regex_triggered:
        # LLM disabled → regex làm fallback
        triggered, message, matched = regex_triggered, regex_message, regex_matched
        method = "regex-fallback"

    latency_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "guardrail_input | latency=%dms triggered=%s method=%s pattern_id=%r reason=%r",
        latency_ms, triggered, method,
        matched["id"] if matched else None, message,
    )

    return {
        "guardrail_triggered": triggered,
        "guardrail_message": message,
        "guardrail_stage": "input" if triggered else None,
        "guardrail_reason": _build_reason(method, matched, message) if triggered else None,
    }
