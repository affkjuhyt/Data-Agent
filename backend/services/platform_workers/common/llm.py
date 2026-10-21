"""Typed chat completions through LiteLLM, on PydanticAI (ADR-37).

Two callers, one shape: the memory worker's insight extraction and the
schema crawler's table description both send a single user message and name
the Pydantic model the answer must fit. PydanticAI sends that model's JSON
schema as ``response_format`` and, when the answer still does not validate,
shows the model the errors once. The SDK retries 429/5xx and dropped
connections with backoff; past that a transport error propagates to the
caller's own recovery path — the memory worker retries the whole event (with
a DLQ), the crawler picks a failed table up on the next tick.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from services.platform_workers.common.settings import LiteLLMSettings

log = logging.getLogger("platform-workers.llm")

T = TypeVar("T", bound=BaseModel)

OUTPUT_RETRIES = 1
HTTP_RETRIES = 2


async def _ask(
    client: AsyncOpenAI, prompt: str, output: type[T], model: str,
    max_tokens: int, temperature: float,
) -> T:
    async with client:
        agent = Agent(
            OpenAIChatModel(model, provider=OpenAIProvider(openai_client=client)),
            output_type=NativeOutput(output),
            retries=OUTPUT_RETRIES,
        )
        result = await agent.run(
            prompt, model_settings=ModelSettings(max_tokens=max_tokens, temperature=temperature),
        )
        return result.output


def ask(
    prompt: str,
    output: type[T],
    model: str,
    *,
    settings: LiteLLMSettings | None = None,
    max_tokens: int = 400,
    temperature: float = 0.0,
    timeout: float = 30.0,
) -> T | None:
    """One completion validated as ``output``.

    Returns ``None`` when the model's answer does not fit even after its retry —
    callers decide what an unusable answer means for them (skip the turn,
    describe the table as empty), so no exception is raised for that.
    """
    settings = settings or LiteLLMSettings()
    # A client per call, closed with it: each call runs in its own event loop
    # (asyncio.run below), and a pooled async client cannot cross loops.
    client = AsyncOpenAI(
        base_url=f"{settings.base_url.rstrip('/')}/v1",
        api_key=settings.api_key,
        timeout=timeout,
        max_retries=HTTP_RETRIES,
    )
    try:
        return asyncio.run(_ask(client, prompt, output, model, max_tokens, temperature))
    except UnexpectedModelBehavior as exc:
        log.warning("llm | answer from model=%s does not fit %s: %s", model, output.__name__, exc)
        return None
