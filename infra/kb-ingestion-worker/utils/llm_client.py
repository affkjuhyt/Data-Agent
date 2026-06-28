"""
LLM access for the ingestion worker — gold/silver/conflict_resolver import this.

The worker no longer knows or chooses the LLM backend. Every call goes to the
model-service GATEWAY (/generate); model-service decides whether to serve locally
or forward to a remote server (e.g. Colab vLLM) based on its own env. Switching
the LLM backend is a model-service env change — nothing here changes.

The JSON SHAPE each call wants is still the worker's concern (domain logic), so the
per-system_type schema registry (llm_schemas) is passed to the gateway as
`json_schema`. The gateway applies it as the server's response_format.
"""
from __future__ import annotations

import logging

import httpx

from utils import config as cfg
from utils.llm_schemas import SCHEMAS_BY_SYSTEM_TYPE

log = logging.getLogger(__name__)

BACKEND = "gateway"
_BASE = cfg.MODEL_SERVICE_URL.rstrip("/")
_DEFAULT_TIMEOUT = 300.0

log.info("LLM access: model-service gateway (%s/generate)", _BASE)

# One shared connection pool for every LLM call. Opening a new AsyncClient per
# request (as the old code did) discarded the pool each time and reconnected on
# every generate; a single lazily-created client reuses keep-alive connections.
# Created lazily so it binds to the running event loop, not import time.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
    return _client


def _schema(system_type: str) -> dict | None:
    return SCHEMAS_BY_SYSTEM_TYPE.get(system_type)


async def _generate(payload: dict, timeout: float) -> str:
    resp = await _get_client().post(f"{_BASE}/generate", json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("content", "")


async def generate_json(
    prompt: str, *, system_prompt: str = "", system_type: str = "",
    max_tokens: int = 512, temperature: float = 0.1, timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    payload: dict = {
        "prompt": prompt, "system_prompt": system_prompt, "json_mode": True,
        "max_tokens": max_tokens, "temperature": temperature,
    }
    schema = _schema(system_type)
    if schema is not None:
        payload["json_schema"] = schema
    return await _generate(payload, timeout)


async def generate_plain(
    prompt: str, *, system_prompt: str = "", system_type: str = "",
    max_tokens: int = 1024, temperature: float = 0.3, timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    return await _generate({
        "prompt": prompt, "system_prompt": system_prompt, "json_mode": False,
        "max_tokens": max_tokens, "temperature": temperature,
    }, timeout)


async def chat_plain(
    messages: list[dict], *, max_tokens: int = 1024, temperature: float = 0.3,
    timeout: float = _DEFAULT_TIMEOUT, system_type: str = "",
) -> str:
    return await _generate({
        "messages": messages, "json_mode": False,
        "max_tokens": max_tokens, "temperature": temperature,
    }, timeout)


async def chat_json(
    messages: list[dict], *, max_tokens: int = 512, temperature: float = 0.1,
    timeout: float = _DEFAULT_TIMEOUT, grammar_gbnf: str = "", system_type: str = "",
) -> str:
    payload: dict = {
        "messages": messages, "json_mode": True,
        "max_tokens": max_tokens, "temperature": temperature,
    }
    schema = _schema(system_type)
    if schema is not None:
        payload["json_schema"] = schema
    return await _generate(payload, timeout)
