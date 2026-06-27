"""Populate the model registry at startup.

  bootstrap(registry)       — synchronous, NO network/DB beyond loading local weights.
                              Registers the default embed + rerank and all chat models.
  sync_with_retry(registry) — background. Pulls every embed/rerank model from Postgres
                              (KBModel) so their DB UUIDs resolve; retries forever.

Embed/rerank live in Postgres and are looked up by UUID; their caller is local
(in-process) unless a REMOTE_*_URL is set for that capability. Chat models are NOT in
the DB and are declared explicitly in `register_chat_models` — one registry.register
per endpoint, each with its own caller (a Cloudflare/vLLM endpoint with a different
payload just uses a different caller class). Chat is looked up by name."""
from __future__ import annotations

import asyncio
import logging
import uuid as _uuid

import asyncpg

import config as cfg
import model_store

from . import callers as C
from .registry import Capability, ModelEntry, Registry

log = logging.getLogger(__name__)

_DELAYS = [2, 5, 10, 20, 30, 60]
_CAP_BY_TASK = {"embedding": Capability.EMBED, "rerank": Capability.RERANK}


# ── embed / rerank (from Postgres; caller = local handle, or remote if a URL is set) ──
def _register_embed(registry: Registry, name: str, model_id: _uuid.UUID | None, *, default: bool = False) -> None:
    if cfg.REMOTE_EMBED_URL:
        caller = C.RemoteEmbedCaller(cfg.REMOTE_EMBED_URL, cfg.REMOTE_API_KEY)
    else:
        caller = C.LocalEmbedCaller(model_store.load_embed(name))
    registry.register(ModelEntry(name=name, capability=Capability.EMBED, caller=caller,
                                 model_id=model_id), default=default)


def _register_rerank(registry: Registry, name: str, model_id: _uuid.UUID | None, *, default: bool = False) -> None:
    if cfg.REMOTE_RERANK_URL:
        caller = C.RemoteRerankCaller(cfg.REMOTE_RERANK_URL, cfg.REMOTE_API_KEY)
    else:
        caller = C.LocalRerankCaller(model_store.load_rerank(name))
    registry.register(ModelEntry(name=name, capability=Capability.RERANK, caller=caller,
                                 model_id=model_id), default=default)


# ── chat (declared explicitly — add/remove an endpoint by editing this function) ──────
def register_chat_models(registry: Registry) -> None:
    """Declare every chat endpoint here, one registry.register per model. Chat models
    are not stored in the DB. Each gets its own caller; an endpoint with a different
    payload shape uses a different caller class (see callers.CloudflareChatCaller).
    Mark exactly one default=True — used when /generate omits `model`."""

    # Local llama-server (model/llama-server) on the host — OpenAI-compatible.
    registry.register(ModelEntry(
        name="qwen3-8b-instruct",
        capability=Capability.CHAT,
        caller=C.OpenAIChatCaller(
            url="http://host.docker.internal:8088",
            model="qwen3-8b-instruct",
            defaults={"temperature": 0.1, "max_tokens": 512},
        ),
    ), default=True)

    # ── add more endpoints here, e.g. a Colab/Cloudflare vLLM tunnel ──
    # registry.register(ModelEntry(
    #     name="qwen2.5-72b",
    #     capability=Capability.CHAT,
    #     caller=C.CloudflareChatCaller(
    #         url="https://<tunnel>.trycloudflare.com", model="qwen2.5-72b",
    #         api_key="<key>", defaults={"temperature": 0.2, "max_tokens": 1024},
    #     ),
    # ))


def bootstrap(registry: Registry) -> None:
    """Synchronous registration of the defaults so the service is ready at boot
    (loads local embed/rerank weights here unless a REMOTE_*_URL is configured)."""
    _register_embed(registry, cfg.EMBED_MODEL, None, default=True)
    _register_rerank(registry, cfg.RERANK_MODEL, None, default=True)
    register_chat_models(registry)
    log.info("Registry bootstrap complete (embed=%s, rerank=%s, chat=%s)",
             cfg.EMBED_MODEL, cfg.RERANK_MODEL, registry.names(Capability.CHAT))


# ── Postgres sync ─────────────────────────────────────────────────────────────────
async def _ensure_active_version(conn, model_id: _uuid.UUID, model_name: str) -> None:
    """Guarantee the model has an active KBModelVersion. version_id is an autoincrement
    int and version_number tracks the revision — activate the earliest seeded version if
    present, else insert version_number=1."""
    active = await conn.fetchval(
        'SELECT version_id FROM "KBModelVersion" WHERE model_id = $1 AND is_active = true',
        model_id,
    )
    if active is not None:
        return
    seeded = await conn.fetchrow(
        'SELECT version_id, version_number FROM "KBModelVersion" '
        "WHERE model_id = $1 ORDER BY version_number LIMIT 1",
        model_id,
    )
    if seeded:
        await conn.execute(
            'UPDATE "KBModelVersion" SET is_active = true WHERE version_id = $1',
            seeded["version_id"],
        )
        log.info("Registry: activated version %d for '%s'", seeded["version_number"], model_name)
    else:
        await conn.execute(
            'INSERT INTO "KBModelVersion" (model_id, version_number, is_active, config, added_on) '
            "VALUES ($1, 1, true, $2, NOW())",
            model_id, '{"active": true}',
        )
        log.info("Registry: inserted active version 1 for '%s'", model_name)


async def _sync_db(registry: Registry, dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            'SELECT model_id, model_name, task_type FROM "KBModel" '
            "WHERE is_deleted = false AND task_type IN ('embedding', 'rerank')",
        )
        for row in rows:
            cap = _CAP_BY_TASK.get(row["task_type"])
            is_default = row["model_name"] == cfg.EMBED_MODEL
            if cap is Capability.EMBED:
                _register_embed(registry, row["model_name"], row["model_id"], default=is_default)
            elif cap is Capability.RERANK:
                _register_rerank(registry, row["model_name"], row["model_id"])
            else:
                continue
            await _ensure_active_version(conn, row["model_id"], row["model_name"])
        log.info("Registry: synced %d embed/rerank model(s) from KBModel", len(rows))
    finally:
        await conn.close()


async def sync_with_retry(registry: Registry) -> None:
    """Background task: sync DB models into the registry, retrying until it succeeds."""
    delays = _DELAYS + [60] * 9999
    for attempt, delay in enumerate(delays, start=1):
        try:
            await _sync_db(registry, cfg.POSTGRES_DSN)
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("Registry sync attempt %d failed (%s) — retrying in %ds", attempt, exc, delay)
            await asyncio.sleep(delay)


def chat_status(registry: Registry) -> dict:
    """LLM-gateway status for /health, derived from registry state (no network call)."""
    entry = registry.default(Capability.CHAT)
    if entry is None:
        return {"configured": False, "ready": False, "model": None}
    return {"configured": True, "ready": True, "model": entry.name}
