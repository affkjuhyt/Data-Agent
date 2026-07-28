from typing import Any

from fastapi import HTTPException, Request

# Get database Server
def svc(request: Request, name: str) -> Any:
    obj = getattr(request.app.state, name, None)
    if obj is None:
        raise HTTPException(
            status_code=503,
            detail=f"Service '{name}' is not available on this deployment.",
        )
    health = getattr(obj, "is_healthy", None)
    if callable(health):
        try:
            healthy = health()
        except Exception:
            healthy = False
        if not healthy:
            raise HTTPException(
                status_code=503,
                detail=f"Service '{name}' is currently unavailable (not connected).",
            )
    return obj
