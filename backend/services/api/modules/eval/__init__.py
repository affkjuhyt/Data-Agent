"""API-04 evaluation and error-book routes."""

from fastapi import APIRouter

from services.api.modules.eval.routes import router

__all__ = ["router"]
