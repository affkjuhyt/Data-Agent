"""Liveness and readiness probes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from services.indicator_api.adapters import ADAPTER_NAMES, Adapter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    # Liveness touches no store: a database outage must make the pod unready,
    # not get it restarted in a loop.
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    adapters: list[Adapter] = request.app.state.adapters
    checks = {name: "not_configured" for name in ADAPTER_NAMES}
    for adapter in adapters:
        try:
            adapter.ping()
            checks[adapter.name] = "ok"
        # Each client raises its own error types (psycopg2, clickhouse-connect,
        # kafka, minio, plain OSError); any of them means "not ready".
        except Exception as exc:  # noqa: BLE001
            logger.warning("ready | %s ping failed: %s", adapter.name, exc)
            checks[adapter.name] = "error"
    failed = "error" in checks.values()
    return JSONResponse(
        status_code=503 if failed else 200,
        content={"status": "unavailable" if failed else "ok", "checks": checks},
    )
