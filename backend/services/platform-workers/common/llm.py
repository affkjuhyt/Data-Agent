"""Chat completions through LiteLLM (``/v1/chat/completions``).

Two callers, one shape: the memory worker's insight extraction and the
schema crawler's table description both send a single user message with
``response_format=json_object`` and parse the reply. Neither retries at the
HTTP level — the memory worker retries the whole event (with a DLQ), and the
crawler picks a failed table up on the next tick — so this module does not
either; a transport error propagates to the caller's own recovery path.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from common.settings import LiteLLMSettings

log = logging.getLogger("platform-workers.llm")


def chat(
    messages: list[dict[str, Any]],
    model: str,
    *,
    settings: LiteLLMSettings | None = None,
    max_tokens: int = 400,
    temperature: float = 0.0,
    response_format: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> str:
    """One completion; returns the assistant message content, stripped."""
    settings = settings or LiteLLMSettings()
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format is not None:
        body["response_format"] = response_format
    r = httpx.post(
        f"{settings.base_url.rstrip('/')}/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.api_key}"},
        json=body,
        timeout=timeout,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return str(content or "").strip()


def chat_json(
    messages: list[dict[str, Any]],
    model: str,
    *,
    settings: LiteLLMSettings | None = None,
    max_tokens: int = 400,
    temperature: float = 0.0,
    timeout: float = 30.0,
) -> Any:
    """``chat`` with ``response_format=json_object``, parsed.

    Returns ``None`` when the model replied with something that is not JSON —
    callers decide what an unusable answer means for them (skip the turn,
    describe the table as empty), so no exception is raised here.
    """
    content = chat(
        messages, model,
        settings=settings, max_tokens=max_tokens, temperature=temperature,
        response_format={"type": "json_object"}, timeout=timeout,
    )
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        log.warning("llm | non-JSON response from model=%s: %r", model, content[:200])
        return None
