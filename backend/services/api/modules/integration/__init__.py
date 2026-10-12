"""integration module (schema `integration`): A2 group Tích hợp."""

from fastapi import APIRouter

from services.api.modules.integration import admin_clients, mcp, owners, routes

router = APIRouter()
router.include_router(routes.router)
router.include_router(owners.router)
router.include_router(admin_clients.router)
router.include_router(mcp.router)

__all__ = ["router"]
