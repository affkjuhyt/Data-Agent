"""governance module (schema `governance`): A2 groups Chính sách and the governance part of
Quản trị (models, providers, AI settings, route decisions, system health, data subject requests).
"""

from fastapi import APIRouter

from services.api.modules.governance import admin_models, dsr, policies, routes

router = APIRouter()
router.include_router(routes.router)
router.include_router(policies.router)
router.include_router(dsr.router)
router.include_router(admin_models.router)

__all__ = ["router"]
