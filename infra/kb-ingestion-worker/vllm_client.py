"""
Thin async client for an OpenAI-compatible vLLM server (/v1/chat/completions).

Drop-in alternative to llama_client — same function signatures — selected via
llm_client when LLM_BACKEND=vllm (see config.py). Target is typically the Colab
vLLM+Qwen server from model/vllm/vllm_qwen_colab.ipynb, reached through a
Cloudflare tunnel; VLLM_BASE_URL and VLLM_API_KEY come from env.

JSON enforcement: instead of llama.cpp GBNF grammars, the JSON shape is enforced
server-side (xgrammar token masking) by a JSON Schema in `response_format`,
selected by `system_type`. The X-System-Type KV-snapshot machinery is not needed:
vLLM's prefix caching reuses repeated system prompts automatically, so the system
prompt is always sent inline.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

import config as cfg

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300.0

# Transient transport failures (tunnel hiccup, Colab cell restarting) are retried
# before giving up — a single ConnectError must NOT abandon extraction and force
# the heuristic fallback.
_TRANSPORT_RETRIES = 3
_TRANSPORT_BACKOFF = 2.0  # seconds, multiplied by attempt number

# ── JSON Schemas per system_type ───────────────────────────────────────────────
#
# Functional equivalents of llama_client's GBNF grammars, but shaped to what the
# worker prompts actually request (score / skip fields included). Standalone
# copies of model/vllm/schemas.py — the worker image does not ship model/.

_ENTITY_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name":        {"type": "string"},
                    "description": {"type": "string"},
                    "score":       {"type": "number"},
                },
                "required": ["name", "description", "score"],
                "additionalProperties": False,
            },
        },
        "intents": {"type": "array", "items": {"type": "string"}},
        "skip":    {"type": "boolean"},
    },
    "required": ["summary", "entities", "intents", "skip"],
    "additionalProperties": False,
}

_RELATIONSHIP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from":  {"type": "string"},
                    "to":    {"type": "string"},
                    "type":  {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["from", "to", "type", "score"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["relationships"],
    "additionalProperties": False,
}

_CONFLICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "has_conflict":  {"type": "boolean"},
        "conflict_type": {"type": "string"},
        "severity":      {"type": "string"},
        "explanation":   {"type": "string"},
    },
    "required": ["has_conflict", "conflict_type", "severity", "explanation"],
    "additionalProperties": False,
}

_MODERATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "blocked": {"type": "boolean"},
        "reason":  {"type": "string"},
    },
    "required": ["blocked", "reason"],
    "additionalProperties": False,
}

# Silver per-chunk call: summary (same language as the chunk) + intents + skip flag.
_CHUNK_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "intents": {"type": "array", "items": {"type": "string"}},
        "skip":    {"type": "boolean"},
    },
    "required": ["summary", "intents", "skip"],
    "additionalProperties": False,
}

# Gold map-reduce pass: one batch of chunk summaries → one combined summary.
_DOC_REDUCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

# Gold document-level entity extraction. `aliases` carries other surface forms of
# the SAME entity (critical for Vietnamese synonyms: "TP.HCM" / "Sài Gòn" /
# "Thành phố Hồ Chí Minh") so the entity registry can learn them as exact lookups.
# `type` is a closed vocabulary — it becomes the Neo4j secondary label, but NEVER
# part of node identity (LLM typing is noisy; identity must not depend on it).
ENTITY_TYPES = [
    "person", "organization", "location", "law_document",
    "system_product", "event", "time_period", "concept",
]

_DOC_ENTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name":        {"type": "string"},
                    "type":        {"type": "string", "enum": ENTITY_TYPES},
                    "aliases":     {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                    "score":       {"type": "number"},
                },
                "required": ["name", "type", "aliases", "description", "score"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["entities"],
    "additionalProperties": False,
}

# Registry merge verification: single same-entity judgment for gray-band
# embedding matches (cosine alone cannot separate e.g. Bangladesh from Ấn Độ).
_MERGE_VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"same": {"type": "boolean"}},
    "required": ["same"],
    "additionalProperties": False,
}

# Alias verification: pair-by-pair binary judgments (far more reliable than
# asking the model to re-generate a cleaned list).
_ALIAS_VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name":  {"type": "string"},
                    "alias": {"type": "string"},
                    "same":  {"type": "boolean"},
                },
                "required": ["name", "alias", "same"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["pairs"],
    "additionalProperties": False,
}

_SCHEMAS_BY_SYSTEM_TYPE: dict[str, dict[str, Any]] = {
    "entity-intent": _ENTITY_INTENT_SCHEMA,
    "relationship":  _RELATIONSHIP_SCHEMA,
    "conflict":      _CONFLICT_SCHEMA,
    "moderate":      _MODERATE_SCHEMA,
    "chunk-summary": _CHUNK_SUMMARY_SCHEMA,
    "doc-reduce":    _DOC_REDUCE_SCHEMA,
    "doc-entity":    _DOC_ENTITY_SCHEMA,
    "alias-verify":  _ALIAS_VERIFY_SCHEMA,
    "merge-verify":  _MERGE_VERIFY_SCHEMA,
}


def _json_response_format(system_type: str) -> dict[str, Any]:
    """Schema-constrained format for known system types; plain JSON mode otherwise."""
    schema = _SCHEMAS_BY_SYSTEM_TYPE.get(system_type)
    if schema is None:
        if system_type:
            log.debug("vllm: no schema for system_type %r — using json_object mode", system_type)
        return {"type": "json_object"}
    return {
        "type": "json_schema",
        "json_schema": {"name": system_type.replace("-", "_"), "strict": True, "schema": schema},
    }


# ── Transport ──────────────────────────────────────────────────────────────────

def _headers() -> dict[str, str]:
    if cfg.VLLM_API_KEY:
        return {"Authorization": f"Bearer {cfg.VLLM_API_KEY}"}
    return {}


async def _request(method: str, path: str, payload: dict[str, Any] | None,
                   timeout: float) -> dict[str, Any]:
    """Call the vLLM server with transient-error retry; return the parsed JSON dict."""
    url = f"{cfg.VLLM_BASE_URL.rstrip('/')}{path}"
    for attempt in range(1, _TRANSPORT_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(method, url, json=payload, headers=_headers())
                resp.raise_for_status()
            return resp.json()
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            if attempt == _TRANSPORT_RETRIES:
                log.error("vllm %s transport error after %d attempts: %s", path, attempt, exc)
                raise
            wait = _TRANSPORT_BACKOFF * attempt
            log.warning(
                "vllm %s transport error (attempt %d/%d): %s — retrying in %.1fs",
                path, attempt, _TRANSPORT_RETRIES, exc, wait,
            )
            await asyncio.sleep(wait)
    raise RuntimeError("unreachable")  # loop either returns or raises


# The served model id is required in every request. VLLM_MODEL pins it; when unset
# it is discovered once from /v1/models and cached (the Colab notebook picks the
# model from the GPU, so the worker should not hard-code it).
_model_id: str = cfg.VLLM_MODEL


async def _get_model(timeout: float) -> str:
    global _model_id
    if _model_id:
        return _model_id
    data = await _request("GET", "/v1/models", None, timeout)
    models = [m["id"] for m in data.get("data", [])]
    if not models:
        raise RuntimeError(f"vLLM server at {cfg.VLLM_BASE_URL} serves no models")
    _model_id = models[0]
    log.info("vllm: discovered served model %r", _model_id)
    return _model_id


async def _chat(messages: list[dict[str, str]], *, max_tokens: int, temperature: float,
                timeout: float, response_format: dict[str, Any] | None) -> str:
    payload: dict[str, Any] = {
        "model":       await _get_model(timeout),
        "messages":    messages,
        "max_tokens":  max_tokens,
        "temperature": temperature,
        "stream":      False,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    data = await _request("POST", "/v1/chat/completions", payload, timeout)
    content: str = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    if not content:
        raise RuntimeError("vllm server returned empty content")
    return content


def _messages(prompt: str, system_prompt: str) -> list[dict[str, str]]:
    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.append({"role": "user", "content": prompt})
    return msgs


# ── Public API — mirrors llama_client signatures ──────────────────────────────

async def generate_json(
    prompt: str,
    *,
    system_prompt: str = "",
    system_type: str = "",
    max_tokens: int = 512,
    temperature: float = 0.1,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Generate JSON constrained by the schema registered for `system_type`
    (plain JSON mode for unknown types). Returns the assistant content string."""
    return await _chat(
        _messages(prompt, system_prompt),
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        response_format=_json_response_format(system_type),
    )


async def generate_plain(
    prompt: str,
    *,
    system_prompt: str = "",
    system_type: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.3,
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Generate free-form text (no JSON mode)."""
    return await _chat(
        _messages(prompt, system_prompt),
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        response_format=None,
    )


async def chat_plain(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 1024,
    temperature: float = 0.3,
    timeout: float = _DEFAULT_TIMEOUT,
    system_type: str = "",
) -> str:
    """Send a chat request and return the raw assistant text (no JSON mode)."""
    return await _chat(
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        response_format=None,
    )


async def chat_json(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 512,
    temperature: float = 0.1,
    timeout: float = _DEFAULT_TIMEOUT,
    grammar_gbnf: str = "",
    system_type: str = "",
) -> str:
    """Send a chat request with schema-constrained JSON output.

    `grammar_gbnf` is accepted for llama_client signature compatibility but ignored —
    the schema registered for `system_type` provides the constraint instead.
    """
    if grammar_gbnf and system_type not in _SCHEMAS_BY_SYSTEM_TYPE:
        log.warning(
            "vllm chat_json: grammar_gbnf given but no schema for system_type %r — "
            "falling back to unconstrained json_object mode", system_type,
        )
    return await _chat(
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        response_format=_json_response_format(system_type),
    )
