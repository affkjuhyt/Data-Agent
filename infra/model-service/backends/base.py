"""Backend capability protocols.

A *backend* serves one capability — chat, embed, or rerank. Several backends may
be registered for the same capability (e.g. a remote Colab GPU and a local CPU
model); the registry (registry.py) picks the ACTIVE one at call time. Callers
(the routers) only ever talk to the active backend and never know whether the
work runs locally or is forwarded to a remote server.

Adding a new way to serve a capability = implement the matching protocol and
register an instance in backends/__init__.py.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ChatBackend(Protocol):
    name: str

    def available(self) -> bool:
        """True when this backend is configured and able to serve right now."""
        ...

    def chat(self, messages: list[dict], response_format: dict | None,
             max_tokens: int, temperature: float) -> str:
        """Return the assistant content string for an OpenAI-style chat request."""
        ...


@runtime_checkable
class EmbedBackend(Protocol):
    name: str

    def available(self) -> bool:
        ...

    def embed(self, texts: list[str], model_id: str | None) -> tuple[list[list[float]], str | None]:
        """Return (embeddings, model_id_used). model_id selects a specific model
        when the backend supports it (remote backends may ignore it)."""
        ...


@runtime_checkable
class RerankBackend(Protocol):
    name: str

    def available(self) -> bool:
        ...

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        """Return up to top_n (document_index, relevance_score) pairs, best first."""
        ...
