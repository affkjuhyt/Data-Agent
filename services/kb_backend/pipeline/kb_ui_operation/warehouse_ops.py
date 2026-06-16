"""Business logic for warehouse config CRUD."""
import datetime
import logging
import uuid
from fastapi import HTTPException
from sqlalchemy import select as sa_select, update as sa_update

from basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest, WhereFilter,
    KBWarehouseConfigInsert, KBWarehouseConfigDelete,
    WarehouseConfigPayload,
)
from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBDataORM, KBWarehouseConfigORM, KBWarehouseORM,
)
from services.kb_backend.UI_model.response import to_string

log = logging.getLogger(__name__)


async def _assert_tenant(postgres, doc_id: str, tenant_id: str) -> None:
    doc_uuid = uuid.UUID(doc_id)
    async with postgres.get_client() as session:
        # Check regular documents first
        row = await session.scalar(
            sa_select(KBDataORM.data_id)
            .where(
                KBDataORM.data_id == doc_uuid,
                KBDataORM.tenant_id == uuid.UUID(tenant_id),
                KBDataORM.is_deleted == False,  # noqa: E712
            )
            .limit(1)
        )
        if row is not None:
            return
        # Warehouse records (created via wizard) live in KBWarehouse without tenant_id;
        # allow if the record exists at all.
        wh_row = await session.scalar(
            sa_select(KBWarehouseORM.warehouse_id)
            .where(
                KBWarehouseORM.warehouse_id == doc_uuid,
                KBWarehouseORM.is_deleted == False,  # noqa: E712
            )
            .limit(1)
        )
    if wh_row is None:
        raise HTTPException(status_code=404, detail="Document not found")


def _to_wconfig(row: dict) -> dict:
    cfg = row.get("config") or {}
    tables_raw = cfg.get("selected_tables") or []
    connection = cfg.get("connection") or {
        "host":     cfg.get("host", ""),
        "port":     str(cfg.get("port", "")),
        "database": cfg.get("database", ""),
    }
    ver_num = row.get("version_number", 1)
    ver_label = f"v{ver_num}"
    return {
        "id":             to_string(row.get("config_id")),
        "name":           f"Config {ver_label}",
        "version":        ver_label,
        "version_number": ver_label,   # UI uses this field name
        "status":         "Active" if row.get("is_active") else "Inactive",
        "created_at":     to_string(row.get("created_at", "")),
        "connection":     connection,
        "tables": [
            {"name": t, "schema": "", "rowCount": "—", "description": ""}
            if isinstance(t, str) else t
            for t in tables_raw
        ],
    }


async def get_warehouse_configs(postgres, warehouse_id: str, tenant_id: str) -> list:
    await _assert_tenant(postgres, warehouse_id, tenant_id)
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
    postgres, warehouse_id: str, body: dict, user_id: str, tenant_id: str,
) -> dict:
    await _assert_tenant(postgres, warehouse_id, tenant_id)
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
        created_by=user_id,
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

    # Return a full WarehouseConfigVersion shape so the UI can add it directly
    ver_label = f"v{new_version}"
    return {
        "id":             str(ins.data.get("config_id")),
        "name":           f"Config {ver_label}",
        "version":        ver_label,
        "version_number": ver_label,
        "status":         "Inactive",
        "created_at":     datetime.datetime.utcnow().isoformat(),
        "connection":     connection,
        "tables":         tables,
    }


async def activate_warehouse_config(
    postgres, warehouse_id: str, config_id: str, tenant_id: str,
) -> dict:
    await _assert_tenant(postgres, warehouse_id, tenant_id)
    warehouse_uuid = uuid.UUID(warehouse_id) if isinstance(warehouse_id, str) else warehouse_id
    config_uuid = uuid.UUID(config_id) if isinstance(config_id, str) else config_id
    async with postgres.get_client() as session:
        await session.execute(
            sa_update(KBWarehouseConfigORM)
            .where(KBWarehouseConfigORM.warehouse_id == warehouse_uuid)
            .values(is_active=False)
        )
        await session.execute(
            sa_update(KBWarehouseConfigORM)
            .where(KBWarehouseConfigORM.config_id == config_uuid)
            .values(is_active=True)
        )
        await session.commit()
    return {"status": "ok", "config_id": config_id}


async def delete_warehouse_config(postgres, config_id: str) -> None:
    resp = await postgres.soft_delete(KBWarehouseConfigDelete(config_id=config_id))
    if resp.code == 404:
        raise HTTPException(status_code=404, detail="Config not found")
    if resp.code != 200:
        log.error("delete_warehouse_config: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Failed to delete config")


async def delete_config_table(postgres, config_id: str, table_id: str) -> None:
    resp = await postgres.read(ReadJoinRequest(
        joins_table=["KBWarehouseConfig"],
        filters=[WhereFilter(
            table_name="KBWarehouseConfig",
            column_name="config_id",
            value=config_id,
        )],
        limit=1,
    ))
    if resp.code != 200 or not resp.data:
        raise HTTPException(status_code=404, detail="Config not found")

    row = resp.data[0]
    cfg = row.get("config") or {}
    cfg["selected_tables"] = [t for t in (cfg.get("selected_tables") or []) if t != table_id]

    config_uuid = uuid.UUID(config_id) if isinstance(config_id, str) else config_id
    async with postgres.get_client() as session:
        await session.execute(
            sa_update(KBWarehouseConfigORM)
            .where(KBWarehouseConfigORM.config_id == config_uuid)
            .values(config=cfg)
        )
        await session.commit()
