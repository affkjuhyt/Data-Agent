"""A1.8 against PostgreSQL: every /v1 call lands in audit.audit_log with its outcome, and a
person over the per-minute limit gets 429 with the RateLimit headers (an in-memory counter in
place of Redis)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import psycopg2
import pytest

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration]


class MemoryCounters:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def hit(self, key: str, ttl_seconds: int) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


class _Tokens:
    def __init__(self, org_id: uuid.UUID) -> None:
        self.org_id = org_id

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org_id)}


@pytest.fixture
def audit_rows(ddl_server: dict[str, str], api_database: str) -> Iterator[Any]:
    conn = psycopg2.connect(ddl_server["dataagent_audit"])
    conn.autocommit = True

    def rows(org: uuid.UUID) -> list[tuple[Any, ...]]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT action, outcome, actor_id, after->>'status' FROM audit.audit_log "
                "WHERE org_id = %s AND action LIKE 'api.%%' ORDER BY seq",
                (str(org),),
            )
            return cur.fetchall()

    yield rows
    conn.close()


def build(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    o = seed.org()
    controller = seed.row(
        "org.app_user", org_id=o, external_id="kc-ac06", username="ac06", display_name="KS"
    )
    seed.row("org.app_user", org_id=o, external_id="kc-ac01", username="ac01", display_name="PT")
    role = seed.row("org.role", org_id=o, code="AC06", name="KS", capabilities=["audit_read"])
    seed.row("org.user_role", org_id=o, user_id=controller, role_id=role, source="manual")
    return o, controller


def client(
    org: uuid.UUID, who: str, *, counters: MemoryCounters | None = None, limit: int = 120
) -> httpx.AsyncClient:
    app = create_app(
        Settings(), token_verifier=_Tokens(org), counter_store=counters, rate_limit=limit
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api",
        headers={"Authorization": f"Bearer {who}"},
    )


async def test_a1_8_every_call_is_logged_with_its_outcome(seed: Seed, audit_rows: Any) -> None:
    org, controller = build(seed)
    async with client(org, "kc-ac06") as c:
        assert (await c.get("/v1/security-events")).status_code == 200
    async with client(org, "kc-ac01") as c:
        assert (await c.get("/v1/security-events")).status_code == 403
    rows = audit_rows(org)
    assert ("api.listSecurityEvents", "ok", controller, "200") in rows
    assert [r for r in rows if r[1] == "denied"] == [
        ("api.listSecurityEvents", "denied", rows[-1][2], "403")
    ]


async def test_a1_8_over_the_limit_is_429_with_rate_limit_headers(
    seed: Seed, audit_rows: Any
) -> None:
    org, _ = build(seed)
    counters = MemoryCounters()
    async with client(org, "kc-ac06", counters=counters, limit=2) as c:
        first = await c.get("/v1/security-events")
        await c.get("/v1/security-events")
        over = await c.get("/v1/security-events")
    assert first.headers["RateLimit-Limit"] == "2"
    assert first.headers["RateLimit-Remaining"] == "1"
    assert 0 < int(first.headers["RateLimit-Reset"]) <= 60
    assert over.status_code == 429 and over.json()["code"] == "rate_limited"
    assert over.headers["Retry-After"] == over.headers["RateLimit-Reset"]
    assert audit_rows(org)[-1][1:] == ("denied", None, "429")
