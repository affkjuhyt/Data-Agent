"""The model gateway as worker-agent sees it (tab 5 G1.1, ADR-38/41): one chat call through LiteLLM.

LiteLLM serves an OpenAI-compatible API (LITELLM_BASE_URL, LITELLM_API_KEY) and maps the role
aliases to the on-prem models: `planner` is the fast MoE (understanding, routing), `responder` the
main dense model (writing). The worker never names a provider, so the gateway's routing and its
frontier rules (never for SQL, the Understander, or tainted runs) stay in one place.

Every call asks for JSON and is short: what comes back is validated by the caller against the
Data Space's own vocabulary or figures, never trusted. Without a gateway configured the port is
absent and the worker keeps to its deterministic path.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class Reply:
    content: dict[str, Any]
    model: str  # the alias the gateway answered for
    tokens: int
    latency_ms: int


class ModelUnavailable(RuntimeError):
    """The gateway did not answer usefully; the caller falls back to its deterministic path."""


class Gateway(Protocol):
    async def json_chat(
        self,
        role: str,
        system: str,
        user: str,
        *,
        max_tokens: int = 800,
        temperature: float = 0.0,
    ) -> Reply: ...


class LiteLLMGateway:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 20.0) -> None:
        self._url = base_url.rstrip("/") + "/v1/chat/completions"
        self._key = api_key
        self._timeout = timeout

    async def json_chat(
        self,
        role: str,
        system: str,
        user: str,
        *,
        max_tokens: int = 800,
        temperature: float = 0.0,
    ) -> Reply:
        """`temperature` above 0 only where several samples are wanted (N-version SQL)."""
        started = time.monotonic()
        body = {
            "model": role,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.post(
                    self._url, json=body, headers={"Authorization": f"Bearer {self._key}"}
                )
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"gateway unreachable: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise ModelUnavailable(f"gateway answered {r.status_code}")
        data = r.json()
        try:
            content = json.loads(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, ValueError) as exc:
            raise ModelUnavailable("the model did not answer JSON") from exc
        if not isinstance(content, dict):
            raise ModelUnavailable("the model did not answer a JSON object")
        return Reply(
            content=content,
            model=str(data.get("model") or role),
            tokens=int((data.get("usage") or {}).get("total_tokens") or 0),
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def gateway_from_env() -> Gateway | None:
    url = os.environ.get("LITELLM_BASE_URL")
    if not url:
        return None
    return LiteLLMGateway(url, os.environ.get("LITELLM_API_KEY", ""))
