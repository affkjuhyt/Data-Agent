"""API-05 agent lifecycle routes."""

from fastapi import APIRouter

from services.api.modules.agent.routes import router

__all__ = ["router"]
