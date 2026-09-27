import logging
from typing import Any

from libs.basemodel.services_databaseconnector.postgres_model import KBWarehouseDelete
from fastapi import HTTPException

log = logging.getLogger(__name__)


async def delete_warehouse_connection(postgres: Any, warehouse_id: str) -> None:
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
