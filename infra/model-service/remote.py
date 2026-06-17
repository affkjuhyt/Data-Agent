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


def llm_chat(messages: list[dict], response_format: dict | None,
             max_tokens: int, temperature: float) -> str:
    """Forward a chat completion to the OpenAI-compatible REMOTE_LLM_URL server
    (e.g. Colab vLLM) and return the assistant content string."""
    global _llm_model_id
    base = cfg.REMOTE_LLM_URL
    if not _llm_model_id:
        _llm_model_id = cfg.REMOTE_LLM_MODEL or _get(base, "/v1/models", cfg.REMOTE_LLM_KEY)["data"][0]["id"]
    payload: dict = {
        "model": _llm_model_id, "messages": messages,
        "max_tokens": max_tokens, "temperature": temperature, "stream": False,
    }
    if response_format is not None:
        payload["response_format"] = response_format
    data = post(base, "/v1/chat/completions", payload, api_key=cfg.REMOTE_LLM_KEY)
    return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
