"""Caller protocols — one per capability.

A *caller* fully encapsulates HOW to reach one model: the endpoint + payload shape +
auth for a remote, or an in-process handle for a local model. The registry just stores
it; routers depend only on these method signatures and never know the implementation.

The point of the protocols is that every model of a given capability is interchangeable
to the caller — but the implementations can differ wildly. Two chat endpoints may both
satisfy `ChatCaller` while POSTing different JSON bodies (e.g. a Cloudflare/vLLM tunnel
whose `/v1/chat/completions` payload differs from a llama-server's). You write a new
caller class per such model in callers.py; nothing else changes.

Protocol per capability:
  embed  → embed(texts)
  rerank → rerank(query, documents, top_n)
  chat   → chat(messages, ...)  and  generate(prompt, ...)
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbedCaller(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


@runtime_checkable
class RerankCaller(Protocol):
    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        ...


@runtime_checkable
class ChatCaller(Protocol):
    def chat(self, messages: list[dict], response_format: dict | None, **opts) -> str:
        """Chat-completion from a full messages list."""
        ...

    def generate(self, prompt: str, system_prompt: str, response_format: dict | None, **opts) -> str:
        """Convenience: build a (system?, user) messages list from a raw prompt and chat."""
        ...
