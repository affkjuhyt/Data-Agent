"""integration module (schema `integration`): A2 group Tích hợp."""

from fastapi import APIRouter

from services.api.modules.integration import mcp, owners, routes

router = APIRouter()
router.include_router(routes.router)
router.include_router(owners.router)
router.include_router(mcp.router)

__all__ = ["router"]
