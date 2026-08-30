"""model_service backends package.

`registry` is the single process-wide model registry. Routers look an entry up
(by UUID for embed/rerank, by name for chat) and call `entry.caller.<capability>()`;
the caller — a concrete class in callers.py, registered explicitly in sync — knows
how to reach that specific model (in-process vs HTTP, and which endpoint shape).

Lifecycle (driven by app.py):
  bootstrap(registry)        — sync, no DB: register default embed/rerank + chat.
  sync_with_retry(registry)  — background: pull embed/rerank from Postgres (KBModel).
  chat_status(registry)      — LLM-gateway status for /health.
"""
from .registry import Capability, ModelEntry, Registry
from .sync import bootstrap, sync_with_retry, chat_status

registry = Registry()

__all__ = [
    "registry",
    "Registry",
    "ModelEntry",
    "Capability",
    "bootstrap",
    "sync_with_retry",
    "chat_status",
]
