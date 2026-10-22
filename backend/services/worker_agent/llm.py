"""The model gateway as worker-agent sees it (tab 5 G1.1, ADR-37/38/41): one typed call through LiteLLM.

LiteLLM serves an OpenAI-compatible API (LITELLM_BASE_URL, LITELLM_API_KEY) and maps the role
aliases to the on-prem models: `planner` is the fast MoE (understanding, routing), `responder` the
main dense model (writing). The worker never names a provider, so the gateway's routing and its
frontier rules (never for SQL, the Understander, or tainted runs) stay in one place.

Every call names the Pydantic model its answer must fit. PydanticAI sends that model's JSON schema
as `response_format` (vLLM constrains the decoding to it) and, when the answer still does not
validate, shows the model the validation errors once before giving up. A validated answer is only
well-formed: the caller still checks it against the Data Space's own vocabulary or figures. Without
a gateway configured the port is absent and the worker keeps to its deterministic path.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

T = TypeVar("T", bound=BaseModel)

# One more request with the validation errors when an answer does not fit its model.
OUTPUT_RETRIES = 1
# The SDK's own retries, with backoff, on 408/409/429/5xx and dropped connections.
HTTP_RETRIES = 2


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


class LiteLLMGateway:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 20.0) -> None:
        # One client for the worker's life, so its connection pool is shared by every call.
        client = AsyncOpenAI(
            base_url=base_url.rstrip("/") + "/v1",
            api_key=api_key,
            timeout=timeout,
            max_retries=HTTP_RETRIES,
        )
        self._provider = OpenAIProvider(openai_client=client)
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
        started = time.monotonic()
        try:
            result = await self._agent(role, output).run(
                user,
                instructions=system,
                model_settings=ModelSettings(max_tokens=max_tokens, temperature=temperature),
            )
        except AgentRunError as exc:
            # Kept short: the message lands in a candidate's error and a run's trace.
            raise ModelUnavailable(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
        return Reply(
            content=result.output,
            model=result.response.model_name or role,
            tokens=result.usage.total_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def gateway_from_env() -> Gateway | None:
    url = os.environ.get("LITELLM_BASE_URL")
    if not url:
        return None
    return LiteLLMGateway(url, os.environ.get("LITELLM_API_KEY", ""))
