"""/health must fail once Postgres is not connected, or k8s never restarts a
kb-backend whose every /api route is 503 (seen after a Postgres restart)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.kb_backend.main import health

pytestmark = pytest.mark.unit


def _request(postgres: object | None) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(postgres=postgres)))


def test_health_is_ok_when_postgres_is_healthy() -> None:
    pg = SimpleNamespace(is_healthy=lambda: True)
    assert asyncio.run(health(_request(pg))) == {"status": "ok"}


def test_health_is_503_when_postgres_is_not_connected() -> None:
    pg = SimpleNamespace(is_healthy=lambda: False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(health(_request(pg)))
    assert exc.value.status_code == 503


def test_health_is_ok_before_the_lifespan_has_set_postgres() -> None:
    assert asyncio.run(health(_request(None))) == {"status": "ok"}
