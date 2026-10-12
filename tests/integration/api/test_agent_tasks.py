"""Việc nền giao cho agent (UC-08-05, BR-126) against PostgreSQL, on the organisation of
test_monitor.py: the runner holds run_agent; the owner only asks."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from .conftest import Seed
from .test_monitor import Org, build, client, member, user

pytestmark = [pytest.mark.integration, pytest.mark.contract]


def agent(
    ag: Org, space: uuid.UUID, code: str, kind: str = "analysis", status: str = "running"
) -> uuid.UUID:
    return ag.seed.row(
        "agent.agent_def",
        org_id=ag.org,
        data_space_id=space,
        code=code,
        name=code,
        kind=kind,
        owner_user_id=ag.users["steward"],
        account_user_id=ag.users["steward"],
        status=status,
    )


@pytest.fixture
def ag(seed: Seed) -> Org:
    mon = build(seed)
    mon.users["runner2"] = user(seed, mon.org, "runner2", mon.roles["runner"])
    member(seed, mon.org, mon.space, mon.users["runner2"], mon.roles["runner"])
    mon.users["analysis"] = agent(mon, mon.space, "phan_tich")
    mon.users["watcher"] = agent(mon, mon.space, "theo_doi", kind="monitor")
    mon.users["paused"] = agent(mon, mon.space, "tam_dung", status="paused")
    mon.users["elsewhere"] = agent(mon, mon.other, "phan_tich_b")
    return mon


def _task(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "description": "Rà toàn bộ hồ sơ vay T9 có tài sản đảm bảo định giá quá 12 tháng",
        "due_at": "2026-10-20T17:00:00+07:00",
    }
    body.update(extra)
    return body


# AC-08-05-02
async def test_a_task_waits_for_the_requester_to_confirm_the_plan(ag: Org) -> None:
    async with client(ag) as api:
        made = await api.post("/v1/tasks", json=_task(), headers=ag.headers("runner"))
        assert made.status_code == 202, made.text
        task = made.json()["id"]
        assert made.headers["Location"] == f"/v1/tasks/{task}"
        assert made.json()["status"] == "pending_confirmation" and made.json()["poll_after_ms"] > 0
        read = await api.get(f"/v1/tasks/{task}", headers=ag.headers("runner"))
        assert read.status_code == 200, read.text
        body = read.json()
        # Without agent_id the space's running analysis agent takes it (UC-08-05 step 1).
        assert body["agent_id"] == str(ag.users["analysis"])
        assert body["requested_by"] == str(ag.users["runner"]) and body["run_id"] is None
        assert body["due_at"].startswith("2026-10-20T10:00:00")
    events = ag.events("agent", task)
    assert [e for e, _ in events] == ["task.created"]
    assert events[0][1]["task_id"] == task and events[0][1]["agent_id"] == str(ag.users["analysis"])
    assert events[0][1]["data_space_id"] == str(ag.space)
    assert events[0][1]["requested_by"] == str(ag.users["runner"])


async def test_the_task_goes_to_a_running_agent_of_the_space(ag: Org) -> None:
    async with client(ag) as api:
        chosen = await api.post(
            "/v1/tasks", json=_task(agent_id=str(ag.users["watcher"])), headers=ag.headers("runner")
        )
        assert chosen.status_code == 202, chosen.text
        assert ag.one(
            "SELECT agent_id FROM agent.background_task WHERE id = %s", chosen.json()["id"]
        ) == str(ag.users["watcher"])
        paused = await api.post(
            "/v1/tasks", json=_task(agent_id=str(ag.users["paused"])), headers=ag.headers("runner")
        )
        assert paused.status_code == 409 and paused.json()["code"] == "invalid_state"
        for agent_id in (ag.users["elsewhere"], uuid.uuid4()):
            refused = await api.post(
                "/v1/tasks", json=_task(agent_id=str(agent_id)), headers=ag.headers("runner")
            )
            assert refused.status_code == 422
            assert refused.json()["errors"][0]["field"] == "agent_id"
        for extra in ({"due_at": "2026-10-20T17:00:00"}, {"description": ""}):
            bad = await api.post("/v1/tasks", json=_task(**extra), headers=ag.headers("runner"))
            assert bad.status_code == 422, extra
        no_cap = await api.post("/v1/tasks", json=_task(), headers=ag.headers("owner"))
        assert no_cap.status_code == 403
        not_member = await api.post(
            "/v1/tasks", json=_task(), headers=ag.headers("colleague", ag.other)
        )
        assert not_member.status_code == 404
    ag.run("UPDATE agent.agent_def SET status = 'paused' WHERE id = %s", ag.users["analysis"])
    async with client(ag) as api:
        nobody = await api.post("/v1/tasks", json=_task(), headers=ag.headers("runner"))
        assert nobody.status_code == 422 and nobody.json()["errors"][0]["field"] == "agent_id"
    assert ag.one("SELECT count(*) FROM agent.background_task WHERE org_id = %s", ag.org) == 1


async def test_a_task_is_its_requesters(ag: Org) -> None:
    async with client(ag) as api:
        task = (await api.post("/v1/tasks", json=_task(), headers=ag.headers("runner"))).json()[
            "id"
        ]
        for call in (
            api.get(f"/v1/tasks/{task}", headers=ag.headers("runner2")),
            api.post(f"/v1/tasks/{task}:cancel", headers=ag.headers("runner2")),
            api.get(f"/v1/tasks/{task}", headers=ag.headers("runner", ag.other)),
            api.post(f"/v1/tasks/{task}:cancel", headers=ag.headers("runner", ag.other)),
            api.get(f"/v1/tasks/{uuid.uuid4()}", headers=ag.headers("runner")),
        ):
            assert (await call).status_code == 404
        for call in (
            api.get(f"/v1/tasks/{task}", headers=ag.headers("owner")),
            api.post(f"/v1/tasks/{task}:cancel", headers=ag.headers("owner")),
        ):
            assert (await call).status_code == 403
    assert (
        ag.one("SELECT status FROM agent.background_task WHERE id = %s", task)
        == "pending_confirmation"
    )


async def test_cancel_a_task(ag: Org) -> None:
    async with client(ag) as api:
        pending = (await api.post("/v1/tasks", json=_task(), headers=ag.headers("runner"))).json()[
            "id"
        ]
        running = (await api.post("/v1/tasks", json=_task(), headers=ag.headers("runner"))).json()[
            "id"
        ]
        done = (await api.post("/v1/tasks", json=_task(), headers=ag.headers("runner"))).json()[
            "id"
        ]
        ag.run("UPDATE agent.background_task SET status = 'running' WHERE id = %s", running)
        ag.run("UPDATE agent.background_task SET status = 'done' WHERE id = %s", done)
        for task, previous in ((pending, "pending_confirmation"), (running, "running")):
            cancelled = await api.post(f"/v1/tasks/{task}:cancel", headers=ag.headers("runner"))
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["status"] == "cancelled"
            event = [p for e, p in ag.events("agent", task) if e == "task.cancelled"]
            assert event[0]["previous_status"] == previous and event[0]["status"] == "cancelled"
        again = await api.post(f"/v1/tasks/{pending}:cancel", headers=ag.headers("runner"))
        assert again.status_code == 409
        finished = await api.post(f"/v1/tasks/{done}:cancel", headers=ag.headers("runner"))
        assert finished.status_code == 409 and finished.json()["code"] == "invalid_state"
    assert ag.one("SELECT status FROM agent.background_task WHERE id = %s", done) == "done"


async def test_results_go_to_a_session_of_the_same_space(ag: Org) -> None:
    def session(owner: str, space: uuid.UUID) -> uuid.UUID:
        return ag.seed.row(
            "workspace.workspace",
            org_id=ag.org,
            data_space_id=space,
            owner_id=ag.users[owner],
            title=f"Phiên của {owner}",
        )

    mine = session("runner", ag.space)
    theirs = session("runner2", ag.space)
    elsewhere = session("runner", ag.other)
    async with client(ag) as api:
        ok = await api.post(
            "/v1/tasks", json=_task(workspace_id=str(mine)), headers=ag.headers("runner")
        )
        assert ok.status_code == 202, ok.text
        assert ag.one(
            "SELECT workspace_id FROM agent.background_task WHERE id = %s", ok.json()["id"]
        ) == str(mine)
        # Another person's session, or one of another Data Space, never takes the results.
        for target in (theirs, elsewhere, uuid.uuid4()):
            refused = await api.post(
                "/v1/tasks", json=_task(workspace_id=str(target)), headers=ag.headers("runner")
            )
            assert refused.status_code == 422, refused.text
            assert refused.json()["errors"][0]["field"] == "workspace_id"
