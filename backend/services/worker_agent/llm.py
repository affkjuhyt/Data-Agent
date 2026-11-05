"""The model gateway as worker-agent sees it (tab 5 G1.1, ADR-37/38/41): one typed call through LiteLLM.

LiteLLM serves an OpenAI-compatible API (LITELLM_BASE_URL, LITELLM_API_KEY) and maps the role
aliases to the on-prem models: `planner` is the fast MoE (understanding, routing), `responder` the
main dense model (writing). The worker never names a provider, so the gateway's routing and its
frontier rules (never for SQL, the Understander, or tainted runs) stay in one place.

Every call names the Pydantic model its answer must fit. For harness-heavy roles (planner,
text_to_sql, …) PydanticAI sends that model's JSON schema as `response_format` (vLLM constrains
the decoding to it). For thinking roles (`responder`, `orchestrator-planner`) constrained decoding
stays off so Qwen3 can emit `<think>…</think>` first; the gateway strips those blocks, validates
the JSON, and shows validation errors once before giving up. A validated answer is only
well-formed: the caller still checks it against the Data Space's own vocabulary or figures. Without
a gateway configured the port is absent and the worker keeps to its deterministic path.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from services.worker_agent import turn_trace

T = TypeVar("T", bound=BaseModel)

# One more request with the validation errors when an answer does not fit its model.
OUTPUT_RETRIES = 1
# The SDK's own retries, with backoff, on 408/409/429/5xx and dropped connections.
HTTP_RETRIES = 2
# Roles whose LiteLLM alias has enable_thinking: true. No response_format — constrained decoding
# would force JSON tokens immediately and erase the reasoning the alias is for. Thinking eats
# latency and tokens; that trade is intentional (correctness over speed) and only these roles pay it.
THINKING_ROLES = frozenset({"responder", "orchestrator-planner"})
# Thinking + a long JSON answer needs headroom beyond the old 20s JSON-only timeout.
THINKING_TIMEOUT_S = 120.0
_THINK_BLOCK = re.compile(r"<think>\s*.*?\s*</think>\s*", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class Reply(Generic[T]):
    content: T
    model: str  # the alias the gateway answered for
    tokens: int  # every request of the call, validation retries included
    latency_ms: int


class ModelUnavailable(RuntimeError):
    """The gateway did not answer usefully; the caller falls back to its deterministic path."""


class Gateway(Protocol):
    async def json_chat(
        self,
        role: str,
        system: str,
        user: str,
        output: type[T],
        *,
        max_tokens: int = 800,
        temperature: float = 0.0,
    ) -> Reply[T]: ...


def strip_think_blocks(text: str) -> str:
    """Drop Qwen3 `<think>…</think>` (and the empty tag form) so only the answer remains."""
    return _THINK_BLOCK.sub("", text).strip()


def _json_payload(text: str) -> str:
    """Prefer a whole-string JSON object; otherwise take the outermost `{…}` span."""
    cleaned = strip_think_blocks(text)
    if not cleaned:
        return cleaned
    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return cleaned[start : end + 1]
        return cleaned


class LiteLLMGateway:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 20.0) -> None:
        # One client for the worker's life, so its connection pool is shared by every call.
        # Thinking roles share this pool but override per-request timeout via the create() call.
        self._client = AsyncOpenAI(
            base_url=base_url.rstrip("/") + "/v1",
            api_key=api_key,
            timeout=timeout,
            max_retries=HTTP_RETRIES,
        )
        self._provider = OpenAIProvider(openai_client=self._client)
        self._agents: dict[tuple[str, type[BaseModel]], Agent[None, Any]] = {}

    def _agent(self, role: str, output: type[T]) -> Agent[None, T]:
        key = (role, output)
        if key not in self._agents:
            self._agents[key] = Agent(
                OpenAIChatModel(role, provider=self._provider),
                output_type=NativeOutput(output),
                retries=OUTPUT_RETRIES,
            )
        return self._agents[key]

    async def json_chat(
        self,
        role: str,
        system: str,
        user: str,
        output: type[T],
        *,
        max_tokens: int = 800,
        temperature: float = 0.0,
    ) -> Reply[T]:
        """`temperature` above 0 only where several samples are wanted (N-version SQL)."""
        if role in THINKING_ROLES:
            return await self._json_chat_thinking(
                role, system, user, output, max_tokens=max_tokens, temperature=temperature
            )
        started = time.monotonic()
        try:
            result = await self._agent(role, output).run(
                user,
                instructions=system,
                model_settings=ModelSettings(max_tokens=max_tokens, temperature=temperature),
            )
        except AgentRunError as exc:
            # Kept short: the message lands in a candidate's error and a run's trace.
            # The trace keeps the kind of failure only: the message can quote the provider's body.
            status = getattr(exc, "status_code", None)
            turn_trace.model_call(
                role=role,
                model=role,
                input_tokens=0,
                output_tokens=0,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=type(exc).__name__ + (f" {status}" if status else ""),
            )
            raise ModelUnavailable(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
        reply = Reply(
            content=result.output,
            model=result.response.model_name or role,
            tokens=result.usage.total_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        # What was asked of the model, without its content, on the turn's trace (turn_trace.py).
        turn_trace.model_call(
            role=role,
            model=reply.model,
            input_tokens=result.usage.input_tokens or 0,
            output_tokens=result.usage.output_tokens or 0,
            latency_ms=reply.latency_ms,
        )
        return reply

    async def _json_chat_thinking(
        self,
        role: str,
        system: str,
        user: str,
        output: type[T],
        *,
        max_tokens: int,
        temperature: float,
    ) -> Reply[T]:
        """Free-form completion (no response_format), strip `<think>`, validate, one repair pass."""
        started = time.monotonic()
        schema = output.model_json_schema()
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"{user}\n\nReply with a JSON object that matches this schema "
                    f"(after any private reasoning):\n{json.dumps(schema, ensure_ascii=False)}"
                ),
            },
        ]
        total_tokens = 0
        last_error = ""
        try:
            for attempt in range(OUTPUT_RETRIES + 1):
                completion = await self._client.chat.completions.create(
                    model=role,
                    messages=messages,  # type: ignore[arg-type]
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=THINKING_TIMEOUT_S,
                )
                usage = completion.usage
                total_tokens += (usage.total_tokens if usage else 0) or 0
                raw = completion.choices[0].message.content or ""
                payload = _json_payload(raw)
                try:
                    content = output.model_validate_json(payload)
                except (ValidationError, ValueError) as exc:
                    last_error = str(exc)[:400]
                    if attempt >= OUTPUT_RETRIES:
                        break
                    messages.append({"role": "assistant", "content": raw})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"Validation errors:\n{last_error}\n\n"
                                "Fix the errors and reply with JSON only. The schema fields "
                                f"include: {sorted(schema.get('properties', {}))}."
                            ),
                        }
                    )
                    continue
                model_name = completion.model or role
                reply = Reply(
                    content=content,
                    model=model_name,
                    tokens=total_tokens,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
                turn_trace.model_call(
                    role=role,
                    model=reply.model,
                    input_tokens=(usage.prompt_tokens if usage else 0) or 0,
                    output_tokens=(usage.completion_tokens if usage else 0) or 0,
                    latency_ms=reply.latency_ms,
                )
                return reply
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            turn_trace.model_call(
                role=role,
                model=role,
                input_tokens=0,
                output_tokens=0,
                latency_ms=int((time.monotonic() - started) * 1000),
                error=type(exc).__name__ + (f" {status}" if status else ""),
            )
            raise ModelUnavailable(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
        turn_trace.model_call(
            role=role,
            model=role,
            input_tokens=0,
            output_tokens=0,
            latency_ms=int((time.monotonic() - started) * 1000),
            error="ValidationError",
        )
        raise ModelUnavailable(f"ValidationError: {last_error[:200]}")


def gateway_from_env() -> Gateway | None:
    url = os.environ.get("LITELLM_BASE_URL")
    if not url:
        return None
    return LiteLLMGateway(url, os.environ.get("LITELLM_API_KEY", ""))
