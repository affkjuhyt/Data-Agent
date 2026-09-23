"""One streamed completion, timed.

Deliberately raw HTTP instead of litellm. The number this harness reports has
to belong to the server: a client library that retries, buffers, or re-chunks a
stream moves time-to-first-token without saying so, and a benchmark that cannot
tell server latency from client latency cannot justify a serving change.

`runtime_services/llm_call.py` measures the same three quantities in production
*through* litellm. When the two disagree the difference is the client and proxy
overhead, which is itself a finding — so both are kept rather than unified.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class Timing:
    """What one request cost. `None` for ttft_ms means no content token ever
    arrived — an error, or a model that emitted only a usage chunk."""

    ttft_ms: float | None
    total_ms: float
    prompt_tokens: int
    completion_tokens: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def decode_ms(self) -> float | None:
        if self.ttft_ms is None:
            return None
        return max(self.total_ms - self.ttft_ms, 0.0)

    @property
    def decode_tps(self) -> float | None:
        """Tokens per second *after* the first one.

        The first token is the tail of prefill, not decode — counting it would
        make a long prompt look like slow decoding instead of slow prefill,
        which are fixed by opposite levers (context reduction vs. quantization).
        """
        decode = self.decode_ms
        if decode is None or decode <= 0 or self.completion_tokens <= 1:
            return None
        return (self.completion_tokens - 1) * 1000.0 / decode


def _usage(chunk: dict[str, Any]) -> tuple[int, int] | None:
    usage = chunk.get("usage")
    if not isinstance(usage, dict):
        return None
    return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


async def complete(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    extra_body: dict[str, Any] | None = None,
) -> Timing:
    """POST one streamed chat completion and time it.

    Streaming is not optional here even though the production planner/judge
    calls are awaited whole: TTFT is only observable on a stream, and TTFT is
    the half of the latency that context work fixes. The totals are unaffected —
    vLLM decodes the same way either way.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
        **(extra_body or {}),
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    started = time.monotonic()
    first_token: float | None = None
    counted = 0
    reported: tuple[int, int] | None = None
    try:
        async with client.stream(
            "POST", f"{base_url}/chat/completions", json=payload, headers=headers,
        ) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")
                return Timing(None, (time.monotonic() - started) * 1000, 0, 0,
                              error=f"HTTP {response.status_code}: {body[:200]}")
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                usage = _usage(chunk)
                if usage is not None:
                    reported = usage
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                text = (choices[0].get("delta") or {}).get("content")
                if not text:
                    continue
                counted += 1
                if first_token is None:
                    first_token = time.monotonic()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return Timing(None, (time.monotonic() - started) * 1000, 0, 0,
                      error=f"{type(exc).__name__}: {exc}"[:200])

    total_ms = (time.monotonic() - started) * 1000
    # Falling back to the chunk count keeps a run usable against a server that
    # does not honour include_usage; it undercounts (one chunk can carry
    # several tokens), so the report says which source it used.
    prompt_tokens, completion_tokens = reported or (0, counted)
    return Timing(
        ttft_ms=None if first_token is None else (first_token - started) * 1000,
        total_ms=total_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens or counted,
    )
