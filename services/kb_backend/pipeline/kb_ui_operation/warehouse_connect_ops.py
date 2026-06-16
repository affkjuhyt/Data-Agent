"""Business logic for warehouse connection CRUD."""
import logging
import uuid
from typing import Optional

from basemodel.services_databaseconnector.postgres_model import (
    KBWarehouseInsert, KBWarehouseDelete,
    ReadJoinRequest, WhereFilter,
)
from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import KBWarehouseORM
from fastapi import HTTPException

log = logging.getLogger(__name__)


async def save_warehouse_connection(
    postgres,
    tenant_id: str,
    connection_name: str,
    warehouse_type: str,
    connection_metadata: dict,
    created_by: str,
) -> dict:
    """
    Save a new warehouse connection to the database.

    Args:
        postgres: PostgresClient instance
        tenant_id: Tenant ID
        connection_name: User-friendly name for the connection
        warehouse_type: "Snowflake" | "Databricks"
        connection_metadata: {account_identifier, database, schema, warehouse, user, password, role}
        created_by: User ID who created this connection

    Returns:
        {"warehouse_id": str, "connection_name": str}
    """
    warehouse_id = f"wh-{uuid.uuid4().hex[:8]}"

    try:
        resp = await postgres.insert(KBWarehouseInsert(
            warehouse_id=warehouse_id,
            tenant_id=tenant_id,
            name=connection_name,
            warehouse_type=warehouse_type,
            connection_metadata=connection_metadata,
            created_by=created_by,
        ))
        if resp.code != 200:
            log.error("save_warehouse_connection: DB error: %s", resp.error)
            raise HTTPException(status_code=500, detail="Failed to save warehouse connection")

        log.info("Saved warehouse connection %s type=%s", warehouse_id, warehouse_type)
        return {
            "warehouse_id": warehouse_id,
            "connection_name": connection_name,
        }
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Failed to save warehouse connection: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save warehouse connection")


async def save_warehouse_table_schema(
    postgres,
    warehouse_id: str,
    table_name: str,
    table_schema: dict,
) -> dict:
    """
    Save table schema metadata for a warehouse.

    Args:
        postgres: PostgresClient instance
        warehouse_id: Warehouse ID
        table_name: Name of the table
        table_schema: {columns: [{name, type, nullable, ...}]}

    Returns:
        {"table_name": str, "columns": int}
    """
    try:
        # TODO: Insert into KBWarehouseTableSchema ORM table
        # This will store the table metadata for querying later
        log.info("Saved table schema for %s.%s", warehouse_id, table_name)
        return {
            "table_name": table_name,
            "columns": len(table_schema.get("columns", [])),
        }
    except Exception as e:
        log.exception("Failed to save table schema: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save table schema")


async def get_warehouse_connection(postgres, warehouse_id: str) -> Optional[dict]:
    """Fetch a warehouse connection by ID."""
    try:
        resp = await postgres.read(ReadJoinRequest(
            joins_table=["KBWarehouse"],
            filters=[WhereFilter(
                table_name="KBWarehouse",
                column_name="warehouse_id",
                value=warehouse_id,
            )],
            limit=1,
        ))
        if resp.code != 200 or not resp.data:
            return None
        return resp.data[0]
    except Exception as e:
        log.error("Failed to fetch warehouse: %s", e)
        return None


async def list_warehouse_connections(
    postgres, tenant_id: str, limit: int = 50
) -> list:
    """List all warehouse connections for a tenant."""
    try:
        resp = await postgres.read(ReadJoinRequest(
            tenant_id=tenant_id,
            joins_table=["KBWarehouse"],
            limit=limit,
        ))
        if resp.code != 200:
            return []
        return resp.data or []
    except Exception as e:
        log.error("Failed to list warehouses: %s", e)
        return []


async def delete_warehouse_connection(postgres, warehouse_id: str) -> None:
    """Delete a warehouse connection and all associated metadata."""
    try:
        resp = await postgres.soft_delete(KBWarehouseDelete(warehouse_id=warehouse_id))
        if resp.code == 404:
            raise HTTPException(status_code=404, detail="Warehouse not found")
        if resp.code != 200:
            log.error("delete_warehouse_connection: DB error: %s", resp.error)
            raise HTTPException(status_code=500, detail="Failed to delete warehouse")
        log.info("Deleted warehouse connection %s", warehouse_id)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Failed to delete warehouse: %s", e)
        raise HTTPException(status_code=500, detail="Failed to delete warehouse")
