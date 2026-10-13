"""Agent lock, unlock and run retry (UC-13-06, UC-13-02, added 12/10/2026) against PostgreSQL.

Locking writes agent_def.status = 'locked' with the status it restores in status_before_lock, and
retry links the new run with agent_run.retry_of (v3_0011)."""

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
    owner: uuid.UUID
    other: uuid.UUID
    space: uuid.UUID
    other_space: uuid.UUID
    suffix: str


def _user(seed: Seed, org: uuid.UUID, name: str) -> uuid.UUID:
    return seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-{name}",
        username=name,
        display_name=name,
        status="active",
    )


@pytest.fixture
def world(seed: Seed) -> World:
    """The steward owns the agents; `other` may create and run agents but owns none; `viewer`
    may only ask."""
    suffix = uuid.uuid4().hex[:8]
    org = seed.org()
    owner = _user(seed, org, f"owner-{suffix}")
    other = _user(seed, org, f"other-{suffix}")
    viewer = _user(seed, org, f"viewer-{suffix}")
    role = seed.row(
        "org.role",
        org_id=org,
        code=f"AG{suffix}",
        name="Agent",
        capabilities=["ask", "create_agent", "run_agent"],
    )
    ask = seed.row("org.role", org_id=org, code=f"AS{suffix}", name="Ask", capabilities=["ask"])
    space = seed.row(
        "org.data_space", org_id=org, code=f"DS-{suffix}", name="Agents", steward_id=owner
    )
    other_space = seed.row(
        "org.data_space", org_id=org, code=f"DS2-{suffix}", name="Other", steward_id=owner
    )
    for user, r in ((owner, role), (other, role), (viewer, ask)):
        for s in (space, other_space):
            seed.row(
                "org.data_space_member",
                org_id=org,
                data_space_id=s,
                principal_type="user",
                principal_id=user,
                role_id=r,
            )
    return World(
        org=org, owner=owner, other=other, space=space, other_space=other_space, suffix=suffix
    )


class Tokens:
    def __init__(self, org: uuid.UUID) -> None:
        self.org = org

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org)}


def client(world: World) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(world.org))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


def headers(world: World, who: str = "owner", space: uuid.UUID | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer kc-{who}-{world.suffix}",
        "X-Data-Space": str(space or world.space),
    }


def execute(seed: Seed, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with seed.conn.cursor() as cur:
        cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
        return list(cur.fetchall()) if cur.description else []


def events(seed: Seed, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
    rows = execute(
        seed,
        "SELECT event_type, payload FROM agent.outbox WHERE aggregate_id = %s"
        " ORDER BY created_at, id",
        aggregate_id,
    )
    return [(row[0], row[1]) for row in rows]


async def new_agent(api: httpx.AsyncClient, world: World) -> dict[str, Any]:
    created = await api.post(
        "/v1/agents",
        headers=headers(world),
        json={
            "code": f"recon-{uuid.uuid4().hex[:6]}",
            "name": "Agent Doi soat",
            "kind": "reconcile",
            "config": {"goal": "So NPL voi bao cao da nop"},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


async def new_run(api: httpx.AsyncClient, world: World, agent_id: str) -> str:
    accepted = await api.post(f"/v1/agents/{agent_id}/test-runs", headers=headers(world))
    assert accepted.status_code == 202, accepted.text
    return str(accepted.json()["id"])


def fail(seed: Seed, run_id: str) -> None:
    execute(
        seed,
        "UPDATE agent.agent_run SET status = 'failed', stop_reason = 'error',"
        " error = 'source timeout', as_of = DATE '2026-09-30', trigger_ref = 'close-2026-09',"
        " started_at = now(), finished_at = now() WHERE id = %s",
        run_id,
    )


# ---------------------------------------------------------------- retry


async def test_a_failed_run_is_retried_as_a_new_run(world: World, seed: Seed) -> None:
    async with client(world) as api:
        agent = await new_agent(api, world)
        old = await new_run(api, world, agent["id"])
        fail(seed, old)

        accepted = await api.post(f"/v1/runs/{old}:retry", headers=headers(world))
        assert accepted.status_code == 202, accepted.text
        new = accepted.json()["id"]
        assert new != old
        assert accepted.headers["Location"] == f"/v1/runs/{new}"
        assert accepted.json()["status"] == "queued"

        got = await api.get(f"/v1/runs/{new}", headers=headers(world))
        assert got.status_code == 200, got.text
        run = got.json()
        assert (run["agent_id"], run["config_version"], run["as_of"], run["trigger_ref"]) == (
            agent["id"],
            1,
            "2026-09-30",
            "close-2026-09",
        )
        assert (run["trigger_kind"], run["is_test"]) == ("test", True)
        assert run["initiated_by"] == str(world.owner)
        # The failed run stays as it was.
        assert execute(seed, "SELECT status FROM agent.agent_run WHERE id = %s", old) == [
            ("failed",)
        ]

    [(event_type, payload)] = events(seed, new)
    assert event_type == "run.status_changed"
    assert payload["retry_of"] == old
    assert payload["status"] == "queued"
    assert payload["config_version"] == 1


async def test_retry_needs_a_failed_run_the_caller_may_steer(world: World, seed: Seed) -> None:
    async with client(world) as api:
        agent = await new_agent(api, world)
        queued = await new_run(api, world, agent["id"])
        not_failed = await api.post(f"/v1/runs/{queued}:retry", headers=headers(world))
        assert not_failed.status_code == 409, not_failed.text
        assert not_failed.json()["code"] == "invalid_state"

        fail(seed, queued)
        # run_agent alone does not steer someone else's run (AC-13-06-07).
        stranger = await api.post(f"/v1/runs/{queued}:retry", headers=headers(world, "other"))
        assert stranger.status_code == 403, stranger.text
        # Without run_agent: 403 before anything is read.
        viewer = await api.post(f"/v1/runs/{queued}:retry", headers=headers(world, "viewer"))
        assert viewer.status_code == 403, viewer.text
        # Another Data Space than the run's, or an unknown run: 404.
        elsewhere = await api.post(
            f"/v1/runs/{queued}:retry", headers=headers(world, space=world.other_space)
        )
        assert elsewhere.status_code == 404, elsewhere.text
        unknown = await api.post(f"/v1/runs/{uuid.uuid4()}:retry", headers=headers(world))
        assert unknown.status_code == 404, unknown.text
    assert execute(
        seed, "SELECT count(*) FROM agent.agent_run WHERE agent_id = %s", agent["id"]
    ) == [(1,)]


async def test_the_retry_is_linked_to_the_failed_run(world: World, seed: Seed) -> None:
    async with client(world) as api:
        agent = await new_agent(api, world)
        old = await new_run(api, world, agent["id"])
        fail(seed, old)
        accepted = await api.post(f"/v1/runs/{old}:retry", headers=headers(world))
        assert accepted.status_code == 202, accepted.text
    assert execute(
        seed, "SELECT retry_of::text FROM agent.agent_run WHERE id = %s", accepted.json()["id"]
    ) == [(old,)]


# ---------------------------------------------------------------- lock and unlock


async def test_lock_stops_runs_and_triggers_and_hands_requests_to_the_owner(
    world: World, seed: Seed
) -> None:
    async with client(world) as api:
        agent = await new_agent(api, world)
        execute(seed, "UPDATE agent.agent_def SET status = 'running' WHERE id = %s", agent["id"])
        triggers = await api.put(
            f"/v1/agents/{agent['id']}/triggers",
            headers=headers(world),
            json={"triggers": [{"kind": "schedule", "cron": "0 9 * * 1-5"}]},
        )
        assert triggers.status_code == 200, triggers.text
        trigger_id = triggers.json()["items"][0]["id"]
        queued = await new_run(api, world, agent["id"])
        waiting = await new_run(api, world, agent["id"])
        execute(
            seed,
            "UPDATE agent.agent_run SET status = 'waiting_human', waiting_since = now()"
            " WHERE id = %s",
            waiting,
        )
        request = uuid.uuid4()
        seed.row(
            "agent.agent_checkpoint_hit",
            org_id=world.org,
            run_id=uuid.UUID(waiting),
            checkpoint="amount > 1bn",
            hitl_request_id=request,
        )
        failed = await new_run(api, world, agent["id"])
        fail(seed, failed)

        locked = await api.post(
            f"/v1/agents/{agent['id']}:lock",
            headers=headers(world),
            json={"reason": "Sai so lieu"},
        )
        assert locked.status_code == 200, locked.text
        assert locked.json()["status"] == "locked"
        assert locked.json()["paused_reason"] == "Sai so lieu"
        assert execute(
            seed, "SELECT status_before_lock FROM agent.agent_def WHERE id = %s", agent["id"]
        ) == [("running",)]
        statuses = dict(
            execute(
                seed,
                "SELECT id::text, status FROM agent.agent_run WHERE agent_id = %s",
                agent["id"],
            )
        )
        assert statuses == {queued: "cancelled", waiting: "waiting_human", failed: "failed"}
        assert execute(
            seed, "SELECT enabled FROM agent.agent_trigger WHERE id = %s", trigger_id
        ) == [(False,)]

        again = await api.post(
            f"/v1/agents/{agent['id']}:lock", headers=headers(world), json={"reason": "x"}
        )
        assert again.status_code == 409, again.text
        retry = await api.post(f"/v1/runs/{failed}:retry", headers=headers(world))
        assert retry.status_code == 409, retry.text

        unlocked = await api.post(f"/v1/agents/{agent['id']}:unlock", headers=headers(world))
        assert unlocked.status_code == 200, unlocked.text
        assert unlocked.json()["status"] == "running"
        assert unlocked.json()["paused_reason"] is None
        assert execute(
            seed, "SELECT status_before_lock FROM agent.agent_def WHERE id = %s", agent["id"]
        ) == [(None,)]
        # Triggers are not turned back on by the unlock (A3).
        assert execute(
            seed, "SELECT enabled FROM agent.agent_trigger WHERE id = %s", trigger_id
        ) == [(False,)]
        not_locked = await api.post(f"/v1/agents/{agent['id']}:unlock", headers=headers(world))
        assert not_locked.status_code == 409, not_locked.text

    by_type = dict(events(seed, agent["id"]))
    lock = by_type["agent.locked"]
    assert lock["previous_status"] == "running"
    assert lock["cancelled_run_ids"] == [queued]
    assert lock["disabled_trigger_ids"] == [trigger_id]
    assert lock["hitl_request_ids"] == [str(request)]
    assert lock["reassign_to"] == str(world.owner)
    assert by_type["agent.unlocked"]["status"] == "running"
    assert [e for e, _ in events(seed, queued)][-1] == "run.status_changed"


async def test_only_the_owner_or_steward_locks_and_only_a_locked_agent_unlocks(
    world: World, seed: Seed
) -> None:
    async with client(world) as api:
        agent = await new_agent(api, world)
        url = f"/v1/agents/{agent['id']}"
        no_reason = await api.post(f"{url}:lock", headers=headers(world), json={})
        assert no_reason.status_code == 422, no_reason.text
        stranger = await api.post(
            f"{url}:lock", headers=headers(world, "other"), json={"reason": "x"}
        )
        assert stranger.status_code == 403, stranger.text
        viewer = await api.post(
            f"{url}:lock", headers=headers(world, "viewer"), json={"reason": "x"}
        )
        assert viewer.status_code == 403, viewer.text
        elsewhere = await api.post(
            f"{url}:lock", headers=headers(world, space=world.other_space), json={"reason": "x"}
        )
        assert elsewhere.status_code == 404, elsewhere.text
        unknown = await api.post(
            f"/v1/agents/{uuid.uuid4()}:lock", headers=headers(world), json={"reason": "x"}
        )
        assert unknown.status_code == 404, unknown.text

        not_locked = await api.post(f"{url}:unlock", headers=headers(world))
        assert not_locked.status_code == 409, not_locked.text
        assert not_locked.json()["code"] == "invalid_state"
    assert execute(seed, "SELECT status FROM agent.agent_def WHERE id = %s", agent["id"]) == [
        ("draft",)
    ]
