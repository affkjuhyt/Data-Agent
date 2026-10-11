"""A1.5 Idempotency-Key against PostgreSQL, with an in-memory response store in place of Redis:
the same POST sent twice runs once, another body under the same key is refused."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import httpx
import pytest

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration]


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def put(self, ref: str, value: str, ttl: datetime.timedelta) -> None:
        self.values[ref] = value

    async def get(self, ref: str) -> str | None:
        return self.values.get(ref)


class _Tokens:
    def __init__(self, org_id: uuid.UUID) -> None:
        self.org_id = org_id

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org_id)}


def build(seed: Seed) -> uuid.UUID:
    o = seed.org()
    for who in ("kc-ac06", "kc-ac06b"):
        user = seed.row("org.app_user", org_id=o, external_id=who, username=who, display_name=who)
        role = seed.row("org.role", org_id=o, code=who, name=who, capabilities=["audit_read"])
        seed.row("org.user_role", org_id=o, user_id=user, role_id=role, source="manual")
    return o


def client(org: uuid.UUID, store: MemoryStore, who: str = "kc-ac06") -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=_Tokens(org), response_store=store)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api",
        headers={"Authorization": f"Bearer {who}"},
    )


async def test_a1_5_the_same_post_runs_once(seed: Seed) -> None:
    org, store = build(seed), MemoryStore()
    key = {"Idempotency-Key": "export-2026-10-12-1"}
    async with client(org, store) as c:
        first = await c.post("/v1/audit:export", json={"filters": {}}, headers=key)
        again = await c.post("/v1/audit:export", json={"filters": {}}, headers=key)
        other = await c.post("/v1/audit:export", json={"filters": {"action": "x"}}, headers=key)
        unkeyed = await c.post("/v1/audit:export", json={"filters": {}})
    assert first.status_code == again.status_code == 202, first.text
    assert again.json() == first.json() and again.headers["Location"] == first.headers["Location"]
    assert again.headers["Idempotent-Replayed"] == "true"
    assert other.status_code == 422 and other.json()["code"] == "idempotency_mismatch"
    assert unkeyed.json()["id"] != first.json()["id"]
    cur = seed.conn.cursor()
    cur.execute("SELECT count(*) FROM integration.idempotency_key WHERE org_id = %s", (str(org),))
    assert cur.fetchone()[0] == 1


async def test_a1_5_a_key_belongs_to_its_sender(seed: Seed) -> None:
    org, store = build(seed), MemoryStore()
    key = {"Idempotency-Key": "same-key"}
    async with client(org, store) as c:
        mine = await c.post("/v1/audit:export", json={"filters": {}}, headers=key)
    async with client(org, store, "kc-ac06b") as c:
        theirs = await c.post("/v1/audit:export", json={"filters": {}}, headers=key)
    assert theirs.status_code == 202 and "Idempotent-Replayed" not in theirs.headers
    assert theirs.json()["id"] != mine.json()["id"]


async def test_a1_5_a_key_over_128_characters_is_refused(seed: Seed) -> None:
    org, store = build(seed), MemoryStore()
    async with client(org, store) as c:
        r = await c.post(
            "/v1/audit:export", json={"filters": {}}, headers={"Idempotency-Key": "k" * 129}
        )
    assert r.status_code == 400
