"""API-02 against PostgreSQL 16 and the generated V3 grants."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]


@dataclass
class World:
    org: uuid.UUID
    user: uuid.UUID
    space: uuid.UUID


def build(seed: Seed, suffix: str = "") -> World:
    org = seed.org()
    user = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-api{suffix}",
        username=f"api{suffix}",
        display_name="API test",
        status="active",
    )
    role = seed.row(
        "org.role", org_id=org, code=f"ASK{suffix or '0'}", name="Ask", capabilities=["ask", "approve"]
    )
    seed.row("org.user_role", org_id=org, user_id=user, role_id=role, source="manual")
    space = seed.row(
        "org.data_space",
        org_id=org,
        code=f"DS-API{suffix}",
        name="API test space",
        steward_id=user,
    )
    seed.row(
        "org.data_space_member",
        org_id=org,
        data_space_id=space,
        principal_type="user",
        principal_id=user,
        role_id=role,
    )
    return World(org=org, user=user, space=space)


@pytest.fixture
def world(seed: Seed) -> World:
    return build(seed)


class Tokens:
    def __init__(self, world: World) -> None:
        self.world = world

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.world.org)}


def client(world: World) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(world))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


async def test_question_lifecycle_and_sse(world: World) -> None:
    headers = {"Authorization": "Bearer kc-api", "X-Data-Space": str(world.space)}
    async with client(world) as api:
        created = await api.post("/v1/questions", headers=headers, json={"text": "Doanh thu?"})
        assert created.status_code == 202, created.text
        question_id = created.json()["question_id"]

        question = await api.get(f"/v1/questions/{question_id}", headers=headers)
        assert question.status_code == 200, question.text
        assert question.json()["status"] == "received"

        stream = await api.get(f"/v1/questions/{question_id}/stream", headers=headers)
        assert stream.status_code == 200, stream.text
        assert "event: question.status" in stream.text
        assert "event: question.complete" not in stream.text

        replay = await api.get(
            f"/v1/questions/{question_id}/stream",
            headers={**headers, "Last-Event-ID": "1"},
        )
        assert replay.status_code == 200, replay.text
        assert "question.status" not in replay.text

        conversations = await api.get("/v1/conversations", headers=headers)
        assert conversations.status_code == 200, conversations.text
        assert conversations.json()["items"][0]["last_question_at"] is not None


async def test_query_run_is_org_scoped(world: World, seed: Seed) -> None:
    headers = {"Authorization": "Bearer kc-api", "X-Data-Space": str(world.space)}
    other = build(seed, "-other")
    async with client(world) as api:
        created = await api.post(
            "/v1/explore/queries", headers=headers, json={"text": "select 1"}
        )
        assert created.status_code == 202, created.text
        run_id = created.json()["id"]
        fetched = await api.get(f"/v1/query-runs/{run_id}", headers=headers)
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["status"] == "running"

    async with client(other) as other_api:
        cross_org = await other_api.get(
            f"/v1/query-runs/{run_id}",
            headers={"Authorization": "Bearer kc-api-other", "X-Data-Space": str(other.space)},
        )
        assert cross_org.status_code == 404, cross_org.text
