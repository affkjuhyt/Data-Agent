"""Question answering and query-result read APIs (schemas `qa` and `query`)."""

from fastapi import APIRouter

from services.api.modules.qa import query, routes

router = APIRouter()
router.include_router(routes.router)
router.include_router(query.router)

__all__ = ["router"]
