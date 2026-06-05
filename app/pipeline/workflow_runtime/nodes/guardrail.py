import json
import os
import time
import logging
import functools
import litellm

import unicodedata

try:
    import regex as re_lib
    _REGEX_TIMEOUT = 0.1
except ImportError:
    import re as re_lib
    _REGEX_TIMEOUT = None

from app.pipeline.workflow_runtime.state import AgentState

logger = logging.getLogger(__name__)

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:4000")
_LITELLM_KEY  = os.environ.get("LITELLM_API_KEY", "sk-dev")
_MAX_CHECK_CHARS = 2000

_PROMPTS_DIR    = os.path.join(os.path.dirname(__file__), "..", "prompts")
_GUARDRAIL_SYSTEM = open(os.path.join(_PROMPTS_DIR, "guardrail_system.txt")).read()


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re_lib.sub(r"\s+", " ", text).strip()
    return text.lower()


@functools.lru_cache(maxsize=64)
def _compile_patterns(patterns_tuple: tuple) -> list[tuple[str, str, object]]:
    compiled = []
    for pid, p in patterns_tuple:
        try:
            compiled.append((pid, p, re_lib.compile(p, re_lib.IGNORECASE)))
        except re_lib.error as e:
            logger.warning("guardrail | invalid pattern skipped id=%s pattern=%r error=%s", pid, p, e)
    return compiled


def _regex_check(text: str, patterns: list) -> tuple[bool, str, dict | None]:
    text = _normalize(text)[:_MAX_CHECK_CHARS]
    patterns_tuple = tuple((item["id"], item["pattern"]) for item in patterns)
    compiled = _compile_patterns(patterns_tuple)

    for pid, p_str, compiled_re in compiled:
        try:
            match = (
                compiled_re.search(text, timeout=_REGEX_TIMEOUT)
                if _REGEX_TIMEOUT else compiled_re.search(text)
            )
            if match:
                return True, "Yêu cầu không được phép.", {"id": pid, "pattern": p_str}
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
        reason = result.get("reason", "") or "Yêu cầu không được phép."
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

    triggered, message, matched = _regex_check(
        state["query"], cfg.get("guardrail_input_patterns", [])
    )
    method = "regex"

    if not triggered and cfg.get("guardrail_llm_enabled", True):
        triggered, message, matched = _llm_check(state["query"], "input", cfg)
        method = "llm"

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


def guardrail_output_node(state: AgentState) -> dict:
    if state.get("guardrail_triggered"):
        return {}

    cfg = state.get("config", {})

    triggered, message, matched = _regex_check(
        state.get("response", ""), cfg.get("guardrail_output_patterns", [])
    )
    method = "regex"

    if not triggered and cfg.get("guardrail_llm_enabled", True):
        triggered, message, matched = _llm_check(state.get("response", ""), "output", cfg)
        method = "llm"

    if triggered:
        logger.info(
            "guardrail_output | triggered method=%s pattern_id=%r reason=%r",
            method, matched["id"] if matched else None, message,
        )
        return {
            "guardrail_triggered": True,
            "guardrail_message": message,
            "guardrail_stage": "output",
            "guardrail_reason": _build_reason(method, matched, message),
            "response": message,
        }
    return {}
