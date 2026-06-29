import functools
import json
import logging
import os
import time
import unicodedata

import litellm

try:
    import regex as re_lib
    _REGEX_TIMEOUT = 0.1
except ImportError:
    import re as re_lib
    _REGEX_TIMEOUT = None

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY", "sk-dev")
_MAX_CHECK_CHARS = 2000

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "prompts")
_GUARDRAIL_SYSTEM = open(os.path.join(_PROMPTS_DIR, "guardrail_system.txt"), encoding="utf-8").read()


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


def _llm_check(text: str, cfg: dict) -> tuple[bool, str]:
    allowed_topics = cfg.get("guardrail_allowed_topics", [])
    topics_block = ""
    if allowed_topics:
        topics_block = "\n\nCHỦ ĐỀ ĐƯỢC PHÉP:\n" + "\n".join(f"- {t}" for t in allowed_topics)

    try:
        response = litellm.completion(
            model=cfg.get("guardrail_model", "guardrail"),
            api_base=_LITELLM_BASE,
            api_key=_LITELLM_KEY,
            messages=[
                {"role": "system", "content": _GUARDRAIL_SYSTEM + topics_block},
                {"role": "user", "content": f"CHECK_TYPE: input\nCONTENT: {text[:_MAX_CHECK_CHARS]}"},
            ],
            temperature=0,
            max_tokens=cfg.get("guardrail_max_tokens", 64),
            response_format={"type": "json_object"},
            timeout=cfg.get("guardrail_timeout", 5),
        )
        result = json.loads(response.choices[0].message.content or "{}")
        triggered = bool(result.get("triggered", False))
        reason = result.get("reason", "Yêu cầu không được phép.") if triggered else ""
        return triggered, reason
    except Exception as e:
        logger.warning("guardrail | LLM check failed, defaulting to pass. error=%s", e)
        return False, ""


async def run_guardrail(query: str, cfg: dict) -> tuple[bool, str]:
    """
    Trả về (triggered, reason).
    cfg có thể truyền vào guardrail_input_patterns, guardrail_llm_enabled, guardrail_model, v.v.
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
        triggered, message = _llm_check(query, cfg)
    elif regex_triggered:
        triggered, message = regex_triggered, regex_message

    logger.info("guardrail | latency=%dms triggered=%s", int((time.monotonic() - t0) * 1000), triggered)
    return triggered, message
