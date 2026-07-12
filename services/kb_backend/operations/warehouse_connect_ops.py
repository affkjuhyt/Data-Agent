"""Business logic for warehouse connection CRUD."""
import logging
from typing import Optional

from basemodel.services_databaseconnector.postgres_model import (
    KBWarehouseDelete,
    ReadJoinRequest, WhereFilter,
)
from fastapi import HTTPException

log = logging.getLogger(__name__)


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
