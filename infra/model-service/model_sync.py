"""
Sync all embedding models from KBModel into the in-process model store.

On every successful DB connection:
  - Queries all KBModel rows with task_type='embedding'
  - Calls models.ensure_loaded(model_name) for each — no-ops for already-loaded models
  - Ensures each has at least one active KBModelVersion
  - Builds a model_id (UUID) → model_name lookup used by the /embed endpoint

Retries with backoff forever — Postgres container may start after this service.
"""
import asyncio
import logging
import uuid as _uuid

import asyncpg

import config as cfg
import models

log = logging.getLogger(__name__)

_DELAYS = [2, 5, 10, 20, 30, 60]

# model_id UUID → model_name str; populated after first successful DB sync
_id_to_name: dict[_uuid.UUID, str] = {}


def get_model_name_for_id(model_id: _uuid.UUID) -> str | None:
    """Return the model_name for a given KBModel.model_id, or None if unknown."""
    return _id_to_name.get(model_id)


def get_model_id_for_name(model_name: str) -> _uuid.UUID | None:
    """Return the KBModel.model_id for a given model_name, or None if unknown."""
    for uid, name in _id_to_name.items():
        if name == model_name:
            return uid
    return None


async def _ensure_active_version(conn, model_id: _uuid.UUID, model_name: str) -> None:
    active = await conn.fetchval(
        'SELECT version_id FROM "KBModelVersion" WHERE model_id = $1 AND is_active = true',
        model_id,
    )
    if active is not None:
        return

    seeded = await conn.fetchrow(
        'SELECT version_id, version_number FROM "KBModelVersion" '
        'WHERE model_id = $1 ORDER BY version_number LIMIT 1',
        model_id,
    )
    if seeded:
        await conn.execute(
            'UPDATE "KBModelVersion" SET is_active = true WHERE version_id = $1',
            seeded["version_id"],
        )
        log.info("Activated version %d for '%s' (model_id=%s)", seeded["version_number"], model_name, model_id)
    else:
        await conn.execute(
            'INSERT INTO "KBModelVersion" (model_id, version_number, is_active, config, added_on) '
            'VALUES ($1, 1, true, $2, NOW())',
            model_id, '{"active": true}',
        )
        log.info("Inserted active version 1 for '%s' (model_id=%s)", model_name, model_id)


async def _sync(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            'SELECT model_id, model_name FROM "KBModel" '
            "WHERE task_type = 'embedding' AND is_deleted = false",
        )
        if not rows:
            log.warning("No embedding models found in KBModel — ensure init.sql seed ran")
            return

        remote_embed = bool(cfg.REMOTE_EMBED_URL)
        for row in rows:
            model_id: _uuid.UUID = row["model_id"]
            model_name: str = row["model_name"]

            if not remote_embed:
                models.ensure_loaded(model_name)
            _id_to_name[model_id] = model_name
            await _ensure_active_version(conn, model_id, model_name)

        log.info(
            "Registry sync complete — %d embedding model(s) loaded: %s",
            len(_id_to_name),
            [f"{n} ({i})" for i, n in _id_to_name.items()],
        )
    finally:
        await conn.close()


async def register_with_retry() -> None:
    """Background task: retries indefinitely until all models are synced."""
    delays = _DELAYS + [60] * 9999
    for attempt, delay in enumerate(delays, start=1):
        try:
            await _sync(cfg.POSTGRES_DSN)
            return
        except Exception as exc:
            log.warning(
                "Registry sync attempt %d failed (%s) — retrying in %ds",
                attempt, exc, delay,
            )
            await asyncio.sleep(delay)
