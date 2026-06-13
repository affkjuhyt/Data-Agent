"""
Thin async client for the llama server's Ollama-compatible /api/chat endpoint.

Bypasses LiteLLM entirely so we control the exact payload fields
(format, grammar_gbnf, options.num_predict, temperature, etc.) without marshaling surprises.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

import config as cfg

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300.0  # Increased from 120s to 300s for CPU inference

# Transient transport failures (llama still warming up, momentary network blip) are
# retried a few times before giving up — a single ConnectError must NOT abandon
# entity/relationship extraction and force the heuristic fallback.
_TRANSPORT_RETRIES = 3
_TRANSPORT_BACKOFF = 2.0  # seconds, multiplied by attempt number

# ── GBNF grammars ──────────────────────────────────────────────────────────────
#
# Rules must be single-line.
# llama_cpp GBNF identifiers: [a-zA-Z0-9-]+ (hyphens OK, NO underscores).
#
# Stage 1: extract summary, entities (with descriptions), intents — no relationships.
# Entities are objects {name, description} so the description can be embedded
# and stored on the Neo4j entity node for neighbour ranking at query time.
ENTITY_INTENT_GBNF = "\n".join([
    r'root    ::= "{" ws sum-kv "," ws ents-kv "," ws ints-kv ws "}" ws',
    r'sum-kv  ::= "\"summary\""  ws ":" ws strval',
    r'ents-kv ::= "\"entities\"" ws ":" ws entarr',
    r'ints-kv ::= "\"intents\""  ws ":" ws strarr',
    r'entarr  ::= "[" ws "]" | "[" ws entobj (ws "," ws entobj)* ws "]"',
    r'entobj  ::= "{" ws "\"name\"" ws ":" ws strval ws "," ws "\"description\"" ws ":" ws strval ws "}"',
    r'strarr  ::= "[" ws "]" | "[" ws strval (ws "," ws strval)* ws "]"',
    r'strval  ::= "\"" strchar* "\""',
    r'strchar ::= [^"\\] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])',
    r'ws      ::= ([ \t\n\r] ws)?',
])

# Stage 2: extract relationships — the model receives the entity list from
# stage 1 so it can ONLY connect entities it was explicitly given.
RELATIONSHIP_GBNF = "\n".join([
    r'root    ::= "{" ws rels-kv ws "}" ws',
    r'rels-kv ::= "\"relationships\"" ws ":" ws relarr',
    r'relarr  ::= "[" ws "]" | "[" ws relobj (ws "," ws relobj)* ws "]"',
    r'relobj  ::= "{" ws "\"from\"" ws ":" ws strval ws "," ws "\"to\"" ws ":" ws strval ws "," ws "\"type\"" ws ":" ws strval ws "}"',
    r'strval  ::= "\"" strchar* "\""',
    r'strchar ::= [^"\\] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])',
    r'ws      ::= ([ \t\n\r] ws)?',
])

# Legacy combined grammar kept for reference; no longer used by gold.py.
EXTRACT_GBNF = "\n".join([
    r'root    ::= "{" ws sum-kv "," ws ents-kv "," ws rels-kv "," ws ints-kv ws "}" ws',
    r'sum-kv  ::= "\"summary\""       ws ":" ws strval',
    r'ents-kv ::= "\"entities\""      ws ":" ws strarr',
    r'rels-kv ::= "\"relationships\"" ws ":" ws relarr',
    r'ints-kv ::= "\"intents\""       ws ":" ws strarr',
    r'strarr  ::= "[" ws "]" | "[" ws strval (ws "," ws strval)* ws "]"',
    r'relarr  ::= "[" ws "]" | "[" ws relobj (ws "," ws relobj)* ws "]"',
    r'relobj  ::= "{" ws "\"from\"" ws ":" ws strval ws "," ws "\"to\"" ws ":" ws strval ws "," ws "\"type\"" ws ":" ws strval ws "}"',
    r'strval  ::= "\"" strchar* "\""',
    r'strchar ::= [^"\\] | "\\" (["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])',
    r'ws      ::= ([ \t\n\r] ws)?',
])


# ── System-prompt snapshot registry ────────────────────────────────────────────
#
# /api/generate prefills ONLY the prompt and relies on a pre-cached system-prompt KV
# snapshot being loaded server-side (selected by the X-System-Type header). The server
# does NOT fall back to an inline system prompt for generate — if the snapshot is absent
# it runs with no system prompt at all. So the generate helpers must KNOW whether a given
# system_type was actually registered. main.py populates this set after it confirms each
# registration; when a type is missing we degrade safely by inlining the system prompt
# into the generated prompt.
CONFIRMED_SYSTEM_TYPES: set[str] = set()


def mark_system_registered(name: str) -> None:
    CONFIRMED_SYSTEM_TYPES.add(name)


def is_system_registered(name: str) -> bool:
    return name in CONFIRMED_SYSTEM_TYPES


async def _post(endpoint: str, payload: dict[str, Any], system_type: str, timeout: float) -> dict[str, Any]:
    """POST to a llama endpoint with transient-error retry; return the parsed JSON dict.

    `system_type` is forwarded as the X-System-Type header so the llama server can load
    the matching pre-cached KV snapshot (registered at worker startup) instead of
    re-evaluating the full system prompt on every call — without it, CPU inference of the
    large extraction prompts routinely exceeds the timeout and the call fails.
    """
    headers = {"X-System-Type": system_type} if system_type else None

    for attempt in range(1, _TRANSPORT_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{cfg.LLAMA_BASE_URL}{endpoint}",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
            return resp.json()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            if attempt == _TRANSPORT_RETRIES:
                log.error("llama %s transport error after %d attempts: %s", endpoint, attempt, exc)
                raise
            wait = _TRANSPORT_BACKOFF * attempt
            log.warning(
                "llama %s transport error (attempt %d/%d): %s — retrying in %.1fs",
                endpoint, attempt, _TRANSPORT_RETRIES, exc, wait,
            )
            await asyncio.sleep(wait)
    raise RuntimeError("unreachable")  # loop either returns or raises


def _chat_content(data: dict[str, Any]) -> str:
    content: str = (data.get("message") or {}).get("content") or ""
    if not content:
        raise RuntimeError("llama server returned empty content")
    return content


def _generate_content(data: dict[str, Any]) -> str:
    content: str = data.get("response") or ""
    if not content:
        raise RuntimeError("llama server returned empty content")
    return content


# ── /api/generate — system prompt baked into the KV snapshot, prompt-only encode ──
#
# Preferred path for stateless extraction (gold/silver): the policy/instructions live in
# a pre-cached KV snapshot, so each call only encodes the chunk text. Falls back to
# inlining the system prompt when the snapshot was not confirmed-registered.

def _build_generate_prompt(prompt: str, system_prompt: str, system_type: str) -> str:
    if system_type and is_system_registered(system_type):
        return prompt  # system already baked into the server-side KV snapshot
    if system_prompt:
        log.warning(
            "llama generate: system_type %r not registered — inlining system prompt as fallback",
            system_type,
        )
        return f"{system_prompt}\n\n{prompt}"
    return prompt


async def generate_json(
    prompt: str,
    *,
    system_prompt: str = "",
    system_type: str = "",
    max_tokens: int = 512,
    temperature: float = 0.1,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Generate JSON via /api/generate. System prompt is supplied by the KV snapshot
    selected via system_type; only `prompt` is encoded. Returns the assistant content."""
    payload: dict[str, Any] = {
        "model":  "llama3",
        "prompt": _build_generate_prompt(prompt, system_prompt, system_type),
        "format": "json",
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": temperature,
        },
    }
    return _generate_content(await _post("/api/generate", payload, system_type, timeout))


async def generate_plain(
    prompt: str,
    *,
    system_prompt: str = "",
    system_type: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.3,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Generate free-form text via /api/generate (no JSON mode)."""
    payload: dict[str, Any] = {
        "model":  "llama3",
        "prompt": _build_generate_prompt(prompt, system_prompt, system_type),
        "stream": False,
        "options": {
            "num_predict": max_tokens,
            "temperature": temperature,
        },
    }
    return _generate_content(await _post("/api/generate", payload, system_type, timeout))


# ── /api/chat — multi-turn; kept for callers that genuinely need conversational shape ──

async def chat_plain(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 1024,
    temperature: float = 0.3,
    timeout: float = _DEFAULT_TIMEOUT,
    system_type: str = "",
) -> str:
    """Send a chat request and return the raw assistant text (no JSON mode).

    Use this when the response should be free-form text rather than structured JSON
    (e.g. the conflict-merge handler which generates a merged prose chunk).
    """
    payload: dict[str, Any] = {
        "model":    "llama3",
        "messages": messages,
        "stream":   False,
        "options": {
            "num_predict": max_tokens,
            "temperature": temperature,
        },
    }
    return _chat_content(await _post("/api/chat", payload, system_type, timeout))


async def chat_json(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 512,
    temperature: float = 0.1,
    timeout: float = _DEFAULT_TIMEOUT,
    grammar_gbnf: str = "",
    system_type: str = "",
) -> str:
    """Send a chat request to the llama server and return the assistant content string.

    Passes format='json' so the model's grammar-constrained JSON mode is active.
    When grammar_gbnf is provided it is forwarded to the server for schema-constrained
    generation (prevents the model from generating off-schema structures that truncate).
    `system_type` selects a pre-cached KV snapshot on the server (see _post).
    Raises httpx.HTTPStatusError on non-2xx, RuntimeError if content is empty.
    """
    payload: dict[str, Any] = {
        "model":    "llama3",
        "messages": messages,
        "format":   "json",
        "stream":   False,
        "options": {
            "num_predict":   max_tokens,
            "temperature":   temperature,
        },
    }
    if grammar_gbnf:
        payload["grammar_gbnf"] = grammar_gbnf

    return _chat_content(await _post("/api/chat", payload, system_type, timeout))
