from __future__ import annotations

import asyncio
import logging
import os
import uuid as _uuid

import asyncpg

import config as cfg
import model_store

from . import callers as C
from .registry import Capability, ModelEntry, Registry

log = logging.getLogger(__name__)

_DELAYS = [2, 5, 10, 20, 30, 60]
_TASK_BY_CAP = {Capability.EMBED: "embedding", Capability.RERANK: "rerank"}

def _endpoints(url_env: str, key_env: str) -> list[tuple[str, str | None]]:
    urls = [u.strip().rstrip("/") for u in os.getenv(url_env, "").split(",")]
    keys = [k.strip() for k in os.getenv(key_env, "").split(",")]
    pairs: list[tuple[str, str | None]] = []
    for i, url in enumerate(urls):
        if not url:
            continue
        key = keys[i] if i < len(keys) else ""
        pairs.append((url, key or None))
    return pairs


def _discover_name(url: str, api_key: str | None, prefer: str = "id") -> str | None:
    try:
        model = C._get(url, "/v1/models", api_key, timeout=5)["data"][0]
        return model.get(prefer) or model.get("id")
    except Exception as e:  # noqa: BLE001
        log.warning("Registry: /v1/models discovery failed for %s (%s)", url, e)
        return None


def register_embedding_models(registry: Registry) -> None:
    """In-process e5-small (default until a remote is added) + one model per remote /embed URL."""
    registry.register(ModelEntry(
        name="intfloat/multilingual-e5-small",
        capability=Capability.EMBED,
        caller=C.MultilingualE5SmallCaller(model_store.load_embed("intfloat/multilingual-e5-small")),
    ))
    for i, (url, key) in enumerate(_endpoints("REMOTE_EMBED_URL", "REMOTE_API_KEY")):
        name = _discover_name(url, key) or f"remote-embed-{i}"
        registry.register(ModelEntry(
            name=name,
            capability=Capability.EMBED,
            caller=C.VietnameseEmbedCaller(url, key),
        ), default=(i == 0))


def register_rerank_models(registry: Registry) -> None:
    """One model per remote /rerank URL."""
    for i, (url, key) in enumerate(_endpoints("REMOTE_RERANK_URL", "REMOTE_API_KEY")):
        name = _discover_name(url, key, prefer="rerank") or f"remote-rerank-{i}"
        registry.register(ModelEntry(
            name=name,
            capability=Capability.RERANK,
            caller=C.VietnameseRerankCaller(url, key),
        ), default=(i == 0))


def register_chat_models(registry: Registry) -> None:
    """Local llama-server (default until a remote is added) + one model per remote LLM URL.
    All are OpenAI /v1/chat/completions; looked up by name."""
    registry.register(ModelEntry(
        name="llama-server-qwen3",
        capability=Capability.CHAT,
        caller=C.LlamaServerChatCaller(),
    ))
    for i, (url, key) in enumerate(_endpoints("REMOTE_LLM_URL", "REMOTE_LLM_KEY")):
        name = _discover_name(url, key) or f"remote-chat-{i}"
        registry.register(ModelEntry(
            name=name,
            capability=Capability.CHAT,
            caller=C.CloudflareQwenChatCaller(url, key),
        ), default=(i == 0))


def bootstrap(registry: Registry) -> None:
    """Synchronous registration of every model (loads in-process embedder weights here)."""
    register_embedding_models(registry)
    register_rerank_models(registry)
    register_chat_models(registry)
    log.info("Registry bootstrap complete — embed=%s rerank=%s chat=%s",
             registry.names(Capability.EMBED), registry.names(Capability.RERANK),
             registry.names(Capability.CHAT))


# ── Postgres: resolve each embed/rerank model's UUID by name (create if missing) ──
async def _resolve_or_create(conn, model_name: str, task_type: str) -> _uuid.UUID:
    found = await conn.fetchval(
        'SELECT model_id FROM "KBModel" WHERE model_name = $1 AND task_type = $2 AND is_deleted = false',
        model_name, task_type,
    )
    if found is not None:
        return found
    new_id = _uuid.uuid4()
    async with conn.transaction():
        await conn.execute(
            'INSERT INTO "KBModel" (model_id, model_name, task_type) VALUES ($1, $2, $3)',
            new_id, model_name, task_type,
        )
        await conn.execute(
            'INSERT INTO "KBModelVersion" (model_id, version_number, is_active, config, added_on) '
            "VALUES ($1, 1, true, $2, NOW())",
            new_id, '{"active": true}',
        )
    log.info("Registry: created KBModel '%s' (%s) + v1 → %s", model_name, task_type, new_id)
    return new_id


async def _sync_ids(registry: Registry, dsn: str) -> None:
    """For every registered embed/rerank model, resolve its KBModel UUID by name"""
    conn = await asyncpg.connect(dsn)
    try:
        for cap, task in _TASK_BY_CAP.items():
            for entry in registry.entries(cap):
                model_id = await _resolve_or_create(conn, entry.name, task)
                registry.bind_id(entry, model_id)
    finally:
        await conn.close()


async def sync_with_retry(registry: Registry) -> None:
    delays = _DELAYS + [60] * 9999
    for attempt, delay in enumerate(delays, start=1):
        try:
            await _sync_ids(registry, cfg.POSTGRES_DSN)
            return
        except Exception as exc:
            log.warning("Registry version sync attempt %d failed (%s) — retrying in %ds",
                        attempt, exc, delay)
            await asyncio.sleep(delay)


def chat_status(registry: Registry) -> dict:
    entry = registry.default(Capability.CHAT)
    if entry is None:
        return {"configured": False, "ready": False, "model": None}
    return {"configured": True, "ready": True, "model": entry.name}
