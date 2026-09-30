"""LLM calls recorded as Langfuse generations, with time to first token.

Every call site used to run a plain `litellm.acompletion` and then
`update_current_generation(...)`. No generation was ever current at that
point (the LangChain handler opens its node spans without making them the
OTEL current span), so those updates recorded nothing: the trace had no
model, no token usage and no way to tell prefill time from decode time.

`acompletion` opens the generation itself and streams the call internally,
so the moment the first token arrives is known. Langfuse derives
`timeToFirstToken` from `completion_start_time`; decode speed goes into the
metadata. Callers get the same response object a non-streaming call returns.
"""
from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

import litellm

from services.workflow_runtime.infrastructure.observability import get_langfuse_client

_MODEL_PARAMS = ("temperature", "max_tokens", "response_format", "timeout")


def _model_parameters(kwargs: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key in _MODEL_PARAMS:
        if key in kwargs and kwargs[key] is not None:
            value = kwargs[key]
            params[key] = value if isinstance(value, str | int | float | bool) else json.dumps(value)
    return params


def _usage(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    prompt = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0) or 0
    return int(prompt), int(completion)


async def acompletion(
    name: str,
    completion: Callable[..., Awaitable[Any]],
    *,
    on_token: Callable[[str], Awaitable[None]] | None = None,
    **kwargs: Any,
) -> Any:
    """`completion(**kwargs)` — the caller's `litellm.acompletion` — traced as
    the generation `name`. The caller passes the function it would have called,
    so it is looked up where it always was (and patched there in tests).
    `on_token` receives each piece of text as the model produces it."""
    lf = get_langfuse_client()
    ctx = lf.start_as_current_generation(
        name=name,
        model=kwargs.get("model"),
        input=kwargs.get("messages"),
        model_parameters=_model_parameters(kwargs),
    ) if lf else nullcontext()

    with ctx as generation:
        started = time.monotonic()
        first_token: float | None = None
        first_token_at: datetime | None = None
        chunks: list[Any] = []
        try:
            stream = await completion(**kwargs, stream=True, stream_options={"include_usage": True})
            stream_type = getattr(litellm, "CustomStreamWrapper", None)
            if isinstance(stream_type, type) and isinstance(stream, stream_type):
                async for chunk in stream:
                    chunks.append(chunk)
                    text = getattr(chunk.choices[0].delta, "content", None) if chunk.choices else None
                    if not text:
                        continue
                    if first_token is None:
                        first_token = time.monotonic()
                        first_token_at = datetime.now(UTC)
                    if on_token is not None:
                        await on_token(text)
                response = litellm.stream_chunk_builder(chunks, messages=kwargs.get("messages"))
            else:
                # A complete response (a stubbed litellm in tests): nothing to
                # time, the rest of the record still applies.
                response = stream
        except BaseException as e:
            if generation is not None:
                generation.update(level="ERROR", status_message=str(e)[:500] or type(e).__name__)
            raise
        finished = time.monotonic()

        if generation is not None:
            prompt_tokens, completion_tokens = _usage(response)
            metadata: dict[str, Any] = {"total_ms": round((finished - started) * 1000)}
            if first_token is not None:
                metadata["ttft_ms"] = round((first_token - started) * 1000)
                decode_s = finished - first_token
                if completion_tokens > 1 and decode_s > 0:
                    # The first token is part of the prefill, not the decode.
                    metadata["decode_tokens_per_s"] = round((completion_tokens - 1) / decode_s, 1)
            generation.update(
                output=response.choices[0].message.content if response.choices else None,
                usage_details={"input": prompt_tokens, "output": completion_tokens},
                completion_start_time=first_token_at,
                metadata=metadata,
            )
        return response
