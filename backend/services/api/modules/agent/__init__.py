"""API-05 Agent group: agents, configurations, triggers, deployments, runs and memories; plus the
background tasks of UC-08-05 (tasks.py), whose table is schema agent's."""

from services.api.modules.agent import tasks
from services.api.modules.agent.routes import router

router.include_router(tasks.router)

__all__ = ["router"]
