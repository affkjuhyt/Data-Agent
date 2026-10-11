"""API-04 human decision inbox routes."""

from fastapi import APIRouter

from services.api.modules.inbox.routes import router

__all__ = ["router"]
