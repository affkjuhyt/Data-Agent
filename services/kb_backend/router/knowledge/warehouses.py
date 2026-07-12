"""
/api/knowledge/warehouses/{id}/configs — warehouse config management.
Shared helpers are also re-exported for documents.py aliases.
"""
from fastapi import APIRouter, Depends, Query, Request

from basemodel.services_databaseconnector.shared_model import ResponseModel
from services.kb_backend.schemas import CreateWarehouseConfigRequest
from services.kb_backend.dependencies.auth import authenticate
from services.kb_backend.dependencies.services import svc
from services.kb_backend.dependencies.auth import require_perm
from services.kb_backend.operations.warehouse_ops import (
    get_warehouse_configs, create_warehouse_config,
    activate_warehouse_config, delete_warehouse_config,
    delete_config_table, list_warehouses,
)
from services.kb_backend.operations.warehouse_connect_ops import (
    delete_warehouse_connection,
)

router = APIRouter(prefix="/api/knowledge/warehouses", tags=["Knowledge-Warehouses"])


@router.get("", response_model=ResponseModel)
async def list_warehouses_route(
    request: Request,
    ctx: dict = Depends(authenticate),
    cursor: str | None = Query(None, description="opaque cursor; omit for first page"),
):
    """Paginated warehouse list for the Warehouses sub-tab (tenant-scoped)."""
    result = await list_warehouses(svc(request, "postgres"), tenant_id=ctx["tenant_id"], cursor=cursor)
    return ResponseModel(code=200, data=result)


@router.get("/{warehouse_id}/configs", response_model=ResponseModel)
async def get_configs(
    warehouse_id: str, request: Request, ctx: dict = Depends(authenticate),
):
    result = await get_warehouse_configs(svc(request, "postgres"), warehouse_id, tenant_id=ctx["tenant_id"])
    return ResponseModel(code=200, data=result)


@router.post("/{warehouse_id}/configs", response_model=ResponseModel)
async def create_config(
    warehouse_id: str, body: CreateWarehouseConfigRequest,
    request: Request, ctx: dict = Depends(require_perm("add_warehouse_config")),
):
    result = await create_warehouse_config(
        postgres=svc(request, "postgres"),
        warehouse_id=warehouse_id,
        body=body.model_dump(),
        created_by=ctx["user_name"],
        tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


@router.patch("/{warehouse_id}/configs/{config_id}/activate", response_model=ResponseModel)
async def activate_config(
    warehouse_id: str, config_id: str,
    request: Request, ctx: dict = Depends(require_perm("edit_warehouse_config")),
):
    result = await activate_warehouse_config(
        svc(request, "postgres"), warehouse_id, config_id, tenant_id=ctx["tenant_id"],
    )
    return ResponseModel(code=200, data=result)


@router.delete("/{warehouse_id}", response_model=ResponseModel)
async def delete_warehouse_route(
    warehouse_id: str,
    request: Request, ctx: dict = Depends(require_perm("delete_data")),
):
    """Delete an entire warehouse (KBWarehouse row + its configs). Warehouses live
    in their own tables, not KBData, so they cannot be removed via the document
    delete route — this is their dedicated delete path."""
    await delete_warehouse_connection(svc(request, "postgres"), warehouse_id)
    return ResponseModel(code=200, data=None)


@router.delete("/{warehouse_id}/configs/{config_id}", response_model=ResponseModel)
async def delete_config(
    warehouse_id: str, config_id: str,
    request: Request, ctx: dict = Depends(require_perm("edit_warehouse_config")),
):
    await delete_warehouse_config(svc(request, "postgres"), config_id)
    return ResponseModel(code=200, data=None)


@router.delete("/{warehouse_id}/configs/{config_id}/tables/{table_id}", response_model=ResponseModel)
async def delete_config_table_route(
    warehouse_id: str, config_id: str, table_id: str,
    request: Request, ctx: dict = Depends(require_perm("edit_warehouse_config")),
):
    await delete_config_table(svc(request, "postgres"), config_id, table_id)
    return ResponseModel(code=200, data=None)
