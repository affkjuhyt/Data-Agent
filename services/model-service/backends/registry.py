"""The model registry — one entry per usable model, the single source of truth
the routers consult.

Entries are registered at startup by `sync`:
  • embedding + rerank models come from Postgres (KBModel), keyed by DB UUID
    (`model_id`) — callers pass that UUID.
  • chat models are declared explicitly in `sync.register_chat_models`, keyed by
    NAME — callers pass the model name (chat models are not stored in KBModel).

An entry is just the lookup keys + the model's `caller` (a concrete class in
callers.py that satisfies the capability's protocol). The caller — not the entry —
holds how to reach the model, so there is no per-call dispatch on local/remote."""
from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from enum import Enum

from .base import ChatCaller, EmbedCaller, RerankCaller


class Capability(str, Enum):
    EMBED = "embed"
    RERANK = "rerank"
    CHAT = "chat"


@dataclass
class ModelEntry:
    name: str
    capability: Capability
    caller: EmbedCaller | RerankCaller | ChatCaller
    model_id: _uuid.UUID | None = None


class Registry:
    def __init__(self) -> None:
        self._by_id: dict[_uuid.UUID, ModelEntry] = {}
        self._by_name: dict[str, ModelEntry] = {}
        self._default: dict[Capability, str] = {}

    def register(self, entry: ModelEntry, *, default: bool = False) -> None:
        self._by_name[entry.name] = entry
        if entry.model_id is not None:
            self._by_id[entry.model_id] = entry
        if default or entry.capability not in self._default:
            self._default[entry.capability] = entry.name

    def by_id(self, model_id: _uuid.UUID | str) -> ModelEntry | None:
        if isinstance(model_id, str):
            try:
                model_id = _uuid.UUID(model_id)
            except (ValueError, TypeError):
                return None
        return self._by_id.get(model_id)

    def by_name(self, name: str) -> ModelEntry | None:
        return self._by_name.get(name)

    def default(self, capability: Capability) -> ModelEntry | None:
        name = self._default.get(capability)
        return self._by_name.get(name) if name else None

    def has(self, capability: Capability) -> bool:
        return capability in self._default

    def names(self, capability: Capability | None = None) -> list[str]:
        return [e.name for e in self._by_name.values()
                if capability is None or e.capability == capability]

    def entries(self, capability: Capability | None = None) -> list[ModelEntry]:
        return [e for e in self._by_name.values()
                if capability is None or e.capability == capability]

    def bind_id(self, entry: ModelEntry, model_id: _uuid.UUID) -> None:
        entry.model_id = model_id
        self._by_id[model_id] = entry
