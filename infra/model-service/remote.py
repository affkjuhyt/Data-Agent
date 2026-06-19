"""Tiny stdlib HTTP proxy helper — forwards /embed and /rerank to a remote GPU
server (e.g. Colab + cloudflared) when REMOTE_*_URL is configured. Uses urllib so
the model-service image needs no extra dependency (fast rebuild)."""
from __future__ import annotations

import json
import urllib.request

import config as cfg


def post(base_url: str, path: str, payload: dict, api_key: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    key = api_key if api_key is not None else cfg.REMOTE_API_KEY
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        base_url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=cfg.REMOTE_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(base_url: str, path: str, api_key: str | None = None) -> dict:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(base_url + path, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


# Served LLM model id is required by OpenAI /v1/chat/completions. Pinned via
# REMOTE_LLM_MODEL or discovered once from /v1/models and cached.
_llm_model_id: str = ""

# Cached gateway status for /health — updated by probe_llm() at startup and by a
# successful discover. /health reads this WITHOUT a network call so a down backend
# never makes the healthcheck hang on the 30s /v1/models timeout.
_llm_status: dict = {"configured": bool(cfg.REMOTE_LLM_URL), "ready": False, "model": None}


def discover_llm_model() -> str:
    """Return the served LLM model id (pinned via REMOTE_LLM_MODEL or discovered
    from /v1/models, cached after first lookup). Raises if no backend is reachable."""
    global _llm_model_id, _llm_status
    if not cfg.REMOTE_LLM_URL:
        raise RuntimeError("REMOTE_LLM_URL is not set")
    if not _llm_model_id:
        _llm_model_id = (
            cfg.REMOTE_LLM_MODEL
            or _get(cfg.REMOTE_LLM_URL, "/v1/models", cfg.REMOTE_LLM_KEY)["data"][0]["id"]
        )
    _llm_status = {"configured": True, "ready": True, "model": _llm_model_id}
    return _llm_model_id


def probe_llm() -> dict:
    """Validate the configured LLM backend once and cache the result (non-raising).
    Called at startup; result is surfaced via llm_status()/health."""
    global _llm_status
    if not cfg.REMOTE_LLM_URL:
        _llm_status = {"configured": False, "ready": False, "model": None}
        return dict(_llm_status)
    try:
        discover_llm_model()
    except Exception as e:  # noqa: BLE001 — startup probe must never raise
        _llm_status = {"configured": True, "ready": False, "model": None, "error": str(e)}
    return dict(_llm_status)


def llm_status() -> dict:
    """Last-known LLM gateway status (no network call)."""
    return dict(_llm_status)


def llm_chat(messages: list[dict], response_format: dict | None,
             max_tokens: int, temperature: float) -> str:
    """Forward a chat completion to the OpenAI-compatible REMOTE_LLM_URL server
    (e.g. Colab vLLM) and return the assistant content string."""
    model_id = discover_llm_model()
    payload: dict = {
        "model": model_id, "messages": messages,
        "max_tokens": max_tokens, "temperature": temperature, "stream": False,
    }
    if response_format is not None:
        payload["response_format"] = response_format
    data = post(cfg.REMOTE_LLM_URL, "/v1/chat/completions", payload, api_key=cfg.REMOTE_LLM_KEY)
    return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
