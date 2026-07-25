"""Service-accessor dependency: hand a route a *connected* client off app.state.

Health-gates the connector (via its `is_healthy()`) so a route never receives a
dead client. Returns a clean 503 when a service isn't connected — e.g. Qdrant /
Neo4j aren't running in lite deployments, or a live service went down and the
background health-check loop marked it unhealthy — instead of letting the route
blow up later with a low-level ConnectionError (500).
"""
from typing import Any

from fastapi import HTTPException, Request


def svc(request: Request, name: str) -> Any:
    obj = getattr(request.app.state, name, None)
    if obj is None:
        raise HTTPException(
            status_code=503,
            detail=f"Service '{name}' is not available on this deployment.",
        )
    # Connectors raise from is_healthy() until a connection is first established
    # (e.g. Qdrant/Neo4j off in lite), and the health-check loop flips it to False
    # if a live service dies. Either way → a clean 503. Objects without is_healthy
    # (stateless pipelines) pass through unchecked.
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
