"""Shared LLM-based guardrail check — used by both orchestrator (input +
multi-agent synthesis output) and workflow_runtime (single-agent output).

Pure LLM-calling primitive: does not swallow errors (raises GuardrailCheckError
instead) so each call site can decide its own fail-open/fail-closed policy —
orchestrator's input check is fail-open, output checks (both services) are
fail-closed, per design.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

import litellm

_LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://localhost:8500")
_LITELLM_KEY = os.environ.get("LITELLM_API_KEY", "sk-dev")
_MAX_CHECK_CHARS = 2000

_PROMPT_PATH = Path(__file__).parent / "prompts" / "guardrail_system.txt"
_GUARDRAIL_SYSTEM = _PROMPT_PATH.read_text(encoding="utf-8")


class GuardrailCheckError(Exception):
    """Raised when the guardrail LLM call itself fails (timeout, bad JSON, etc).
    Callers decide fail-open vs fail-closed — this module never guesses."""


async def llm_guardrail_check(
    text: str,
    cfg: dict[str, Any],
    check_type: Literal["input", "output"] = "input",
) -> tuple[bool, str]:
    """Trả về (triggered, reason). Raise GuardrailCheckError nếu lệnh gọi LLM
    thất bại (timeout, JSON không hợp lệ...) — không tự fail-open/closed."""
    allowed_topics = cfg.get("guardrail_allowed_topics", []) if check_type == "input" else []
    topics_block = ""
    if allowed_topics:
        topics_block = "\n\nCHỦ ĐỀ ĐƯỢC PHÉP:\n" + "\n".join(f"- {t}" for t in allowed_topics)

    model = cfg.get("guardrail_model", "openai/guardrail")
    label = "CONTENT" if check_type == "input" else "CÂU TRẢ LỜI CẦN KIỂM TRA"
    messages = [
        {"role": "system", "content": _GUARDRAIL_SYSTEM + topics_block},
        {"role": "user", "content": f"CHECK_TYPE: {check_type}\n{label}: {text[:_MAX_CHECK_CHARS]}"},
    ]

    try:
        response = await litellm.acompletion(
            model=model,
            api_base=_LITELLM_BASE,
            api_key=_LITELLM_KEY,
            messages=messages,
            temperature=0,
            max_tokens=cfg.get("guardrail_max_tokens", 64),
            response_format={"type": "json_object"},
            timeout=cfg.get("guardrail_timeout", 15),
            # Một blip mạng không nên hạ cấp cả lớp check — call sites coi
            # GuardrailCheckError là "lớp này im lặng" và phải xử lý theo
            # chính sách fail-open/fail-closed của mình, đắt hơn nhiều so
            # với một lần thử lại.
            num_retries=cfg.get("guardrail_num_retries", 1),
        )
        output = response.choices[0].message.content or ""
        result = json.loads(output or "{}")
    except Exception as e:
        raise GuardrailCheckError(str(e)) from e

    triggered = bool(result.get("triggered", False))
    reason = result.get("reason", "Yêu cầu không được phép.") if triggered else ""
    return triggered, reason
