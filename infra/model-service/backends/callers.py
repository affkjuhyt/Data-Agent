"""One explicit, self-contained caller per model — no inheritance, no shared base.

Each class conforms to its capability's protocol (base.py: EmbedCaller / RerankCaller /
ChatCaller) by duck typing, and spells out exactly how to reach THAT model: its endpoint,
payload, auth, response parsing (or its in-process handle). Two models that happen to
speak the same protocol still get their own class — so changing one never touches another,
and a model with a quirky endpoint is just edited in place.

Only the stdlib HTTP helpers are shared (urllib, so the image needs no extra dep)."""
from __future__ import annotations

import json
import urllib.request

import config as cfg


# ── HTTP helpers (the one shared thing) ──────────────────────────────────────────
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


def _get(base_url: str, path: str, api_key: str | None = None, timeout: float = 30) -> dict:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(base_url + path, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ═══════════════════════════════════════════════════════════════════════════════
# EMBED
# ═══════════════════════════════════════════════════════════════════════════════
class VietnameseEmbedCaller:
    def __init__(self, url: str, api_key: str | None = None) -> None:
        self._url = url
        self._key = api_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        data = _post(self._url, "/embed", {"texts": texts}, self._key)
        return data["embeddings"]


class MultilingualE5SmallCaller:
    def __init__(self, handle) -> None:
        self._handle = handle

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._handle.encode(texts, normalize_embeddings=True).tolist()


# RERANK
class VietnameseRerankCaller:
    def __init__(self, url: str, api_key: str | None = None) -> None:
        self._url = url
        self._key = api_key

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        if not documents:
            return []
        data = _post(self._url, "/rerank",
                     {"query": query, "documents": documents, "top_n": top_n}, self._key)
        return [(r["index"], r["relevance_score"]) for r in data.get("results", [])]



# CHAT
class CloudflareQwenChatCaller:
    def __init__(self, url: str, api_key: str | None = None) -> None:
        self._url = url
        self._key = api_key
        self._model = ""
        self._defaults = {"temperature": 0.1, "max_tokens": 1024}

    def _model_id(self) -> str:
        if not self._model:
            self._model = _get(self._url, "/v1/models", self._key)["data"][0]["id"]
        return self._model

    def chat(self, messages: list[dict], response_format: dict | None, **opts) -> str:
        params = {**self._defaults, **{k: v for k, v in opts.items() if v is not None}}
        body: dict = {"model": self._model_id(), "messages": messages, "stream": False, **params}
        if response_format is not None:
            body["response_format"] = response_format
        data = _post(self._url, "/v1/chat/completions", body, self._key)
        return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""

    def generate(self, prompt: str, system_prompt: str = "",
                 response_format: dict | None = None, **opts) -> str:
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, response_format, **opts)


class LlamaServerChatCaller:
    """Local llama-server (model/llama-server) on the host — OpenAI /v1/chat/completions.
    Model id is fixed (llama-server serves the single loaded GGUF and accepts any id)."""
    def __init__(self, url: str = "http://host.docker.internal:8088",
                 api_key: str | None = None) -> None:
        self._url = url
        self._key = api_key
        self._model = "qwen3-8b-instruct"
        self._defaults = {"temperature": 0.1, "max_tokens": 512}

    def chat(self, messages: list[dict], response_format: dict | None, **opts) -> str:
        params = {**self._defaults, **{k: v for k, v in opts.items() if v is not None}}
        body: dict = {"model": self._model, "messages": messages, "stream": False, **params}
        if response_format is not None:
            body["response_format"] = response_format
        data = _post(self._url, "/v1/chat/completions", body, self._key)
        return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""

    def generate(self, prompt: str, system_prompt: str = "",
                 response_format: dict | None = None, **opts) -> str:
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages, response_format, **opts)
