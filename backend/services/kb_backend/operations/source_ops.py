"""CRUD + run-history for the scheduled source registry ("KBSource" /
"KBSourceConfig" / "KBSourceRun") — mirrors warehouse_ops.py's shape
(KBWarehouse/KBWarehouseConfig) since a source is a versioned-config entity
of the same kind, just fetched by a URL+cron instead of a DB connection.
"""
from __future__ import annotations

import base64
import datetime
import uuid
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import func as sa_func
from sqlalchemy import or_ as sa_or
from sqlalchemy import select as sa_select

from libs.shared.cron import next_run_after

from libs.basemodel.services_databaseconnector.postgres_model import (
    KBSourceConfigInsert,
    KBSourceDelete,
    KBSourceInsert,
    SourceConfigPayload,
)
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBSourceConfigORM,
    KBSourceORM,
    KBSourceRunORM,
)
from services.kb_backend.operations.pagination import PAGE_SIZE, build_cursor_page

_VALID_CONNECTOR_TYPES = {"http_file", "web_crawl", "web_crawl_headless", "rss", "rest_api", "sdmx"}

_ACTIVE_RUN_STATUSES = ("queued", "running")
# A run that has been queued or running this long is a worker that died, not a
# slow crawl (the worker marks such rows failed too); it must not lock the
# source forever.
_STALE_RUN_AFTER = datetime.timedelta(minutes=30)
_RECENT_FINISH_WINDOW = datetime.timedelta(minutes=2)


def _decode_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(base64.urlsafe_b64decode(cursor.encode()).decode()))
    except (ValueError, TypeError):
        return 0


def _next_run_at(cron: str | None, last_started: datetime.datetime | None) -> str | None:
    """When the worker will next start this source on its own: the next cron
    occurrence after its last run. None for a manual-only source, and for one
    that has not started yet (a schedule begins with the first run)."""
    if not cron or not cron.strip() or last_started is None:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    if last_started.tzinfo is None:
        last_started = last_started.replace(tzinfo=datetime.timezone.utc)
    following = next_run_after(cron, last_started)
    return max(following, now).isoformat() if following else None


def _to_response(row: dict[str, Any], active_cfg: dict[str, Any] | None, last_run: dict[str, Any] | None) -> dict[str, Any]:
    """`row` is always a plain dict (never a live ORM instance) — see
    `_source_row`'s docstring for why."""
    cfg = active_cfg or {}
    last_run_started = (last_run or {}).get("started_at")
    created_at = row.get("created_at")
    return {
        "next_run_at": _next_run_at(cfg.get("cron"), last_run_started),
        "id": str(row["source_id"]),
        "title": row["title"],
        "publisher": row["publisher"],
        "connector_type": row["connector_type"],
        "url": cfg.get("url"),
        "cron": cfg.get("cron"),
        "render": bool(cfg.get("render", False)),
        "openbao_path": cfg.get("openbao_path"),
        "query_params": cfg.get("query_params"),
        "auth_style": cfg.get("auth_style"),
        "auth_param_name": cfg.get("auth_param_name"),
        "recursive": bool(cfg.get("recursive", False)),
        "max_depth": cfg.get("max_depth", 1),
        "same_domain_only": bool(cfg.get("same_domain_only", True)),
        "promotion_mode": cfg.get("promotion_mode", "manual"),
        "created_by": row["created_by"],
        "created_at": created_at.isoformat() if created_at else "",
        "last_run_status": (last_run or {}).get("status"),
        "last_run_at": last_run_started.isoformat() if last_run_started else None,
    }


async def list_sources(postgres: Any, tenant_id: str, cursor: str | None = None) -> dict[str, Any]:
    offset = _decode_offset(cursor)
    tenant_uuid = uuid.UUID(tenant_id)
    async with postgres.get_client() as session:
        total = await session.scalar(
            sa_select(sa_func.count()).select_from(KBSourceORM)
            .where(KBSourceORM.tenant_id == tenant_uuid, KBSourceORM.is_deleted == False)  # noqa: E712
        ) or 0
        rows = (await session.execute(
            sa_select(KBSourceORM)
            .where(KBSourceORM.tenant_id == tenant_uuid, KBSourceORM.is_deleted == False)  # noqa: E712
            .order_by(KBSourceORM.inserted_at.desc(), KBSourceORM.source_id.desc())
            .offset(offset).limit(PAGE_SIZE)
        )).scalars().all()

        source_ids = [r.source_id for r in rows]
        active_cfgs: dict[str, dict[str, Any]] = {}
        last_runs: dict[str, dict[str, Any]] = {}
        if source_ids:
            crows = (await session.execute(
                sa_select(KBSourceConfigORM)
                .where(KBSourceConfigORM.source_id.in_(source_ids), KBSourceConfigORM.is_active == True)  # noqa: E712
            )).scalars().all()
            active_cfgs = {str(c.source_id): (c.config or {}) for c in crows}

            # One run per source, most recent — a window function keeps this
            # a single query instead of N, same "no N+1" motivation as
            # schema_crawler's _last_crawled_by_warehouse.
            rrows = (await session.execute(
                sa_select(KBSourceRunORM)
                .where(KBSourceRunORM.source_id.in_(source_ids))
                .order_by(KBSourceRunORM.source_id, KBSourceRunORM.started_at.desc())
            )).scalars().all()
            for r in rrows:
                sid = str(r.source_id)
                if sid not in last_runs:
                    last_runs[sid] = {"status": r.status, "started_at": r.started_at}

        # Extract to plain dicts while the rows are still attached — reading
        # an attribute after this `async with` closes raises
        # DetachedInstanceError (see _source_row's docstring).
        row_dicts = [
            {
                "source_id": r.source_id, "title": r.title, "publisher": r.publisher,
                "connector_type": r.connector_type, "created_by": r.created_by, "created_at": r.created_at,
            }
            for r in rows
        ]

    docs = [_to_response(rd, active_cfgs.get(str(rd["source_id"])), last_runs.get(str(rd["source_id"]))) for rd in row_dicts]
    has_next = offset + PAGE_SIZE < total
    next_cursor = base64.urlsafe_b64encode(str(offset + PAGE_SIZE).encode()).decode() if has_next else None
    return build_cursor_page(docs, total, has_next, next_cursor)


async def _source_row(postgres: Any, source_id: str, tenant_id: str) -> dict[str, Any]:
    """Returns a plain dict, not the ORM instance: the session that loaded it
    closes at the end of this function's `async with` block, and reading an
    attribute off a detached instance afterward raises DetachedInstanceError
    (SQLAlchemy expires attributes on session close) — found by actually
    calling create_source end-to-end, not by the earlier empty-table smoke
    test, which never touched a real row's attributes."""
    async with postgres.get_client() as session:
        row = await session.scalar(
            sa_select(KBSourceORM).where(
                KBSourceORM.source_id == uuid.UUID(source_id),
                KBSourceORM.tenant_id == uuid.UUID(tenant_id),
                KBSourceORM.is_deleted == False,  # noqa: E712
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Source not found")
        return {
            "source_id": row.source_id, "title": row.title, "publisher": row.publisher,
            "connector_type": row.connector_type, "created_by": row.created_by,
            "created_at": row.created_at,
        }


async def create_source(postgres: Any, body: dict[str, Any], created_by: str, tenant_id: str, role_id: str) -> dict[str, Any]:
    connector_type = body["connector_type"]
    if connector_type not in _VALID_CONNECTOR_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"connector_type must be one of {sorted(_VALID_CONNECTOR_TYPES)} in V1, got {connector_type!r}",
        )
    # Empty cron is valid: a manual-only source the worker's cron_due() never
    # auto-triggers (see web_crawler/worker.py's _is_due — a falsy cron_expr
    # is simply never due), only run via "Run Now". Not every real source has
    # a schedule the friendly picker can express yet (e.g. a true one-off, or
    # an irregular publication with no fixed cadence).

    # The Bronze document is named after the source title (unique per tenant),
    # so a second source with the same title would silently overwrite the
    # first one's document on every run.
    async with postgres.get_client() as session:
        taken = await session.scalar(
            sa_select(KBSourceORM.source_id).where(
                KBSourceORM.tenant_id == uuid.UUID(tenant_id),
                KBSourceORM.title == body["title"],
                KBSourceORM.is_deleted == False,  # noqa: E712
            ).limit(1)
        )
    if taken is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A source titled {body['title']!r} already exists — its Bronze document is named after "
                   "the title, so a second one would overwrite it. Use a different title.",
        )

    src_ins = await postgres.insert(KBSourceInsert(
        tenant_id=tenant_id, role_id=role_id, connector_type=connector_type,
        title=body["title"], publisher=body.get("publisher"), created_by=created_by,
    ))
    if src_ins.code != 200:
        raise HTTPException(status_code=500, detail=f"Failed to create source: {src_ins.error}")
    source_id = src_ins.data["source_id"]

    cron = body.get("cron") or ""
    # A worker can't click a button: a source with a real schedule defaults to
    # promoting itself straight through to Gold after a changed crawl, while a
    # manual-only (no-schedule) source keeps the human-click flow — the
    # caller can still override either way via `promotion_mode`.
    promotion_mode: Literal["manual", "auto"] = body.get("promotion_mode") or ("auto" if cron.strip() else "manual")
    config_payload = SourceConfigPayload(
        url=body["url"], format=body.get("format"), cron=cron,
        render=bool(body.get("render", False)), variables=body.get("variables"),
        openbao_path=body.get("openbao_path"), query_params=body.get("query_params"),
        auth_style=body.get("auth_style"), auth_param_name=body.get("auth_param_name"),
        recursive=bool(body.get("recursive", False)), max_depth=body.get("max_depth", 1),
        same_domain_only=bool(body.get("same_domain_only", True)),
        promotion_mode=promotion_mode,
        notes=body.get("notes"),
    )
    cfg_ins = await postgres.insert(KBSourceConfigInsert(
        source_id=source_id, version_number=1, is_active=True, created_by=created_by,
        config=config_payload,
    ))
    if cfg_ins.code != 200:
        raise HTTPException(status_code=500, detail=f"Failed to create source config: {cfg_ins.error}")

    row = await _source_row(postgres, source_id, tenant_id)
    return _to_response(row, config_payload.model_dump(), None)


async def update_source_config(
    postgres: Any, source_id: str, body: dict[str, Any], updated_by: str, tenant_id: str,
) -> dict[str, Any]:
    src = await _source_row(postgres, source_id, tenant_id)  # existence + tenant check
    source_uuid = src["source_id"]

    async with postgres.get_client() as session:
        current = await session.scalar(
            sa_select(KBSourceConfigORM).where(
                KBSourceConfigORM.source_id == source_uuid,
                KBSourceConfigORM.is_active == True,  # noqa: E712
            )
        )
        max_v = await session.scalar(
            sa_select(sa_func.max(KBSourceConfigORM.version_number))
            .where(KBSourceConfigORM.source_id == source_uuid)
        ) or 0

        # Read config + deactivate in the SAME session as the insert below —
        # doing this across two separate `get_client()` sessions (the
        # original shape) meant `current.is_active = False` mutated an
        # instance the second session never knew about, so the deactivate
        # silently never persisted; and reading `current.config` after the
        # first session closed raised DetachedInstanceError once a row
        # actually existed (the empty-table smoke test never hit either bug).
        base_cfg = dict((current.config if current else {}) or {})
        base_cfg.update({k: v for k, v in body.items() if v is not None})

        if current is not None:
            current.is_active = False
        session.add(KBSourceConfigORM(
            source_id=source_uuid, version_number=max_v + 1, is_active=True,
            config=base_cfg, created_by=updated_by,
        ))
        await session.commit()

    return _to_response(src, base_cfg, None)


async def delete_source(postgres: Any, source_id: str, tenant_id: str) -> None:
    await _source_row(postgres, source_id, tenant_id)  # existence + tenant check
    resp = await postgres.soft_delete(KBSourceDelete(source_id=source_id))
    if resp.code not in (200, 404):
        raise HTTPException(status_code=500, detail=f"Failed to delete source: {resp.error}")


async def list_source_runs(postgres: Any, source_id: str, tenant_id: str) -> list[dict[str, Any]]:
    await _source_row(postgres, source_id, tenant_id)  # existence + tenant check
    async with postgres.get_client() as session:
        runs = (await session.execute(
            sa_select(KBSourceRunORM)
            .where(KBSourceRunORM.source_id == uuid.UUID(source_id))
            .order_by(KBSourceRunORM.started_at.desc())
            .limit(50)
        )).scalars().all()
        return [
            {
                "run_id": str(r.run_id),
                "status": r.status,
                "trigger": r.trigger,
                "retry_count": r.retry_count,
                "started_at": r.started_at.isoformat() if r.started_at else "",
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "data_ids": r.data_ids or [],
                "error": r.error,
                "quality_metrics": r.quality_metrics,
            }
            for r in runs
        ]


async def queue_run_now(postgres: Any, source_id: str, tenant_id: str) -> dict[str, Any]:
    """Records a manual run as `queued`; the worker picks queued rows up on its
    next tick. The row (not a Kafka message) is the queue, so Run Now works
    even when Kafka is down and the UI can show what is waiting."""
    await _source_row(postgres, source_id, tenant_id)  # existence + tenant check
    source_uuid = uuid.UUID(source_id)
    cutoff = datetime.datetime.now(datetime.timezone.utc) - _STALE_RUN_AFTER
    async with postgres.get_client() as session:
        active = await session.scalar(
            sa_select(KBSourceRunORM.run_id).where(
                KBSourceRunORM.source_id == source_uuid,
                KBSourceRunORM.status.in_(_ACTIVE_RUN_STATUSES),
                KBSourceRunORM.started_at > cutoff,
            ).limit(1)
        )
        if active is not None:
            raise HTTPException(status_code=409, detail="This source is already queued or running")
        run = KBSourceRunORM(source_id=source_uuid, status="queued", trigger="manual", retry_count=0)
        session.add(run)
        await session.flush()
        run_id = str(run.run_id)
        await session.commit()
    return {"run_id": run_id, "status": "queued", "source_id": source_id}


async def list_recent_activity(postgres: Any, tenant_id: str) -> list[dict[str, Any]]:
    """Runs that are queued/running plus ones that finished moments ago — what
    the Activity panel shows, across every source of the tenant."""
    tenant_uuid = uuid.UUID(tenant_id)
    finished_after = datetime.datetime.now(datetime.timezone.utc) - _RECENT_FINISH_WINDOW
    async with postgres.get_client() as session:
        rows = (await session.execute(
            sa_select(
                KBSourceRunORM.run_id, KBSourceRunORM.source_id, KBSourceORM.title,
                KBSourceRunORM.status, KBSourceRunORM.trigger, KBSourceRunORM.started_at,
                KBSourceRunORM.finished_at, KBSourceRunORM.error, KBSourceRunORM.quality_metrics,
            )
            .join(KBSourceORM, KBSourceORM.source_id == KBSourceRunORM.source_id)
            .where(
                KBSourceORM.tenant_id == tenant_uuid,
                KBSourceORM.is_deleted == False,  # noqa: E712
                sa_or(
                    KBSourceRunORM.status.in_(_ACTIVE_RUN_STATUSES),
                    KBSourceRunORM.finished_at > finished_after,
                ),
            )
            .order_by(KBSourceRunORM.started_at.desc())
            .limit(50)
        )).all()
        return [
            {
                "run_id": str(r.run_id), "source_id": str(r.source_id), "title": r.title,
                "status": r.status, "trigger": r.trigger,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "error": r.error,
                "unchanged": bool((r.quality_metrics or {}).get("unchanged")),
                "existing_tier": (r.quality_metrics or {}).get("existing_tier"),
            }
            for r in rows
        ]
