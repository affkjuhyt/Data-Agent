"""Remote backends — forward a capability to an OpenAI/GPU server reachable over
HTTP (e.g. a Colab notebook behind a cloudflared tunnel). Each is `available()`
only when its REMOTE_*_URL is configured, so setting/clearing one env var flips
the capability between remote and local."""
from __future__ import annotations

import config as cfg
import http_proxy


class RemoteChatBackend:
    name = "remote-llm"

    def __init__(self) -> None:
        # The served LLM model id is required by OpenAI /v1/chat/completions.
        # Pinned via REMOTE_LLM_MODEL or discovered once from /v1/models and cached.
        self._model_id: str = ""

    def available(self) -> bool:
        return bool(cfg.REMOTE_LLM_URL)

    def chat(self, messages: list[dict], response_format: dict | None,
             max_tokens: int, temperature: float) -> str:
        base = cfg.REMOTE_LLM_URL
        if not self._model_id:
            self._model_id = cfg.REMOTE_LLM_MODEL or \
                http_proxy.get(base, "/v1/models", cfg.REMOTE_LLM_KEY)["data"][0]["id"]
        payload: dict = {
            "model": self._model_id, "messages": messages,
            "max_tokens": max_tokens, "temperature": temperature, "stream": False,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        data = http_proxy.post(base, "/v1/chat/completions", payload, api_key=cfg.REMOTE_LLM_KEY)
        return (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""


class RemoteEmbedBackend:
    name = "remote-embed"

    def available(self) -> bool:
        return bool(cfg.REMOTE_EMBED_URL)

    def embed(self, texts: list[str], model_id: str | None) -> tuple[list[list[float]], str | None]:
        # The remote GPU model owns the embedding; model_id is echoed back unchanged.
        if not texts:
            return [], model_id
        data = http_proxy.post(cfg.REMOTE_EMBED_URL, "/embed", {"texts": texts})
        return data["embeddings"], model_id


class RemoteRerankBackend:
    name = "remote-rerank"

    def available(self) -> bool:
        return bool(cfg.REMOTE_RERANK_URL)

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        if not documents:
            return []
        data = http_proxy.post(cfg.REMOTE_RERANK_URL, "/rerank",
                               {"query": query, "documents": documents, "top_n": top_n})
        return [(r["index"], r["relevance_score"]) for r in data.get("results", [])]
