"""Local backends — serve a capability from an in-process model on this box.

Embed/rerank wrap the loaded SentenceTransformer / CrossEncoder (models.py).
Chat is a registered-but-unavailable placeholder: the design is OPEN for a local
LLM, but today the only chat backend is remote (Colab vLLM). Implementing local
chat = make `available()` return True and fill in `chat()`."""
from __future__ import annotations

import uuid as _uuid

import config as cfg
import model_sync
import models


class LocalChatBackend:
    name = "local-llm"

    def available(self) -> bool:
        return False  # no in-process LLM yet — see module docstring

    def chat(self, messages: list[dict], response_format: dict | None,
             max_tokens: int, temperature: float) -> str:
        raise NotImplementedError("local LLM backend not implemented")


class LocalEmbedBackend:
    name = "local-embed"

    def available(self) -> bool:
        return models.is_ready()

    def embed(self, texts: list[str], model_id: str | None) -> tuple[list[list[float]], str | None]:
        if model_id:
            try:
                requested = _uuid.UUID(model_id)
            except ValueError:
                raise ValueError(f"model_id '{model_id}' is not a valid UUID")
            model_name = model_sync.get_model_name_for_id(requested)
            if model_name is None:
                raise LookupError(
                    f"model_id {requested} not in registry — "
                    "DB sync not yet complete or model not seeded in KBModel"
                )
            embed_model = models.get_embed_for_name(model_name)
            if embed_model is None:
                raise RuntimeError(
                    f"Model '{model_name}' registered but not yet loaded — retry shortly"
                )
            used_id = model_id
        else:
            embed_model = models.get_embed()
            own_uuid = model_sync.get_model_id_for_name(cfg.EMBED_MODEL)
            used_id = str(own_uuid) if own_uuid else None

        if not texts:
            return [], used_id
        return embed_model.encode(texts, normalize_embeddings=True).tolist(), used_id


class LocalRerankBackend:
    name = "local-rerank"

    def available(self) -> bool:
        return models.is_ready()

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        if not documents:
            return []
        pairs = [(query, doc) for doc in documents]
        scores = models.get_rerank().predict(pairs).tolist()
        ranked = sorted(
            ((i, float(s)) for i, s in enumerate(scores)),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:min(top_n, len(ranked))]
