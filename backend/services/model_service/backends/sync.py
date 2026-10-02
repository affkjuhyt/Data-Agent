from __future__ import annotations

from typing import Any, cast
import asyncio
import logging
import os
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import config as cfg

from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBModelORM,
)

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


def _probe_tei(url: str, api_key: str | None) -> str | None:
    """TEI (text-embeddings-inference) answers GET /info with {"model_id": ...}.
    Returns the model_id when the endpoint is TEI, None otherwise — the legacy
    remote shim (Colab-era /embed {"texts"} shape) has no /info route."""
    try:
        info = C._get(url, "/info", api_key, timeout=5)
        return cast("str | None", info.get("model_id"))
    except Exception:  # noqa: BLE001 — any failure just means "not TEI"
        return None


def _discover_name(url: str, api_key: str | None, prefer: str = "id") -> str | None:
    try:
        model = C._get(url, "/v1/models", api_key, timeout=5)["data"][0]
        return cast("str | None", model.get(prefer) or model.get("id"))
    except Exception as e:  # noqa: BLE001
        log.warning("Registry: /v1/models discovery failed for %s (%s)", url, e)
        return None


def register_embedding_models(registry: Registry) -> None:
    """One model per remote TEI /embed URL — no in-process fallback since the
    platform moved to the GPU server (REMOTE_EMBED_URL is required for /embed)."""
    for i, (url, key) in enumerate(_endpoints("REMOTE_EMBED_URL", "REMOTE_API_KEY")):
        tei_model = _probe_tei(url, key)
        if not tei_model:
            log.error("Registry: %s does not answer GET /info — not a TEI endpoint, skipping", url)
            continue
        registry.register(ModelEntry(
            name=tei_model,
            capability=Capability.EMBED,
            caller=C.TeiEmbedCaller(url, key),
        ), default=(i == 0))
    if not registry.has(Capability.EMBED):
        log.error("Registry: no embedding backend — set REMOTE_EMBED_URL to a TEI endpoint; "
                  "/embed will return 503 until then")


def register_rerank_models(registry: Registry) -> None:
    """One model per remote TEI /rerank URL — no in-process cross-encoder any more."""
    for i, (url, key) in enumerate(_endpoints("REMOTE_RERANK_URL", "REMOTE_API_KEY")):
        tei_model = _probe_tei(url, key)
        if not tei_model:
            log.error("Registry: %s does not answer GET /info — not a TEI endpoint, skipping", url)
            continue
        registry.register(ModelEntry(
            name=tei_model,
            capability=Capability.RERANK,
            caller=C.TeiRerankCaller(url, key),
        ), default=(i == 0))
    if not registry.has(Capability.RERANK):
        log.error("Registry: no rerank backend — set REMOTE_RERANK_URL to a TEI endpoint; "
                  "/rerank will return 503 until then")


def register_chat_models(registry: Registry) -> None:
    """One model per remote OpenAI-compatible LLM URL (vLLM on the GPU server);
    looked up by name. No local llama-server fallback any more."""
    for i, (url, key) in enumerate(_endpoints("REMOTE_LLM_URL", "REMOTE_LLM_KEY")):
        # Pin the model when REMOTE_LLM_MODEL is set (a multi-model server like
        # ollama otherwise resolves to /v1/models[0] non-deterministically).
        model = cfg.REMOTE_LLM_MODEL
        name = model or _discover_name(url, key) or f"remote-chat-{i}"
        registry.register(ModelEntry(
            name=name,
            capability=Capability.CHAT,
            caller=C.CloudflareQwenChatCaller(url, key, model),
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
def _async_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    if dsn.startswith("postgresql://"):
        return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    return dsn


async def _resolve_or_create(session: AsyncSession, model_name: str, task_type: str) -> _uuid.UUID:
    found = await session.scalar(
        select(KBModelORM.model_id).where(
            KBModelORM.model_name == model_name,
            KBModelORM.task_type == task_type,
            KBModelORM.is_deleted.is_(False),
        )
    )
    if found is not None:
        return found
    new_id = _uuid.uuid4()
    session.add(
        KBModelORM(
            model_id=new_id,
            model_name=model_name,
            task_type=task_type,
        )
    )
    await session.flush()
    log.info("Registry: created KBModel '%s' (%s) → %s", model_name, task_type, new_id)
    return new_id


async def _sync_ids(registry: Registry, dsn: str) -> None:
    """For every registered embed/rerank model, resolve its KBModel UUID by name"""
    engine = create_async_engine(_async_dsn(dsn), pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as session:
            async with session.begin():
                for cap, task in _TASK_BY_CAP.items():
                    for entry in registry.entries(cap):
                        model_id = await _resolve_or_create(session, entry.name, task)
                        registry.bind_id(entry, model_id)
    finally:
        await engine.dispose()


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


def chat_status(registry: Registry) -> dict[str, Any]:
    entry = registry.default(Capability.CHAT)
    if entry is None:
        return {"configured": False, "ready": False, "model": None}
    return {"configured": True, "ready": True, "model": entry.name}
