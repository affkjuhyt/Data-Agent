from typing import Any
import base64
import datetime
import logging
import uuid
from fastapi import HTTPException
from sqlalchemy import select as sa_select, update as sa_update, func as sa_func

from libs.shared.row_version import with_new_version
from libs.basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest, WhereFilter,
    KBWarehouseConfigInsert, KBWarehouseConfigDelete,
    WarehouseConfigPayload,
)
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBWarehouseConfigORM, KBWarehouseORM,
)
from services.kb_backend.operations.access import assert_child_scoped, kbdata_row_exists
from services.kb_backend.operations.pagination import PAGE_SIZE, build_cursor_page
from services.kb_backend.schemas import to_string

from services.kb_backend.operations.locking import update_locked

log = logging.getLogger(__name__)


def _decode_offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(base64.urlsafe_b64decode(cursor.encode()).decode()))
    except (ValueError, TypeError):  # binascii.Error subclasses ValueError
        return 0


async def list_warehouses(postgres: Any, tenant_id: str, cursor: str | None = None) -> dict[str, Any]:
    offset = _decode_offset(cursor)
    tenant_uuid = uuid.UUID(tenant_id)
    async with postgres.get_client() as session:
        total = await session.scalar(
            sa_select(sa_func.count()).select_from(KBWarehouseORM)
            .where(
                KBWarehouseORM.tenant_id == tenant_uuid,
                KBWarehouseORM.is_deleted == False,
            )
        ) or 0
        rows = (await session.execute(
            sa_select(KBWarehouseORM)
            .where(
                KBWarehouseORM.tenant_id == tenant_uuid,
                KBWarehouseORM.is_deleted == False,
            )
            .order_by(KBWarehouseORM.inserted_at.desc(), KBWarehouseORM.warehouse_id.desc())
            .offset(offset).limit(PAGE_SIZE)
        )).scalars().all()

        wh_ids = [w.warehouse_id for w in rows]
        creators: dict[str, str] = {}
        if wh_ids:
            crows = (await session.execute(
                sa_select(KBWarehouseConfigORM.warehouse_id, KBWarehouseConfigORM.created_by)
                .where(
                    KBWarehouseConfigORM.warehouse_id.in_(wh_ids),
                    KBWarehouseConfigORM.is_active == True,  # noqa: E712
                )
            )).all()
            creators = {str(wid): (cb or "") for wid, cb in crows}

    docs = []
    for w in rows:
        service = (w.service or "").lower()
        docs.append({
            "data_id": str(w.warehouse_id),
            "name": w.description or f"Warehouse ({service.title()})",
            "source_type": "warehouse",
            "extension": None,
            "current_tier": "gold",
            "status": "PUBLISHED",
            "added_by": creators.get(str(w.warehouse_id), ""),
            "added_on": str(w.created_at or ""),
            "abstract": None,
            "metadata": {
                "doc_type": f"Warehouse/{service}",
                "language": None,
                "access_role": None,
                "warehouse_type": service,
            },
        })

    has_next = offset + PAGE_SIZE < total
    next_cursor = base64.urlsafe_b64encode(str(offset + PAGE_SIZE).encode()).decode() if has_next else None
    return build_cursor_page(docs, total, has_next, next_cursor)


async def _warehouse_row_exists(postgres: Any, warehouse_id: str, tenant_id: str) -> bool:
    async with postgres.get_client() as session:
        row = await session.scalar(
            sa_select(KBWarehouseORM.warehouse_id)
            .where(
                KBWarehouseORM.warehouse_id == uuid.UUID(warehouse_id),
                KBWarehouseORM.tenant_id == uuid.UUID(tenant_id),
                KBWarehouseORM.is_deleted == False,
            )
            .limit(1)
        )
    return row is not None


async def _assert_tenant(postgres: Any, doc_id: str, tenant_id: str, role_id: str | None = None) -> None:
    if await kbdata_row_exists(postgres, doc_id, tenant_id, role_id, exclude_deleted=True):
        return
    if await _warehouse_row_exists(postgres, doc_id, tenant_id):
        return
    raise HTTPException(status_code=404, detail="Document not found")


def _to_wconfig(row: dict[str, Any]) -> dict[str, Any]:
    cfg = row.get("config") or {}
    tables_raw = cfg.get("selected_tables") or []
    connection = cfg.get("connection") or {
        "host": cfg.get("host", ""),
        "port": str(cfg.get("port", "")),
        "database": cfg.get("database", ""),
    }
    ver_num = row.get("version_number", 1)
    ver_label = f"v{ver_num}"
    return {
        "id": to_string(row.get("config_id")),
        "row_version": to_string(row.get("row_version")),
        "name": f"Config {ver_label}",
        "version": ver_label,
        "version_number": ver_label,
        "status": "Active" if row.get("is_active") else "Inactive",
        "created_at": to_string(row.get("created_at", "")),
        "connection": connection,
        "tables": [
            {"name": t, "schema": "", "rowCount": "—", "description": ""}
            if isinstance(t, str) else t
            for t in tables_raw
        ],
    }


async def get_warehouse_configs(postgres: Any, warehouse_id: str, tenant_id: str, role_id: str | None = None) -> list[
    Any]:
    await _assert_tenant(postgres, warehouse_id, tenant_id, role_id)
    resp = await postgres.read(ReadJoinRequest(
        joins_table=["KBWarehouseConfig"],
        filters=[WhereFilter(
            table_name="KBWarehouseConfig",
            column_name="warehouse_id",
            value=warehouse_id,
        )],
        limit=50,
    ))
    if resp.code != 200:
        log.error("warehouse_ops: get_warehouse_configs failed: %s", resp.error)
        raise HTTPException(status_code=500, detail="Failed to load warehouse configs")
    return [_to_wconfig(r) for r in (resp.data or [])]


async def create_warehouse_config(
        postgres: Any, warehouse_id: str, body: dict[str, Any], created_by: str, tenant_id: str,
        role_id: str | None = None,
) -> dict[str, Any]:
    await _assert_tenant(postgres, warehouse_id, tenant_id, role_id)
    resp = await postgres.read(ReadJoinRequest(
        joins_table=["KBWarehouseConfig"],
        filters=[WhereFilter(
            table_name="KBWarehouseConfig",
            column_name="warehouse_id",
            value=warehouse_id,
        )],
        limit=100,
    ))
    existing = resp.data or []
    max_v = max((r.get("version_number") or 0 for r in existing), default=0)

    connection = body.get("connection") or {}
    tables = body.get("tables") or body.get("selectedTables") or body.get("selected_tables") or []

    new_version = max_v + 1
    ins = await postgres.insert(KBWarehouseConfigInsert(
        warehouse_id=warehouse_id,
        version_number=new_version,
        is_active=False,
        created_by=created_by,
        config=WarehouseConfigPayload(
            host=connection.get("account") or connection.get("host") or body.get("host"),
            database=connection.get("database") or body.get("database"),
            selected_tables=tables,
            sync_schedule=body.get("syncSchedule") or body.get("sync_schedule"),
            schema_filter=body.get("schemaFilter") or body.get("schema_filter"),
            connection=connection or None,
        ),
    ))
    if ins.code != 200:
        log.error("warehouse_ops: create_warehouse_config insert failed: %s", ins.error)
        raise HTTPException(status_code=500, detail="Failed to create config")

    ver_label = f"v{new_version}"
    return {
        "id": str(ins.data.get("config_id")),
        "name": f"Config {ver_label}",
        "version": ver_label,
        "version_number": ver_label,
        "status": "Inactive",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "connection": connection,
        "tables": tables,
    }


async def activate_warehouse_config(
        postgres: Any, warehouse_id: str, config_id: str, tenant_id: str, role_id: str | None = None,
) -> dict[str, Any]:
    await _assert_tenant(postgres, warehouse_id, tenant_id, role_id)
    warehouse_uuid = uuid.UUID(warehouse_id) if isinstance(warehouse_id, str) else warehouse_id
    config_uuid = uuid.UUID(config_id) if isinstance(config_id, str) else config_id
    async with postgres.get_client() as session:
        await session.execute(
            sa_update(KBWarehouseConfigORM)
            .where(KBWarehouseConfigORM.warehouse_id == warehouse_uuid)
            .values(**with_new_version(is_active=False))
        )
        await session.execute(
            sa_update(KBWarehouseConfigORM)
            .where(
                KBWarehouseConfigORM.config_id == config_uuid,
                KBWarehouseConfigORM.warehouse_id == warehouse_uuid,
            )
            .values(**with_new_version(is_active=True))
        )
        await session.commit()
    return {"status": "ok", "config_id": config_id}


async def delete_warehouse_config(
        postgres: Any, warehouse_id: str, config_id: str, tenant_id: str, role_id: str | None = None,
) -> None:
    await _assert_tenant(postgres, warehouse_id, tenant_id, role_id)
    await assert_child_scoped(
        postgres, KBWarehouseConfigORM.config_id, config_id,
        KBWarehouseConfigORM.warehouse_id, warehouse_id, not_found_detail="Config not found",
    )
    resp = await postgres.soft_delete(KBWarehouseConfigDelete(config_id=config_id))
    if resp.code == 404:
        raise HTTPException(status_code=404, detail="Config not found")
    if resp.code != 200:
        log.error("delete_warehouse_config: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Failed to delete config")


async def delete_config_table(
        postgres: Any, warehouse_id: str, config_id: str, table_id: str, tenant_id: str,
        role_id: str | None = None, expected_version: uuid.UUID | None = None,
) -> dict[str, Any]:
    await _assert_tenant(postgres, warehouse_id, tenant_id, role_id)

    config_uuid = uuid.UUID(config_id) if isinstance(config_id, str) else config_id
    warehouse_uuid = uuid.UUID(warehouse_id) if isinstance(warehouse_id, str) else warehouse_id

    def mutate(locked: Any) -> dict[str, Any]:
        cfg = dict(locked.config or {})
        cfg["selected_tables"] = [t for t in (cfg.get("selected_tables") or []) if t != table_id]
        return {"config": cfg}

    new_version = await update_locked(
        postgres,
        KBWarehouseConfigORM,
        [KBWarehouseConfigORM.config_id == config_uuid,
         KBWarehouseConfigORM.warehouse_id == warehouse_uuid],
        mutate=mutate,
        expected_version=expected_version,
        not_found_detail="Config not found",
    )
    return {"status": "ok", "row_version": str(new_version)}
