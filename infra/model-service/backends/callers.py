"""Concrete callers — one class per way of reaching a model.

Each class conforms to a capability protocol (base.py: EmbedCaller / RerankCaller /
ChatCaller) but is free to implement it however that model's endpoint requires. You
register a specific caller instance per model (see sync.py), so a new endpoint with a
different payload shape is just a new class here — no dispatch, no shared "if remote"
branches. Routers only ever call caller.embed / caller.rerank / caller.chat.

The tiny stdlib HTTP helpers live here too (urllib, so the image needs no extra dep)."""
from __future__ import annotations

import json
import urllib.request

import config as cfg


# ── HTTP helpers ───────────────────────────────────────────────────────────────
def _post(base_url: str, path: str, payload: dict, api_key: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
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


# ── Embed callers (EmbedCaller) ──────────────────────────────────────────────────
class LocalEmbedCaller:
    """In-process SentenceTransformer."""
    def __init__(self, handle) -> None:
        self._handle = handle

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._handle.encode(texts, normalize_embeddings=True).tolist()


class RemoteEmbedCaller:
    """POST {url}/embed → {"embeddings": [...]} (our model-service embed contract)."""
    def __init__(self, url: str, api_key: str | None = None) -> None:
        self._url, self._key = url, api_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return _post(self._url, "/embed", {"texts": texts}, self._key)["embeddings"]


# ── Rerank callers (RerankCaller) ─────────────────────────────────────────────────
class LocalRerankCaller:
    """In-process CrossEncoder."""
    def __init__(self, handle) -> None:
        self._handle = handle

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        if not documents:
            return []
        scores = self._handle.predict([(query, d) for d in documents]).tolist()
        ranked = sorted(((i, float(s)) for i, s in enumerate(scores)), key=lambda x: x[1], reverse=True)
        return ranked[:min(top_n, len(ranked))]


class RemoteRerankCaller:
    """POST {url}/rerank → {"results": [{index, relevance_score}]}."""
    def __init__(self, url: str, api_key: str | None = None) -> None:
        self._url, self._key = url, api_key

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        if not documents:
            return []
        data = _post(self._url, "/rerank",
                     {"query": query, "documents": documents, "top_n": top_n}, self._key)
        return [(r["index"], r["relevance_score"]) for r in data.get("results", [])]


# ── Chat callers (ChatCaller) ─────────────────────────────────────────────────────
class _ChatMixin:
    """generate() is the same convenience for every chat caller: wrap a prompt into a
    (system?, user) messages list and delegate to chat()."""
    def generate(self, prompt: str, system_prompt: str = "",
                 response_format: dict | None = None, **opts) -> str:
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, response_format, **opts)


class OpenAIChatCaller(_ChatMixin):
    """Standard OpenAI /v1/chat/completions (vLLM, llama-server, ollama). `defaults`
    are this model's call params (temperature/max_tokens); per-call overrides win."""
    def __init__(self, url: str, model: str, api_key: str | None = None,
                 defaults: dict | None = None) -> None:
        self._url, self._model, self._key = url, model, api_key
        self._defaults = defaults or {}

    def chat(self, messages: list[dict], response_format: dict | None, **opts) -> str:
        params = {**self._defaults, **{k: v for k, v in opts.items() if v is not None}}
        body: dict = {"model": self._model, "messages": messages, "stream": False, **params}
        if response_format is not None:
            body["response_format"] = response_format
        data = _post(self._url, "/v1/chat/completions", body, self._key)
        return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""


# Example of a model whose endpoint speaks the SAME capability but a DIFFERENT payload.
# A Cloudflare-fronted server might keep /v1/chat/completions yet wrap generation params
# under a "parameters" object (illustrative — adjust to the real endpoint's contract).
# Registering an instance of this for that model is all it takes; routers are unchanged.
class CloudflareChatCaller(_ChatMixin):
    def __init__(self, url: str, model: str, api_key: str | None = None,
                 defaults: dict | None = None) -> None:
        self._url, self._model, self._key = url, model, api_key
        self._defaults = defaults or {}

    def chat(self, messages: list[dict], response_format: dict | None, **opts) -> str:
        params = {**self._defaults, **{k: v for k, v in opts.items() if v is not None}}
        body: dict = {"model": self._model, "messages": messages, "parameters": params}
        if response_format is not None:
            body["parameters"]["response_format"] = response_format
        data = _post(self._url, "/v1/chat/completions", body, self._key)
        return (data.get("result") or {}).get("response") or \
               (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
