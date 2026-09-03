"""One explicit, self-contained caller per model — no inheritance, no shared base.

Each class conforms to its capability's protocol (base.py: EmbedCaller / RerankCaller /
ChatCaller) by duck typing, and spells out exactly how to reach THAT model: its endpoint,
payload, auth, response parsing (or its in-process handle). Two models that happen to
speak the same protocol still get their own class — so changing one never touches another,
and a model with a quirky endpoint is just edited in place.

Only the stdlib HTTP helpers are shared (urllib, so the image needs no extra dep)."""
from __future__ import annotations

from typing import Any, cast
import json
import urllib.request

import config as cfg


# HTTP helpers
def _post(base_url: str, path: str, payload: dict[str, Any], api_key: str | None = None) -> dict[str, Any]:
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
        return cast(dict[str, Any], json.loads(r.read().decode("utf-8")))


def _post_raw(base_url: str, path: str, payload: dict[str, Any], api_key: str | None = None) -> Any:
    """Same as _post but without the dict cast — TEI's /embed answers a bare JSON array."""
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


def _get(base_url: str, path: str, api_key: str | None = None, timeout: float = 30) -> dict[str, Any]:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(base_url + path, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return cast(dict[str, Any], json.loads(r.read().decode("utf-8")))

# EMBED
class TeiEmbedCaller:
    """HuggingFace text-embeddings-inference (TEI): POST /embed {"inputs": [...]} →
    bare JSON array of vectors, one per input, in request order."""
    def __init__(self, url: str, api_key: str | None = None) -> None:
        self._url = url
        self._key = api_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        data = _post_raw(self._url, "/embed", {"inputs": texts}, self._key)
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError(f"TEI /embed returned {type(data).__name__} with "
                             f"{len(data) if isinstance(data, list) else 'no'} vectors for {len(texts)} inputs")
        return cast(list[list[float]], data)


# RERANK
class TeiRerankCaller:
    """TEI reranker: POST /rerank {"query", "texts"} → [{"index", "score"}, ...].
    TEI scores every pair and has no top_n parameter — slice client-side."""
    def __init__(self, url: str, api_key: str | None = None) -> None:
        self._url = url
        self._key = api_key

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        if not documents:
            return []
        data = _post_raw(self._url, "/rerank", {"query": query, "texts": documents}, self._key)
        ranked = sorted(((r["index"], float(r["score"])) for r in data), key=lambda x: x[1], reverse=True)
        return ranked[:top_n]



# CHAT
class CloudflareQwenChatCaller:
    def __init__(self, url: str, api_key: str | None = None, model: str = "") -> None:
        self._url = url
        self._key = api_key
        # If a model is pinned (REMOTE_LLM_MODEL), use it — a server that serves
        # several models (e.g. ollama) would otherwise be picked non-deterministically
        # by /v1/models[0]. Empty string = auto-discover the first advertised model.
        self._model = model
        self._defaults = {"temperature": 0.1, "max_tokens": 1024}

    def _model_id(self) -> str:
        if not self._model:
            self._model = _get(self._url, "/v1/models", self._key)["data"][0]["id"]
        return self._model

    def chat(self, messages: list[dict[str, Any]], response_format: dict[str, Any] | None, **opts: Any) -> str:
        params = {**self._defaults, **{k: v for k, v in opts.items() if v is not None}}
        body: dict[str, Any] = {"model": self._model_id(), "messages": messages, "stream": False, **params}
        # Qwen3 on vLLM defaults to emitting <think> blocks, which break every
        # JSON-parsing consumer of /generate (kb ingestion entity extraction,
        # canonicalize). vLLM honors chat_template_kwargs; ollama/llama.cpp
        # ignore unknown body fields, so this is safe for the other backends.
        body["chat_template_kwargs"] = {"enable_thinking": False}
        if response_format is not None:
            body["response_format"] = response_format
        data = _post(self._url, "/v1/chat/completions", body, self._key)
        return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""

    def generate(self, prompt: str, system_prompt: str = "",
                 response_format: dict[str, Any] | None = None, **opts: Any) -> str:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, response_format, **opts)
