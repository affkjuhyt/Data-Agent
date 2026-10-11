"""API-03 workspace routes."""

from fastapi import APIRouter

from services.api.modules.workspace.routes import router

__all__ = ["router"]
