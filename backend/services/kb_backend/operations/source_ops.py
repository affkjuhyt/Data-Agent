"""CRUD + run-history for the scheduled source registry ("KBSource" /
"KBSourceConfig" / "KBSourceRun") — mirrors warehouse_ops.py's shape
(KBWarehouse/KBWarehouseConfig) since a source is a versioned-config entity
of the same kind, just fetched by a URL+cron instead of a DB connection.
"""
from __future__ import annotations

import base64
import uuid
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import select as sa_select, func as sa_func

from libs.basemodel.services_databaseconnector.postgres_model import (
    KBSourceInsert, KBSourceConfigInsert, KBSourceDelete, KBSourceConfigDelete,
    SourceConfigPayload,
)
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBSourceORM, KBSourceConfigORM, KBSourceRunORM,
)
from services.kb_backend.operations.pagination import PAGE_SIZE, build_cursor_page

_VALID_CONNECTOR_TYPES = {"http_file", "web_crawl", "web_crawl_headless"}


def _decode_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(base64.urlsafe_b64decode(cursor.encode()).decode()))
    except (ValueError, TypeError):
        return 0


def _to_response(row: KBSourceORM, active_cfg: dict[str, Any] | None, last_run: dict[str, Any] | None) -> dict[str, Any]:
    cfg = active_cfg or {}
    last_run_started = (last_run or {}).get("started_at")
    return {
        "id": str(row.source_id),
        "title": row.title,
        "publisher": row.publisher,
        "connector_type": row.connector_type,
        "url": cfg.get("url"),
        "cron": cfg.get("cron"),
        "render": bool(cfg.get("render", False)),
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else "",
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

    docs = [_to_response(r, active_cfgs.get(str(r.source_id)), last_runs.get(str(r.source_id))) for r in rows]
    has_next = offset + PAGE_SIZE < total
    next_cursor = base64.urlsafe_b64encode(str(offset + PAGE_SIZE).encode()).decode() if has_next else None
    return build_cursor_page(docs, total, has_next, next_cursor)


async def _source_row(postgres: Any, source_id: str, tenant_id: str) -> KBSourceORM:
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
    return cast(KBSourceORM, row)


async def create_source(postgres: Any, body: dict[str, Any], created_by: str, tenant_id: str, role_id: str) -> dict[str, Any]:
    connector_type = body["connector_type"]
    if connector_type not in _VALID_CONNECTOR_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"connector_type must be one of {sorted(_VALID_CONNECTOR_TYPES)} in V1, got {connector_type!r}",
        )
    if not body.get("cron", "").strip():
        raise HTTPException(status_code=422, detail="cron is required")

    src_ins = await postgres.insert(KBSourceInsert(
        tenant_id=tenant_id, role_id=role_id, connector_type=connector_type,
        title=body["title"], publisher=body.get("publisher"), created_by=created_by,
    ))
    if src_ins.code != 200:
        raise HTTPException(status_code=500, detail=f"Failed to create source: {src_ins.error}")
    source_id = src_ins.data["source_id"]

    config_payload = SourceConfigPayload(
        url=body["url"], format=body.get("format"), cron=body["cron"],
        render=bool(body.get("render", False)), variables=body.get("variables"),
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
    row = await _source_row(postgres, source_id, tenant_id)

    async with postgres.get_client() as session:
        current = await session.scalar(
            sa_select(KBSourceConfigORM).where(
                KBSourceConfigORM.source_id == row.source_id,
                KBSourceConfigORM.is_active == True,  # noqa: E712
            )
        )
        max_v = await session.scalar(
            sa_select(sa_func.max(KBSourceConfigORM.version_number))
            .where(KBSourceConfigORM.source_id == row.source_id)
        ) or 0

    base_cfg = dict((current.config if current else {}) or {})
    base_cfg.update({k: v for k, v in body.items() if v is not None})

    async with postgres.get_client() as session:
        if current is not None:
            current.is_active = False
        session.add(KBSourceConfigORM(
            source_id=row.source_id, version_number=max_v + 1, is_active=True,
            config=base_cfg, created_by=updated_by,
        ))
        await session.commit()

    refreshed = await _source_row(postgres, source_id, tenant_id)
    return _to_response(refreshed, base_cfg, None)


async def delete_source(postgres: Any, source_id: str, tenant_id: str) -> None:
    row = await _source_row(postgres, source_id, tenant_id)
    resp = await postgres.soft_delete(KBSourceDelete(source_id=str(row.source_id)))
    if resp.code not in (200, 404):
        raise HTTPException(status_code=500, detail=f"Failed to delete source: {resp.error}")


async def list_source_runs(postgres: Any, source_id: str, tenant_id: str) -> list[dict[str, Any]]:
    row = await _source_row(postgres, source_id, tenant_id)
    async with postgres.get_client() as session:
        runs = (await session.execute(
            sa_select(KBSourceRunORM)
            .where(KBSourceRunORM.source_id == row.source_id)
            .order_by(KBSourceRunORM.started_at.desc())
            .limit(50)
        )).scalars().all()
    return [
        {
            "run_id": str(r.run_id),
            "status": r.status,
            "trigger": r.trigger,
            "started_at": r.started_at.isoformat() if r.started_at else "",
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "data_ids": r.data_ids or [],
            "error": r.error,
            "quality_metrics": r.quality_metrics,
        }
        for r in runs
    ]
