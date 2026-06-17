"""
Warehouse connection management and discovery.
POST /api/knowledge/warehouses/connect — test connection, discover tables
POST /api/knowledge/warehouses/select-tables — save connection + config, returns warehouse_id
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
import logging

from basemodel.services_databaseconnector.shared_model import ResponseModel
from basemodel.services_databaseconnector.postgres_model import (
    KBWarehouseInsert,
    KBWarehouseConfigInsert,
    WarehouseConfigPayload,
)
from services.kb_backend.dependencies.auth import authenticate
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.database_connector.snowflake_connector import SnowflakeConnector

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/knowledge/warehouses", tags=["Warehouse-Connection"])


class ConnectSnowflakeRequest(BaseModel):
    account_identifier: str
    user: str
    password: str
    warehouse: str
    database: str
    schema: str
    role: str | None = None


class SelectTablesRequest(BaseModel):
    connection_name: str
    warehouse_type: str  # "Snowflake" | "Databricks"
    account_identifier: str
    user: str
    password: str
    warehouse: str
    database: str
    schema: str
    selected_table_ids: list[str]
    role: str | None = None


@router.post("/connect", response_model=ResponseModel)
async def test_warehouse_connection(
    body: ConnectSnowflakeRequest,
    request: Request,
    ctx: dict = Depends(require_perm("add_warehouse")),
):
    """Test Snowflake connection and discover tables in the schema."""
    log.info("Testing Snowflake connection account=%s database=%s", body.account_identifier, body.database)

    connector = SnowflakeConnector()
    try:
        result = await connector.validate_and_connect(
            account_identifier=body.account_identifier,
            user=body.user,
            password=body.password,
            warehouse=body.warehouse,
            database=body.database,
            schema=body.schema,
            role=body.role,
        )
    finally:
        connector.close()

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])

    return ResponseModel(code=200, data={
        "success": True,
        "message": result["message"],
        "tables": result["tables"],
    })


@router.post("/select-tables", response_model=ResponseModel)
async def select_warehouse_tables(
    body: SelectTablesRequest,
    request: Request,
    ctx: dict = Depends(require_perm("add_warehouse")),
):
    """
    Validate Snowflake connection, fetch column schemas, then persist:
      1. KBWarehouse record
      2. KBWarehouseConfig v1 (active) with connection details + selected tables

    Returns warehouse_id — the UI uses this as the document ID so CONFIGS tab
    can call GET /api/knowledge/documents/{warehouse_id}/configs later.
    """
    log.info("Saving warehouse connection name=%s tables=%d", body.connection_name, len(body.selected_table_ids))

    postgres = svc(request, "postgres")

    # 1. Connect and validate
    connector = SnowflakeConnector()
    try:
        result = await connector.validate_and_connect(
            account_identifier=body.account_identifier,
            user=body.user,
            password=body.password,
            warehouse=body.warehouse,
            database=body.database,
            schema=body.schema,
            role=body.role,
        )

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["message"])

        # 2. Fetch column schemas for selected tables
        table_schemas: dict[str, dict] = {}
        for table_id in body.selected_table_ids:
            schema = await connector.get_table_schema(table_id)
            if schema:
                table_schemas[table_id] = schema
    finally:
        connector.close()

    # 3. Build table objects merging discovery info + column schema
    discovered = {t["table_name"]: t for t in result.get("tables", [])}
    tables = []
    for table_id in body.selected_table_ids:
        info = discovered.get(table_id, {})
        col_schema = table_schemas.get(table_id, {})
        row_count = info.get("row_count")
        tables.append({
            "name": table_id,
            "schema": body.schema or "PUBLIC",
            "rowCount": f"{row_count:,}" if isinstance(row_count, int) else "—",
            "description": "",
            "columns": col_schema.get("columns", []),
        })

    # 4. Create KBWarehouse record
    wh_ins = await postgres.insert(KBWarehouseInsert(
        service=body.warehouse_type,
        description=body.connection_name,
    ))
    if wh_ins.code != 200:
        log.error("warehouse_connect: failed to create warehouse record: %s", wh_ins.error)
        raise HTTPException(status_code=500, detail="Failed to create warehouse")
    warehouse_id: str = wh_ins.data["warehouse_id"]

    # 5. Create initial config v1 (auto-activated)
    cfg_ins = await postgres.insert(KBWarehouseConfigInsert(
        warehouse_id=warehouse_id,
        version_number=1,
        is_active=True,
        created_by=ctx["user_id"],
        config=WarehouseConfigPayload(
            host=body.account_identifier,
            database=body.database,
            selected_tables=tables,
            connection={
                "account":   body.account_identifier,
                "username":  body.user,
                "warehouse": body.warehouse,
                "database":  body.database,
                "schema":    body.schema,
                "role":      body.role,
            },
        ),
    ))
    if cfg_ins.code != 200:
        log.error("warehouse_connect: failed to create config: %s", cfg_ins.error)
        raise HTTPException(status_code=500, detail="Failed to create warehouse config")

    log.info("Created warehouse %s with config %s (%d tables)", warehouse_id, cfg_ins.data.get("config_id"), len(tables))

    return ResponseModel(code=200, data={
        "warehouse_id":   warehouse_id,
        "connection_name": body.connection_name,
        "warehouse_type": body.warehouse_type.lower(),
        "message": f"Warehouse connected with {len(tables)} table(s)",
        "tables": tables,
    })
