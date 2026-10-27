"""API-05 Agent group against PostgreSQL: the A3 behaviour, the rules the database and the module
enforce, and the agent.outbox rows each state change writes.

The acceptance criteria a test proves are named in a comment above it: tests/conftest.py's
`req` marker does not accept AC-xx-yy-zz ids yet, and design_checklist reads either form."""

from __future__ import annotations

import datetime
import json
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
class AgentWorld:
    org: uuid.UUID
    owner: uuid.UUID
    other: uuid.UUID
    space: uuid.UUID
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


def build(seed: Seed) -> AgentWorld:
    """An org with the Data Space steward (owner of the agents) and a second member who may
    run agents but owns none of them."""
    suffix = uuid.uuid4().hex[:8]
    org = seed.org()
    owner = _user(seed, org, f"owner-{suffix}")
    other = _user(seed, org, f"other-{suffix}")
    caps = ["ask", "create_agent", "run_agent"]
    role = seed.row("org.role", org_id=org, code=f"AG{suffix}", name="Agent", capabilities=caps)
    space = seed.row(
        "org.data_space", org_id=org, code=f"DS-AG{suffix}", name="Agent space", steward_id=owner
    )
    for user in (owner, other):
        seed.row("org.user_role", org_id=org, user_id=user, role_id=role, source="manual")
        seed.row(
            "org.data_space_member",
            org_id=org,
            data_space_id=space,
            principal_type="user",
            principal_id=user,
            role_id=role,
        )
    return AgentWorld(org=org, owner=owner, other=other, space=space, suffix=suffix)


@pytest.fixture
def world(seed: Seed) -> AgentWorld:
    return build(seed)


class Tokens:
    def __init__(self, org: uuid.UUID) -> None:
        self.org = org

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org)}


def client(world: AgentWorld) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(world.org))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


def owner_headers(world: AgentWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer kc-owner-{world.suffix}", "X-Data-Space": str(world.space)}


def other_headers(world: AgentWorld) -> dict[str, str]:
    return {"Authorization": f"Bearer kc-other-{world.suffix}", "X-Data-Space": str(world.space)}


async def test_onboarding_is_server_backed_and_starts_a_trial(world: AgentWorld) -> None:
    headers = owner_headers(world)
    async with client(world) as api:
        initial = await api.get("/v1/me/onboarding", headers=headers)
        assert initial.status_code == 200
        assert initial.json()["state"] == "pending"
        assert len(initial.json()["templates"]) == 3

        started = await api.post(
            "/v1/me/onboarding:try-agent", headers=headers, json={"template_ref": "analysis"}
        )
        assert started.status_code == 202, started.text
        assert started.json()["agent_id"] and started.json()["run_id"]

        skipped = await api.post("/v1/me/onboarding:skip", headers=headers)
        assert skipped.status_code == 200
        assert skipped.json()["state"] == "skipped"


def events(seed: Seed, aggregate_id: str) -> list[tuple[str, str, dict[str, Any]]]:
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT aggregate_type, event_type, payload FROM agent.outbox"
            " WHERE aggregate_id = %s ORDER BY created_at, id",
            (aggregate_id,),
        )
        return list(cur.fetchall())


def execute(seed: Seed, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with seed.conn.cursor() as cur:
        cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
        return list(cur.fetchall()) if cur.description else []


async def new_agent(api: httpx.AsyncClient, world: AgentWorld, **extra: Any) -> dict[str, Any]:
    body = {"code": f"recon-{uuid.uuid4().hex[:6]}", "name": "Agent Doi soat", "kind": "reconcile"}
    created = await api.post("/v1/agents", headers=owner_headers(world), json={**body, **extra})
    assert created.status_code == 201, created.text
    return created.json()


async def started_run(api: httpx.AsyncClient, world: AgentWorld, agent_id: str) -> str:
    accepted = await api.post(f"/v1/agents/{agent_id}/test-runs", headers=owner_headers(world))
    assert accepted.status_code == 202, accepted.text
    return accepted.json()["id"]


# Proves AC-13-01-01.
async def test_create_agent_with_config_owned_by_the_caller(world: AgentWorld, seed: Seed) -> None:
    async with client(world) as api:
        draft = await api.post(
            "/v1/agents:draft-from-text",
            headers=owner_headers(world),
            json={"description": "Moi khi khoa so, so NPL voi bao cao da nop. Them chi tiet."},
        )
        assert draft.status_code == 200, draft.text
        assert draft.json()["goal"] == "Moi khi khoa so, so NPL voi bao cao da nop."
        assert draft.json()["limits"]["max_steps"] == 60
        # Nothing saved by the draft.
        listed = await api.get("/v1/agents", headers=owner_headers(world))
        assert listed.json()["items"] == []

        created = await new_agent(
            api,
            world,
            monthly_budget="1500000.00",
            config={"goal": "So NPL voi bao cao da nop", "limits": {"max_steps": 40}},
        )
        assert created["status"] == "draft"
        assert created["owner_user_id"] == str(world.owner)
        assert created["account_user_id"] not in (str(world.owner), None)
        assert created["current_config_version"] == 1
        assert created["current_config"]["version"] == 1
        assert created["monthly_budget"] == "1500000.00"

        got = await api.get(f"/v1/agents/{created['id']}", headers=owner_headers(world))
        assert got.status_code == 200, got.text
        assert got.json()["current_config"]["goal"] == "So NPL voi bao cao da nop"

        duplicate = await api.post(
            "/v1/agents",
            headers=owner_headers(world),
            json={"code": created["code"], "name": "Again", "kind": "reconcile"},
        )
        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["code"] == "conflict"

    created_events = events(seed, created["id"])
    assert [(t, e) for t, e, _ in created_events] == [("agent_def", "agent.created")]
    payload = created_events[0][2]
    assert payload["owner_user_id"] == str(world.owner)
    assert payload["account_user_id"] == created["account_user_id"]


async def test_list_agents_filters_and_pages(world: AgentWorld) -> None:
    async with client(world) as api:
        first = await new_agent(api, world)
        second = await new_agent(api, world, kind="report")
        third = await new_agent(api, world)
        page = await api.get("/v1/agents?page_size=2", headers=owner_headers(world))
        assert page.status_code == 200, page.text
        assert [a["id"] for a in page.json()["items"]] == [third["id"], second["id"]]
        token = page.json()["next_page_token"]
        assert token
        rest = await api.get(
            f"/v1/agents?page_size=2&page_token={token}", headers=owner_headers(world)
        )
        assert [a["id"] for a in rest.json()["items"]] == [first["id"]]
        assert rest.json()["next_page_token"] == ""

        reports = await api.get("/v1/agents?kind=report", headers=owner_headers(world))
        assert [a["id"] for a in reports.json()["items"]] == [second["id"]]
        mine = await api.get(f"/v1/agents?owner={world.other}", headers=owner_headers(world))
        assert mine.json()["items"] == []

        await api.post(
            f"/v1/agents/{first['id']}/configs", headers=owner_headers(world), json={"goal": "g"}
        )
        await started_run(api, world, first["id"])
        counted = await api.get("/v1/agents?kind=reconcile", headers=owner_headers(world))
        by_id = {a["id"]: a for a in counted.json()["items"]}
        assert by_id[first["id"]]["active_runs"] == 1
        assert by_id[first["id"]]["waiting_runs"] == 0


# Proves AC-13-02-01, AC-13-02-04.
async def test_config_versions_keep_running_runs_on_their_version(
    world: AgentWorld, seed: Seed
) -> None:
    # A skill of the agent's Data Space: a config names only those (R1-11).
    skill_id = seed.row(
        "skill.skill",
        org_id=world.org,
        data_space_id=world.space,
        code="s-v2",
        name="S",
        category="custom",
        scope="data_space",
        owner_id=world.owner,
    )
    async with client(world) as api:
        item = await new_agent(api, world, config={"goal": "v1"})
        run_id = await started_run(api, world, item["id"])
        # The agent goes live on version 1: a new version waits for a deployment.
        execute(seed, "UPDATE agent.agent_def SET status = 'running' WHERE id = %s", item["id"])
        v2 = await api.post(
            f"/v1/agents/{item['id']}/configs",
            headers=owner_headers(world),
            json={
                "goal": "v2",
                "skill_ids": [str(skill_id)],
                "tools": [
                    {"tool": "query", "permission_level": "read"},
                    {
                        "tool": "create_proposal",
                        "permission_level": "write_derived",
                        "requires_checkpoint": True,
                    },
                ],
                "change_note": "them cong cu ghi",
            },
        )
        assert v2.status_code == 201, v2.text
        assert v2.json()["version"] == 2
        assert {t["tool"] for t in v2.json()["tools"]} == {"query", "create_proposal"}

        agent_now = await api.get(f"/v1/agents/{item['id']}", headers=owner_headers(world))
        assert agent_now.json()["current_config_version"] == 1
        run = await api.get(f"/v1/runs/{run_id}", headers=owner_headers(world))
        assert run.json()["config_version"] == 1

        configs = await api.get(f"/v1/agents/{item['id']}/configs", headers=owner_headers(world))
        assert [c["version"] for c in configs.json()["items"]] == [2, 1]
    assert ("agent_config", "agent_config.created") in [
        (t, e) for t, e, _ in events(seed, v2.json()["id"])
    ]


# Proves AC-13-02-03, AC-13-02-05, AC-13-02-06, AC-13-02-08.
async def test_config_rules(world: AgentWorld) -> None:
    async with client(world) as api:
        item = await new_agent(api, world)
        url = f"/v1/agents/{item['id']}/configs"
        h = owner_headers(world)

        def tool(level: str, checkpoint: bool) -> dict[str, Any]:
            return {"tool": "t", "permission_level": level, "requires_checkpoint": checkpoint}

        unchecked = await api.post(
            url, headers=h, json={"goal": "g", "tools": [tool("write_derived", False)]}
        )
        assert unchecked.status_code == 422, unchecked.text
        assert unchecked.json()["code"] == "business_rule_violated"
        assert "BR-70" in unchecked.json()["detail"]
        autonomous = await api.post(
            url,
            headers=h,
            json={
                "goal": "g",
                "autonomy": "act_then_report",
                "tools": [tool("write_derived", False)],
            },
        )
        assert autonomous.status_code == 201, autonomous.text

        external = await api.post(
            url, headers=h, json={"goal": "g", "tools": [tool("send_external", False)]}
        )
        assert external.status_code == 422, external.text
        assert external.json()["code"] == "business_rule_violated"

        system = await api.post(url, headers=h, json={"goal": "g", "tools": [tool("system", True)]})
        assert system.status_code == 422, system.text

        too_many = await api.post(url, headers=h, json={"goal": "g", "limits": {"max_steps": 70}})
        assert too_many.status_code == 422, too_many.text
        assert too_many.json()["code"] == "business_rule_violated"
        within = await api.post(url, headers=h, json={"goal": "g", "limits": {"max_steps": 40}})
        assert within.status_code == 201, within.text
        # The refused saves took no version number.
        assert within.json()["version"] == 2


# Proves AC-13-03-01, AC-13-03-06.
async def test_triggers_replace_the_set_and_preview(world: AgentWorld, seed: Seed) -> None:
    async with client(world) as api:
        item = await new_agent(api, world)
        url = f"/v1/agents/{item['id']}/triggers"
        saved = await api.put(
            url,
            headers=owner_headers(world),
            json={
                "triggers": [
                    {
                        "kind": "schedule",
                        "cron": "0 7 * * 1-5",
                        "min_interval": "PT1H",
                        "no_overlap": True,
                    },
                    {"kind": "event", "event_type": "period.closed"},
                ]
            },
        )
        assert saved.status_code == 200, saved.text
        schedule, event = saved.json()["items"]
        runs = [datetime.datetime.fromisoformat(t) for t in schedule["next_runs"]]
        assert len(runs) == 5
        assert all(t.hour == 7 and t.minute == 0 and t.isoweekday() <= 5 for t in runs)
        assert schedule["warning"] is None
        assert event["next_runs"] == []

        noisy = await api.put(
            url,
            headers=owner_headers(world),
            json={"triggers": [{"kind": "schedule", "cron": "* * * * *"}]},
        )
        assert noisy.status_code == 200, noisy.text
        assert noisy.json()["items"][0]["warning"]
        assert (
            len(execute(seed, "SELECT 1 FROM agent.agent_trigger WHERE agent_id = %s", item["id"]))
            == 1
        )

        missing_cron = await api.put(
            url, headers=owner_headers(world), json={"triggers": [{"kind": "schedule"}]}
        )
        assert missing_cron.status_code == 422, missing_cron.text
        assert missing_cron.json()["code"] == "business_rule_violated"
        bad_cron = await api.put(
            url,
            headers=owner_headers(world),
            json={"triggers": [{"kind": "schedule", "cron": "7am"}]},
        )
        assert bad_cron.status_code == 422, bad_cron.text
    assert [e for _, e, _ in events(seed, item["id"])].count("agent_trigger.replaced") == 2


# Proves AC-13-04-01.
async def test_test_and_eval_runs_are_accepted_with_location(world: AgentWorld, seed: Seed) -> None:
    async with client(world) as api:
        bare = await new_agent(api, world)
        no_config = await api.post(
            f"/v1/agents/{bare['id']}/test-runs", headers=owner_headers(world)
        )
        assert no_config.status_code == 409, no_config.text
        assert no_config.json()["code"] == "invalid_state"

        item = await new_agent(api, world, config={"goal": "g"})
        accepted = await api.post(
            f"/v1/agents/{item['id']}/test-runs",
            headers=owner_headers(world),
            json={"config_version": 1, "as_of": "2026-06-30"},
        )
        assert accepted.status_code == 202, accepted.text
        body = accepted.json()
        assert accepted.headers["Location"] == f"/v1/runs/{body['id']}"
        assert body["status"] == "queued" and body["poll_after_ms"] > 0
        run = await api.get(accepted.headers["Location"], headers=owner_headers(world))
        assert run.json()["trigger_kind"] == "test"
        assert run.json()["is_test"] is True
        assert run.json()["as_of"] == "2026-06-30"
        assert run.json()["initiated_by"] == str(world.owner)

        unknown = await api.post(
            f"/v1/agents/{item['id']}/test-runs",
            headers=owner_headers(world),
            json={"config_version": 9},
        )
        assert unknown.status_code == 422, unknown.text

        evaluated = await api.post(
            f"/v1/agents/{item['id']}/eval-runs", headers=owner_headers(world)
        )
        assert evaluated.status_code == 202, evaluated.text
        eval_run_id = evaluated.json()["id"]
        assert evaluated.headers["Location"] == f"/v1/eval-runs/{eval_run_id}"
        configs = await api.get(f"/v1/agents/{item['id']}/configs", headers=owner_headers(world))
        assert configs.json()["items"][0]["status"] == "testing"
        assert configs.json()["items"][0]["eval_run_id"] == eval_run_id

    queued = events(seed, body["id"])
    assert queued == [
        (
            "agent_run",
            "run.status_changed",
            {
                "run_id": body["id"],
                "agent_id": item["id"],
                "config_version": 1,
                "is_test": True,
                "previous_status": None,
                "status": "queued",
            },
        )
    ]
    config_id = configs.json()["items"][0]["id"]
    assert [e for _, e, _ in events(seed, config_id)][-1] == "agent_config.eval_requested"


# Proves AC-13-05-01, AC-13-05-05.
async def test_deployment_lifecycle(world: AgentWorld, seed: Seed) -> None:
    async with client(world) as api:
        item = await new_agent(api, world, config={"goal": "v1"})
        h = owner_headers(world)
        await api.post(f"/v1/agents/{item['id']}/configs", headers=h, json={"goal": "v2"})
        url = f"/v1/agents/{item['id']}/deployments"

        not_passed = await api.post(url, headers=h, json={"config_version": 2})
        assert not_passed.status_code == 409, not_passed.text
        assert not_passed.json()["code"] == "invalid_state"

        # The eval module passes versions 1 and 2; version 1 is the one serving.
        execute(
            seed,
            "UPDATE agent.agent_config SET status = 'passed', eval_run_id = gen_random_uuid()"
            " WHERE agent_id = %s",
            item["id"],
        )
        execute(
            seed,
            "UPDATE agent.agent_def SET status = 'running', current_config_version = 1 WHERE id = %s",
            item["id"],
        )
        created = await api.post(url, headers=h, json={"config_version": 2})
        assert created.status_code == 201, created.text
        deployment = created.json()
        assert deployment["status"] == "pending_approval"
        assert deployment["traffic_pct"] == 10
        assert deployment["previous_version"] == 1
        assert deployment["gates"]["min_runs"] == 20

        second = await api.post(url, headers=h, json={"config_version": 2})
        assert second.status_code == 409, second.text
        assert second.json()["code"] == "conflict"

        patch_url = f"/v1/deployments/{deployment['id']}"
        early = await api.patch(patch_url, headers=h, json={"traffic_pct": 50})
        assert early.status_code == 409, early.text
        assert early.json()["code"] == "invalid_state"

        # Approval in Cần bạn moves it to rolling (inbox → worker); stand in for it.
        execute(
            seed,
            "UPDATE agent.agent_deployment SET status = 'rolling' WHERE id = %s",
            deployment["id"],
        )
        half = await api.patch(patch_url, headers=h, json={"traffic_pct": 50})
        assert half.status_code == 200, half.text
        assert half.json()["status"] == "rolling"
        full = await api.patch(patch_url, headers=h, json={"traffic_pct": 100})
        assert full.json()["status"] == "completed"
        served = await api.get(f"/v1/agents/{item['id']}", headers=h)
        assert served.json()["current_config_version"] == 2
        assert served.json()["active_deployment"] is None

        missing_reason = await api.post(f"{patch_url}:rollback", headers=h, json={})
        assert missing_reason.status_code == 422, missing_reason.text
        rolled = await api.post(
            f"{patch_url}:rollback", headers=h, json={"reason": "Loi tang 6 diem", "to_version": 1}
        )
        assert rolled.status_code == 200, rolled.text
        assert rolled.json()["status"] == "rolled_back"
        assert rolled.json()["rollback_reason"] == "Loi tang 6 diem"
        again = await api.post(f"{patch_url}:rollback", headers=h, json={"reason": "x"})
        assert again.status_code == 409, again.text
        after = await api.get(f"/v1/agents/{item['id']}", headers=h)
        assert after.json()["current_config_version"] == 1
        statuses = dict(
            execute(
                seed,
                "SELECT version, status FROM agent.agent_config WHERE agent_id = %s",
                item["id"],
            )
        )
        assert statuses == {1: "deployed", 2: "superseded"}

    assert [e for _, e, _ in events(seed, deployment["id"])] == [
        "agent_deployment.created",
        "agent_deployment.traffic_changed",
        "agent.deployed",
        "agent.rolled_back",
    ]


# Proves AC-13-06-02, AC-13-06-03, AC-13-06-07.
async def test_run_control_state_machine(world: AgentWorld, seed: Seed) -> None:
    async with client(world) as api:
        item = await new_agent(api, world, config={"goal": "g"})
        run_id = await started_run(api, world, item["id"])
        h = owner_headers(world)

        stranger = await api.post(f"/v1/runs/{run_id}:pause", headers=other_headers(world))
        assert stranger.status_code == 403, stranger.text
        resume_running = await api.post(f"/v1/runs/{run_id}:resume", headers=h)
        assert resume_running.status_code == 409, resume_running.text
        assert resume_running.json()["code"] == "invalid_state"
        early_plan = await api.put(
            f"/v1/runs/{run_id}/plan", headers=h, json={"steps": [{"id": 1}]}
        )
        assert early_plan.status_code == 409, early_plan.text

        execute(
            seed,
            "UPDATE agent.agent_run SET status = 'running', started_at = now(), current_step = 4 WHERE id = %s",
            run_id,
        )
        paused = await api.post(f"/v1/runs/{run_id}:pause", headers=h)
        assert paused.status_code == 200, paused.text
        assert paused.json()["status"] == "paused"
        again = await api.post(f"/v1/runs/{run_id}:pause", headers=h)
        assert again.status_code == 409, again.text

        plan = await api.put(
            f"/v1/runs/{run_id}/plan",
            headers=h,
            json={
                "steps": [{"seq": 5, "name": "compare"}, {"seq": 7, "name": "report"}],
                "edit_note": "bo buoc 6",
            },
        )
        assert plan.status_code == 200, plan.text
        assert plan.json()["version"] == 1
        replan = await api.put(f"/v1/runs/{run_id}/plan", headers=h, json={"steps": [{"seq": 5}]})
        assert replan.json()["version"] == 2

        resumed = await api.post(f"/v1/runs/{run_id}:resume", headers=h)
        assert resumed.json()["status"] == "running"

        cancelled = await api.post(
            f"/v1/runs/{run_id}:cancel", headers=h, json={"reason": "Khong can nua"}
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["stop_reason"] == "user_cancelled"
        assert cancelled.json()["finished_at"] is not None
        terminal = await api.post(f"/v1/runs/{run_id}:cancel", headers=h)
        assert terminal.status_code == 409, terminal.text

    changes = [(e, p.get("status")) for _, e, p in events(seed, run_id)]
    assert changes == [
        ("run.status_changed", "queued"),
        ("run.status_changed", "paused"),
        ("run.plan_edited", "paused"),
        ("run.plan_edited", "paused"),
        ("run.status_changed", "running"),
        ("run.status_changed", "cancelled"),
    ]
    # The free-text reason stays on the run, never in the event.
    assert all("reason" not in p for _, _, p in events(seed, run_id))


async def test_another_member_may_steer_a_run_they_started(world: AgentWorld, seed: Seed) -> None:
    async with client(world) as api:
        item = await new_agent(api, world, config={"goal": "g"})
        # A run the other member started (a manual run, e.g. from a background task).
        run_id = seed.row(
            "agent.agent_run",
            org_id=world.org,
            agent_id=uuid.UUID(item["id"]),
            config_version=1,
            data_space_id=world.space,
            trigger_kind="manual",
            initiated_by=world.other,
        )
        cancelled = await api.post(f"/v1/runs/{run_id}:cancel", headers=other_headers(world))
        assert cancelled.status_code == 200, cancelled.text


async def test_only_the_owner_or_steward_changes_an_agent(world: AgentWorld, seed: Seed) -> None:
    """BR-147: create_agent does not open another person's agent; the owner and the Data Space
    steward (or backup steward) decide for it."""
    async with client(world) as api:
        item = await new_agent(api, world, config={"goal": "g"})
        execute(
            seed,
            "UPDATE agent.agent_config SET status = 'passed', eval_run_id = gen_random_uuid()"
            " WHERE agent_id = %s",
            item["id"],
        )
        deployment = await api.post(
            f"/v1/agents/{item['id']}/deployments",
            headers=owner_headers(world),
            json={"config_version": 1},
        )
        assert deployment.status_code == 201, deployment.text
        dep = f"/v1/deployments/{deployment.json()['id']}"
        base = f"/v1/agents/{item['id']}"
        other = other_headers(world)
        attempts = [
            ("POST", f"{base}/configs", {"goal": "g2"}),
            ("PUT", f"{base}/triggers", {"triggers": []}),
            ("POST", f"{base}/test-runs", {}),
            ("POST", f"{base}/eval-runs", {}),
            ("POST", f"{base}/deployments", {"config_version": 1}),
            ("PATCH", dep, {"traffic_pct": 50}),
            ("POST", f"{dep}:rollback", {"reason": "x"}),
            ("POST", f"{base}/memories", {"scope": "agent", "kind": "decision", "content": "x"}),
        ]
        for method, path, body in attempts:
            refused = await api.request(method, path, headers=other, json=body)
            assert refused.status_code == 403, (path, refused.text)
            assert refused.json()["code"] == "forbidden"
        configs = await api.get(f"{base}/configs", headers=owner_headers(world))
        assert [c["version"] for c in configs.json()["items"]] == [1]

        # The backup steward is in charge of the Data Space too.
        execute(
            seed,
            "UPDATE org.data_space SET backup_steward_id = %s WHERE id = %s",
            world.other,
            world.space,
        )
        allowed = await api.post(f"{base}/configs", headers=other, json={"goal": "g2"})
        assert allowed.status_code == 201, allowed.text


async def test_rollback_only_returns_to_a_version_that_served(
    world: AgentWorld, seed: Seed
) -> None:
    async with client(world) as api:
        h = owner_headers(world)
        item = await new_agent(api, world, config={"goal": "v1 never evaluated"})
        for goal in ("v2", "v3"):
            await api.post(f"/v1/agents/{item['id']}/configs", headers=h, json={"goal": goal})
        execute(
            seed,
            "UPDATE agent.agent_config SET status = 'passed', eval_run_id = gen_random_uuid()"
            " WHERE agent_id = %s AND version IN (2, 3)",
            item["id"],
        )
        execute(
            seed,
            "UPDATE agent.agent_def SET status = 'running', current_config_version = 2 WHERE id = %s",
            item["id"],
        )
        created = await api.post(
            f"/v1/agents/{item['id']}/deployments", headers=h, json={"config_version": 3}
        )
        dep = f"/v1/deployments/{created.json()['id']}"
        execute(
            seed,
            "UPDATE agent.agent_deployment SET status = 'rolling' WHERE id = %s",
            created.json()["id"],
        )
        for target in (1, 3, 4):
            refused = await api.post(
                f"{dep}:rollback", headers=h, json={"reason": "x", "to_version": target}
            )
            assert refused.status_code == 422, (target, refused.text)
            assert refused.json()["code"] == "business_rule_violated"
        rolled = await api.post(f"{dep}:rollback", headers=h, json={"reason": "x", "to_version": 2})
        assert rolled.status_code == 200, rolled.text
        served = await api.get(f"/v1/agents/{item['id']}", headers=h)
        assert served.json()["current_config_version"] == 2


async def test_memories_stay_with_their_user_and_agent_owner(world: AgentWorld) -> None:
    async with client(world) as api:
        item = await new_agent(api, world)
        url = f"/v1/agents/{item['id']}/memories"
        h, other = owner_headers(world), other_headers(world)
        owners_pref = await api.post(
            url, headers=h, json={"scope": "user", "kind": "preference", "content": "Bang truoc"}
        )
        agent_note = await api.post(
            url, headers=h, json={"scope": "agent", "kind": "decision", "content": "Dung so thuan"}
        )
        shared = await api.post(
            url,
            headers=h,
            json={"scope": "data_space", "kind": "domain_context", "content": "Ky 5"},
        )
        # The other member's own preference, even when naming someone else.
        theirs = await api.post(
            url,
            headers=other,
            json={
                "scope": "user",
                "kind": "preference",
                "content": "Bieu do",
                "user_id": str(world.owner),
            },
        )
        assert theirs.status_code == 201, theirs.text
        assert theirs.json()["user_id"] == str(world.other)

        listed = await api.get(url, headers=other)
        assert {m["id"] for m in listed.json()["items"]} == {
            theirs.json()["id"],
            shared.json()["id"],
        }
        everything = await api.get(url, headers=h)
        assert len(everything.json()["items"]) == 4

        for hidden in (owners_pref, agent_note):
            path = f"/v1/memories/{hidden.json()['id']}"
            assert (await api.patch(path, headers=other, json={"content": "x"})).status_code == 404
            assert (await api.delete(path, headers=other)).status_code == 404
        shared_path = f"/v1/memories/{shared.json()['id']}"
        assert (await api.delete(shared_path, headers=other)).status_code == 403
        expire = await api.patch(
            shared_path, headers=other, json={"expires_at": "2027-01-01T00:00:00Z"}
        )
        assert expire.status_code == 403
        # The steward sees and changes any of them.
        own_path = f"/v1/memories/{theirs.json()['id']}"
        assert (
            await api.patch(own_path, headers=other, json={"status": "disabled"})
        ).status_code == 200
        assert (await api.delete(own_path, headers=h)).status_code == 204


# Proves AC-13-08-01.
async def test_runs_list_detail_children_and_events(world: AgentWorld, seed: Seed) -> None:
    async with client(world) as api:
        item = await new_agent(api, world, config={"goal": "g"})
        parent = await started_run(api, world, item["id"])
        child = await started_run(api, world, item["id"])
        execute(seed, "UPDATE agent.agent_run SET parent_run_id = %s WHERE id = %s", parent, child)
        for seq, kind in ((1, "query"), (2, "checkpoint")):
            seed.row(
                "agent.agent_step",
                org_id=world.org,
                run_id=uuid.UUID(parent),
                seq=seq,
                kind=kind,
                name=f"step-{seq}",
                summary=f"Buoc {seq}",
                status="ok",
                started_at=datetime.datetime.now(datetime.UTC),
            )
        h = owner_headers(world)

        children = await api.get(f"/v1/runs/{parent}/children", headers=h)
        assert [r["id"] for r in children.json()["items"]] == [child]
        assert children.json()["items"][0]["depth"] == 1

        filtered = await api.get(f"/v1/runs?parent_run_id={parent}", headers=h)
        assert [r["id"] for r in filtered.json()["items"]] == [child]
        by_agent = await api.get(f"/v1/runs?agent_id={item['id']}&status=queued", headers=h)
        assert {r["id"] for r in by_agent.json()["items"]} == {parent, child}
        future = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)).isoformat()
        none_yet = await api.get("/v1/runs", headers=h, params={"from": future})
        assert none_yet.json()["items"] == []

        simple = await api.get(f"/v1/runs/{parent}", headers=h)
        assert simple.json()["steps"] is None
        technical = await api.get(f"/v1/runs/{parent}?view=technical", headers=h)
        assert [s["seq"] for s in technical.json()["steps"]] == [1, 2]
        bad_view = await api.get(f"/v1/runs/{parent}?view=raw", headers=h)
        assert bad_view.status_code == 422

        stream = await api.get(f"/v1/runs/{parent}/events", headers=h)
        assert stream.status_code == 200, stream.text
        assert "event: step_started" in stream.text
        assert "event: checkpoint" in stream.text
        assert "event: cost" in stream.text
        assert "event: done" not in stream.text
        resumed = await api.get(f"/v1/runs/{parent}/events", headers={**h, "Last-Event-ID": "5"})
        assert "step_started" not in resumed.text
        assert "event: cost" in resumed.text


async def test_resources_stay_in_their_data_space_and_org(world: AgentWorld, seed: Seed) -> None:
    async with client(world) as api:
        item = await new_agent(api, world, config={"goal": "g"})
        run_id = await started_run(api, world, item["id"])
        no_space = await api.get(
            f"/v1/agents/{item['id']}", headers={"Authorization": f"Bearer kc-owner-{world.suffix}"}
        )
        assert no_space.status_code == 400
        assert no_space.json()["code"] == "data_space_required"

    elsewhere = build(seed)
    async with client(elsewhere) as api:
        h = owner_headers(elsewhere)
        assert (await api.get(f"/v1/agents/{item['id']}", headers=h)).status_code == 404
        assert (await api.get(f"/v1/runs/{run_id}", headers=h)).status_code == 404
        assert (await api.post(f"/v1/runs/{run_id}:cancel", headers=h)).status_code == 404


def seats(seed: Seed, account: str) -> list[tuple[Any, ...]]:
    return execute(
        seed,
        "SELECT r.code, m.row_scope FROM org.data_space_member m"
        " JOIN org.role r ON r.id = m.role_id"
        " WHERE m.principal_type = 'agent' AND m.principal_id = %s",
        account,
    )


async def test_a_new_agent_gets_its_account_seated_to_ask_in_its_data_space(
    world: AgentWorld, seed: Seed
) -> None:
    # R1-11: the account a scheduled run acts as exists and may ask in the agent's space, with
    # the narrowest role that asks (AC01) and no wider rows than its owner reads.
    seed.row("org.role", org_id=world.org, code="AC01", name="Nghiệp vụ", capabilities=["ask"])
    execute(
        seed,
        "UPDATE org.data_space_member SET row_scope = %s WHERE principal_id = %s",
        json.dumps({"region": ["HN"]}),
        world.other,
    )
    async with client(world) as api:
        mine = await new_agent(api, world)
        body = {"code": f"r-{uuid.uuid4().hex[:6]}", "name": "Agent cua other", "kind": "report"}
        theirs = await api.post("/v1/agents", headers=other_headers(world), json=body)
        assert theirs.status_code == 201, theirs.text
        again = await api.post("/v1/agents", headers=other_headers(world), json=body)
        assert again.status_code == 409, again.text

    account = mine["account_user_id"]
    assert execute(
        seed, "SELECT kind, owner_user_id, status FROM org.app_user WHERE id = %s", account
    ) == [("agent", world.owner, "active")]
    # The steward reads the whole space, so does their agent.
    assert seats(seed, account) == [("AC01", None)]
    assert seats(seed, theirs.json()["account_user_id"]) == [("AC01", {"region": ["HN"]})]
    # The refused duplicate left no account behind.
    assert execute(
        seed,
        "SELECT count(*) FROM org.app_user WHERE org_id = %s AND kind = 'agent'",
        world.org,
    ) == [(2,)]


async def test_an_agents_role_holds_nothing_its_owner_does_not(
    world: AgentWorld, seed: Seed
) -> None:
    # AC01 here also administers data, which the owner may not: the agent takes the owner's
    # own asking role instead.
    seed.row(
        "org.role",
        org_id=world.org,
        code="AC01",
        name="Nghiệp vụ",
        capabilities=["ask", "admin_data"],
    )
    async with client(world) as api:
        item = await new_agent(api, world)
    assert seats(seed, item["account_user_id"]) == [(f"AG{world.suffix}", None)]


async def test_a_config_reads_only_what_its_agents_data_space_has(
    world: AgentWorld, seed: Seed
) -> None:
    # R1-11: a config cannot point the agent at another space's metrics or skills.
    elsewhere = seed.row(
        "org.data_space",
        org_id=world.org,
        code=f"DS-X{world.suffix}",
        name="X",
        steward_id=world.owner,
    )
    seed.row(
        "context.metric",
        org_id=world.org,
        data_space_id=world.space,
        code="NPL_RATIO",
        owner_id=world.owner,
    )
    seed.row(
        "context.metric",
        org_id=world.org,
        data_space_id=elsewhere,
        code="FX_GAIN",
        owner_id=world.owner,
    )
    own_skill = seed.row(
        "skill.skill",
        org_id=world.org,
        data_space_id=world.space,
        code="s1",
        name="S1",
        category="custom",
        scope="data_space",
        owner_id=world.owner,
    )
    other_skill = seed.row(
        "skill.skill",
        org_id=world.org,
        data_space_id=elsewhere,
        code="s2",
        name="S2",
        category="custom",
        scope="data_space",
        owner_id=world.owner,
    )
    async with client(world) as api:
        item = await new_agent(api, world)
        url = f"/v1/agents/{item['id']}/configs"
        h = owner_headers(world)

        async def save(**config: Any) -> httpx.Response:
            return await api.post(url, headers=h, json={"goal": "g", **config})

        ok = await save(data_scope={"metrics": ["NPL_RATIO"]}, skill_ids=[str(own_skill)])
        assert ok.status_code == 201, ok.text
        foreign = await save(data_scope={"metrics": ["NPL_RATIO", "FX_GAIN"]})
        assert foreign.status_code == 422 and "FX_GAIN" in foreign.text, foreign.text
        assert (await save(data_scope={"tables": ["mart.nowhere"]})).status_code == 422
        assert (await save(data_scope={"anything": ["x"]})).status_code == 422
        assert (await save(skill_ids=[str(other_skill)])).status_code == 422


async def test_an_agent_is_seen_from_its_own_data_space_only(world: AgentWorld, seed: Seed) -> None:
    # R1-11: the same person, a member of two Data Spaces of one org, reaches an agent and its
    # runs only under the agent's space.
    second = seed.row(
        "org.data_space",
        org_id=world.org,
        code=f"DS-B{world.suffix}",
        name="Second space",
        steward_id=world.owner,
    )
    role = seed.row(
        "org.role",
        org_id=world.org,
        code=f"AGB{world.suffix}",
        name="Agent B",
        capabilities=["ask", "create_agent", "run_agent"],
    )
    seed.row(
        "org.data_space_member",
        org_id=world.org,
        data_space_id=second,
        principal_type="user",
        principal_id=world.owner,
        role_id=role,
    )
    async with client(world) as api:
        item = await new_agent(api, world, config={"goal": "g"})
        run_id = await started_run(api, world, item["id"])
        # A child run written in the other space is not listed under the parent's.
        seed.row(
            "agent.agent_run",
            org_id=world.org,
            agent_id=item["id"],
            config_version=1,
            data_space_id=second,
            trigger_kind="agent_call",
            parent_run_id=run_id,
            depth=1,
            status="queued",
        )
        children = await api.get(f"/v1/runs/{run_id}/children", headers=owner_headers(world))
        assert children.status_code == 200, children.text
        assert children.json()["items"] == []

        h = {**owner_headers(world), "X-Data-Space": str(second)}
        for path in (
            f"/v1/agents/{item['id']}",
            f"/v1/agents/{item['id']}/configs",
            f"/v1/agents/{item['id']}/memories",
            f"/v1/runs/{run_id}",
            f"/v1/runs/{run_id}/children",
        ):
            assert (await api.get(path, headers=h)).status_code == 404, path
        assert (await api.post(f"/v1/agents/{item['id']}/test-runs", headers=h)).status_code == 404
        listed = await api.get("/v1/agents", headers=h)
        assert [a["id"] for a in listed.json()["items"]] == []
        runs = await api.get("/v1/runs", headers=h)
        assert run_id not in [r["id"] for r in runs.json()["items"]]


# Proves AC-13-07-02, AC-13-07-03, AC-13-07-06.
async def test_memories(world: AgentWorld, seed: Seed) -> None:
    async with client(world) as api:
        item = await new_agent(api, world)
        url = f"/v1/agents/{item['id']}/memories"
        h = owner_headers(world)

        own = await api.post(
            url,
            headers=h,
            json={"scope": "agent", "kind": "preference", "content": "Luon hien don vi"},
        )
        assert own.status_code == 201, own.text
        assert own.json()["status"] == "active"
        assert own.json()["source_kind"] == "manual"
        mine = await api.post(
            url, headers=h, json={"scope": "user", "kind": "preference", "content": "Bang truoc"}
        )
        assert mine.json()["user_id"] == str(world.owner)

        shared = await api.post(
            url,
            headers=other_headers(world),
            json={"scope": "data_space", "kind": "domain_context", "content": "Ky chot ngay 5"},
        )
        assert shared.status_code == 201, shared.text
        assert shared.json()["status"] == "pending_review"
        assert shared.json()["agent_id"] is None
        shared_id = shared.json()["id"]
        not_owner = await api.patch(
            f"/v1/memories/{shared_id}", headers=other_headers(world), json={"status": "active"}
        )
        assert not_owner.status_code == 403, not_owner.text
        approved = await api.patch(
            f"/v1/memories/{shared_id}", headers=h, json={"status": "active"}
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "active"
        edited = await api.patch(
            f"/v1/memories/{shared_id}",
            headers=other_headers(world),
            json={"content": "Ky chot ngay 6"},
        )
        assert edited.json()["status"] == "pending_review"

        personal = await api.post(
            url,
            headers=h,
            json={
                "scope": "agent",
                "kind": "data_insight",
                "content": "Khach CCCD 001203004567 no xau",
            },
        )
        assert personal.status_code == 422, personal.text
        assert personal.json()["code"] == "business_rule_violated"
        balance = await api.post(
            url,
            headers=h,
            json={"scope": "agent", "kind": "data_insight", "content": "So du 1.250.000.000 VND"},
        )
        assert balance.status_code == 422, balance.text

        disabled = await api.patch(
            f"/v1/memories/{own.json()['id']}", headers=h, json={"status": "disabled"}
        )
        assert disabled.json()["status"] == "disabled"
        active = await api.get(f"{url}?status=active", headers=h)
        assert [m["id"] for m in active.json()["items"]] == [mine.json()["id"]]
        listed = await api.get(f"{url}?scope=data_space", headers=h)
        assert [m["id"] for m in listed.json()["items"]] == [shared_id]

        deleted = await api.delete(f"/v1/memories/{own.json()['id']}", headers=h)
        assert deleted.status_code == 204, deleted.text
        assert deleted.content == b""
        gone = await api.patch(
            f"/v1/memories/{own.json()['id']}", headers=h, json={"status": "active"}
        )
        assert gone.status_code == 404

    assert [(e, p.get("status")) for _, e, p in events(seed, shared_id)] == [
        ("agent_memory.created", "pending_review"),
        ("agent_memory.status_changed", "active"),
        ("agent_memory.status_changed", "pending_review"),
    ]
    assert [e for _, e, _ in events(seed, own.json()["id"])] == [
        "agent_memory.created",
        "agent_memory.status_changed",
        "agent_memory.deleted",
    ]


async def test_list_says_the_goal_the_trigger_and_the_run_title(world: AgentWorld) -> None:
    async with client(world) as api:
        created = await new_agent(
            api,
            world,
            config={
                "goal": "Giai thich bien gop thang truoc",
                "tools": [
                    {"tool": "query", "permission_level": "read"},
                    {
                        "tool": "create_proposal",
                        "permission_level": "write_derived",
                        "requires_checkpoint": True,
                    },
                ],
            },
        )
        saved = await api.put(
            f"/v1/agents/{created['id']}/triggers",
            headers=owner_headers(world),
            json={"triggers": [{"kind": "schedule", "cron": "0 7 * * 1"}]},
        )
        assert saved.status_code == 200, saved.text
        listed = await api.get("/v1/agents", headers=owner_headers(world))
        assert listed.status_code == 200, listed.text
        item = next(row for row in listed.json()["items"] if row["id"] == created["id"])
        assert item["goal"] == "Giai thich bien gop thang truoc"
        assert item["permission_text"] == "Đọc · Đề xuất"
        assert item["trigger_text"] == "Theo lịch 07:00 thứ Hai"
        assert item["runs_7d"] is None
        assert item["active_deployment_id"] is None

        started = await api.post(
            f"/v1/agents/{created['id']}/test-runs", headers=owner_headers(world)
        )
        assert started.status_code == 202, started.text
        # A test run is not a production run, so the week stays empty and the title is the goal.
        runs = await api.get(f"/v1/runs?agent_id={created['id']}", headers=owner_headers(world))
        assert runs.status_code == 200, runs.text
        assert runs.json()["items"][0]["title"] == "Giai thich bien gop thang truoc"
        assert runs.json()["items"][0]["hitl_count"] == 0
        again = await api.get("/v1/agents", headers=owner_headers(world))
        item = next(row for row in again.json()["items"] if row["id"] == created["id"])
        assert item["runs_7d"] is None
