"""Capability registry.

Holds the candidate backends for each capability (chat / embed / rerank). The
ACTIVE backend is the FIRST registered one whose `available()` is True, so
registration order is preference order. Callers ask for `active_*()` and operate
on whatever they get — no env checks, no knowledge of local vs remote."""
from __future__ import annotations

from .base import ChatBackend, EmbedBackend, RerankBackend


class Registry:
    def __init__(self) -> None:
        self._chat: list[ChatBackend] = []
        self._embed: list[EmbedBackend] = []
        self._rerank: list[RerankBackend] = []

    def register(self, *, chat: ChatBackend | None = None,
                 embed: EmbedBackend | None = None,
                 rerank: RerankBackend | None = None) -> None:
        if chat is not None:
            self._chat.append(chat)
        if embed is not None:
            self._embed.append(embed)
        if rerank is not None:
            self._rerank.append(rerank)

    def active_chat(self) -> ChatBackend | None:
        return next((b for b in self._chat if b.available()), None)

    def active_embed(self) -> EmbedBackend | None:
        return next((b for b in self._embed if b.available()), None)

    def active_rerank(self) -> RerankBackend | None:
        return next((b for b in self._rerank if b.available()), None)
