"""Báo cáo định kỳ, lượt chạy and bản tin lãnh đạo (UC-02-04, UC-08-03, UC-08-04, BR-34) against
PostgreSQL, on the organisation of test_monitor.py."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import httpx
import pytest
from psycopg2.extras import Json

from .conftest import Seed
from .test_monitor import Org, build, client

pytestmark = [pytest.mark.integration, pytest.mark.contract]


@pytest.fixture
def rep(seed: Seed) -> Org:
    mon = build(seed)
    mon.users["workspace"] = seed.row(
        "workspace.workspace",
        org_id=mon.org,
        data_space_id=mon.space,
        owner_id=mon.users["owner"],
        title="Nợ xấu theo vùng",
    )
    mon.users["other_workspace"] = seed.row(
        "workspace.workspace",
        org_id=mon.org,
        data_space_id=mon.other,
        owner_id=mon.users["owner"],
        title="Khác",
    )
    mon.users["calendar"] = seed.row(
        "org.work_calendar", org_id=mon.org, code="VN", name="Lịch làm việc VN"
    )
    # A group of one: the runner, whose role in the space differs from the colleague's.
    mon.users["group"] = seed.row(
        "org.user_group", org_id=mon.org, external_id="g-risk", name="Rủi ro", source="local"
    )
    seed.row(
        "org.user_group_member",
        org_id=mon.org,
        group_id=mon.users["group"],
        user_id=mon.users["runner"],
    )
    return mon


def _body(rep: Org, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "workspace_id": str(rep.users["workspace"]),
        "title": "Báo cáo nợ xấu tuần",
        "cron": "0 8 * * 1",
        "work_calendar_id": str(rep.users["calendar"]),
        "cutoff_time": "07:00:00",
        "as_of_rule": "end_of_previous_week",
        "formats": ["pdf", "xlsx"],
        "recipients": [
            {"principal_id": str(rep.users["colleague"]), "channels": ["email", "teams"]},
            {"principal_type": "group", "principal_id": str(rep.users["group"])},
            {"principal_id": str(rep.users["owner"]), "channels": ["in_app"]},
        ],
    }
    body.update(extra)
    return body


async def _create(api: httpx.AsyncClient, rep: Org, **extra: Any) -> dict[str, Any]:
    made = await api.post("/v1/reports", json=_body(rep, **extra), headers=rep.headers("owner"))
    assert made.status_code == 201, made.text
    out: dict[str, Any] = made.json()
    return out


# AC-02-04-01
async def test_owner_schedules_a_report_for_three_recipients(rep: Org) -> None:
    async with client(rep) as api:
        made = await _create(api, rep)
    assert made["status"] == "active" and made["kind"] == "report"
    assert made["work_calendar_id"] == str(rep.users["calendar"])
    assert made["formats"] == ["pdf", "xlsx"] and made["cutoff_time"] == "07:00:00"
    recipients = {r["principal_id"]: r for r in made["recipients"]}
    assert len(recipients) == 3
    assert recipients[str(rep.users["colleague"])]["channels"] == ["email", "teams"]
    assert recipients[str(rep.users["group"])]["channels"] == ["email"]
    # BR-34: same grants in the space, same rendering; the group's grants differ.
    colleague = recipients[str(rep.users["colleague"])]["permission_group"]
    assert colleague == recipients[str(rep.users["owner"])]["permission_group"]
    assert colleague != recipients[str(rep.users["group"])]["permission_group"]
    assert (
        rep.one(
            "SELECT count(*) FROM monitor.report_recipient WHERE report_schedule_id = %s",
            made["id"],
        )
        == 3
    )
    events = rep.events("monitor", made["id"])
    assert [e for e, _ in events] == ["report_schedule.created"]
    assert events[0][1]["report_schedule_id"] == made["id"]
    assert events[0][1]["cron"] == "0 8 * * 1" and events[0][1]["data_space_id"] == str(rep.space)


async def test_a_report_is_checked_before_it_is_scheduled(rep: Org) -> None:
    async with client(rep) as api:
        cases: list[tuple[dict[str, Any], str | None]] = [
            ({"workspace_id": None}, "workspace_id"),
            ({"workspace_id": str(rep.users["other_workspace"])}, "workspace_id"),
            ({"work_calendar_id": str(uuid.uuid4())}, "work_calendar_id"),
            ({"recipients": [{"principal_id": str(uuid.uuid4())}]}, "recipients"),
            ({"recipients": [{"principal_id": str(rep.users["gone"])}]}, "recipients"),
            (
                {
                    "recipients": [
                        {"principal_id": str(rep.users["colleague"])},
                        {"principal_id": str(rep.users["colleague"])},
                    ]
                },
                None,
            ),
            ({"cron": "weekly"}, None),
            ({"formats": ["docx"]}, None),
            ({"formats": []}, None),
        ]
        for extra, field in cases:
            refused = await api.post(
                "/v1/reports", json=_body(rep, **extra), headers=rep.headers("owner")
            )
            assert refused.status_code == 422, (extra, refused.text)
            assert refused.json()["code"] == "validation_failed"
            if field:
                assert refused.json()["errors"][0]["field"] == field, extra
        no_cap = await api.post("/v1/reports", json=_body(rep), headers=rep.headers("nocap"))
        assert no_cap.status_code == 403
        # A digest is built without a session.
        digest = await api.post(
            "/v1/reports",
            json=_body(rep, kind="digest", workspace_id=None, recipients=[]),
            headers=rep.headers("owner"),
        )
        assert digest.status_code == 201, digest.text
    assert rep.one("SELECT count(*) FROM monitor.report_schedule WHERE org_id = %s", rep.org) == 1


async def test_only_the_owner_changes_pauses_or_runs_a_report(rep: Org) -> None:
    async with client(rep) as api:
        made = await _create(api, rep)
        url = f"/v1/reports/{made['id']}"
        for call in (
            api.patch(url, json={"title": "Của tôi"}, headers=rep.headers("colleague")),
            api.post(f"{url}:pause", headers=rep.headers("colleague")),
            api.post(f"{url}:run-now", json={}, headers=rep.headers("colleague")),
        ):
            assert (await call).status_code == 403
        for call in (
            api.patch(url, json={"title": "x"}, headers=rep.headers("nocap")),
            api.post(f"{url}:pause", headers=rep.headers("nocap")),
            api.post(f"{url}:run-now", json={}, headers=rep.headers("nocap")),
        ):
            assert (await call).status_code == 403
        for call in (
            api.patch(url, json={"title": "x"}, headers=rep.headers("owner", rep.other)),
            api.post(f"{url}:pause", headers=rep.headers("owner", rep.other)),
            api.post(f"{url}:run-now", json={}, headers=rep.headers("owner", rep.other)),
            api.patch(
                f"/v1/reports/{uuid.uuid4()}", json={"title": "x"}, headers=rep.headers("owner")
            ),
        ):
            assert (await call).status_code == 404
    assert [e for e, _ in rep.events("monitor", made["id"])] == ["report_schedule.created"]


async def test_patch_replaces_the_recipients(rep: Org) -> None:
    async with client(rep) as api:
        made = await _create(api, rep)
        url = f"/v1/reports/{made['id']}"
        null = await api.patch(url, json={"cron": None}, headers=rep.headers("owner"))
        assert null.status_code == 422
        bad_calendar = await api.patch(
            url, json={"work_calendar_id": str(uuid.uuid4())}, headers=rep.headers("owner")
        )
        assert bad_calendar.status_code == 422
        changed = await api.patch(
            url,
            json={
                "cron": "0 8 1 * *",
                "as_of_rule": "end_of_previous_month",
                "work_calendar_id": None,
                "recipients": [{"principal_id": str(rep.users["steward"]), "channels": ["zalo"]}],
            },
            headers=rep.headers("owner"),
        )
        assert changed.status_code == 200, changed.text
        body = changed.json()
        assert body["cron"] == "0 8 1 * *" and body["work_calendar_id"] is None
        assert [(r["principal_id"], r["channels"]) for r in body["recipients"]] == [
            (str(rep.users["steward"]), ["zalo"])
        ]
    updated = [p for e, p in rep.events("monitor", made["id"]) if e == "report_schedule.updated"]
    assert updated[0]["changed"] == ["as_of_rule", "cron", "recipients", "work_calendar_id"]


async def test_pause_a_report(rep: Org) -> None:
    async with client(rep) as api:
        made = await _create(api, rep)
        paused = await api.post(f"/v1/reports/{made['id']}:pause", headers=rep.headers("owner"))
        assert paused.status_code == 200, paused.text
        assert paused.json()["status"] == "paused" and len(paused.json()["recipients"]) == 3
        again = await api.post(f"/v1/reports/{made['id']}:pause", headers=rep.headers("owner"))
        assert again.status_code == 409 and again.json()["code"] == "invalid_state"
    events = rep.events("monitor", made["id"])
    assert [e for e, _ in events] == ["report_schedule.created", "report_schedule.paused"]
    assert events[1][1]["previous_status"] == "active" and events[1][1]["status"] == "paused"


# AC-08-03-03
async def test_run_now_takes_the_day_the_owner_picks(rep: Org) -> None:
    async with client(rep) as api:
        made = await _create(api, rep)
        url = f"/v1/reports/{made['id']}:run-now"
        picked = await api.post(url, json={"as_of": "2026-10-15"}, headers=rep.headers("owner"))
        assert picked.status_code == 202, picked.text
        run = picked.json()["id"]
        assert picked.headers["Location"] == f"/v1/report-runs/{run}"
        assert picked.json()["status"] == "running" and picked.json()["poll_after_ms"] > 0
        today = await api.post(url, headers=rep.headers("owner"))
        assert today.status_code == 202, today.text
        bad = await api.post(url, json={"as_of": "15/10/2026"}, headers=rep.headers("owner"))
        assert bad.status_code == 422
    assert str(rep.one("SELECT as_of FROM monitor.report_run WHERE id = %s", run)) == "2026-10-15"
    assert rep.one("SELECT trigger FROM monitor.report_run WHERE id = %s", run) == "run_now"
    assert (
        rep.one("SELECT as_of FROM monitor.report_run WHERE id = %s", today.json()["id"])
        == datetime.datetime.now(datetime.UTC).date()
    )
    events = rep.events("monitor", run)
    assert [e for e, _ in events] == ["report_run.requested"]
    assert (
        events[0][1]["report_schedule_id"] == made["id"] and events[0][1]["as_of"] == "2026-10-15"
    )
    assert events[0][1]["requested_by"] == str(rep.users["owner"])


# AC-08-03-05
async def test_each_reader_sees_only_their_permission_group_rendering(rep: Org) -> None:
    async with client(rep) as api:
        made = await _create(api, rep)
        groups = {r["principal_id"]: r["permission_group"] for r in made["recipients"]}
        north, south = groups[str(rep.users["colleague"])], groups[str(rep.users["group"])]
        started = await api.post(
            f"/v1/reports/{made['id']}:run-now",
            json={"as_of": "2026-10-31"},
            headers=rep.headers("owner"),
        )
        run = started.json()["id"]
        # What the report agent writes when the run has rendered one file per permission group.
        rep.run(
            "UPDATE monitor.report_run SET status = 'sent', finished_at = now(), outputs = %s"
            " WHERE id = %s",
            Json(
                [
                    {"permission_group": north, "format": "pdf", "object_key": "r/north.pdf"},
                    {"permission_group": south, "format": "pdf", "object_key": "r/south.pdf"},
                ]
            ),
            run,
        )
        url = f"/v1/report-runs/{run}"
        owner = await api.get(url, headers=rep.headers("owner"))
        assert owner.status_code == 200, owner.text
        assert owner.json()["status"] == "sent" and owner.json()["as_of"] == "2026-10-31"
        assert {o["permission_group"] for o in owner.json()["outputs"]} == {north, south}
        assert all("object_key" not in o for o in owner.json()["outputs"])
        colleague = await api.get(url, headers=rep.headers("colleague"))
        assert [o["permission_group"] for o in colleague.json()["outputs"]] == [north]
        # The runner reads it as a member of the recipient group.
        runner = await api.get(url, headers=rep.headers("runner"))
        assert [o["permission_group"] for o in runner.json()["outputs"]] == [south]
        not_a_recipient = await api.get(url, headers=rep.headers("steward"))
        assert not_a_recipient.status_code == 404
        other_space = await api.get(url, headers=rep.headers("owner", rep.other))
        assert other_space.status_code == 404
        no_cap = await api.get(url, headers=rep.headers("nocap"))
        assert no_cap.status_code == 403
        missing = await api.get(f"/v1/report-runs/{uuid.uuid4()}", headers=rep.headers("owner"))
        assert missing.status_code == 404


async def test_the_leader_turns_the_digest_on_and_off(rep: Org) -> None:
    async with client(rep) as api:
        on = await api.put(
            "/v1/me/digest", json={"metrics": [str(rep.metric)]}, headers=rep.headers("colleague")
        )
        assert on.status_code == 200, on.text
        digest = on.json()
        assert digest["kind"] == "digest" and digest["status"] == "active"
        assert digest["cron"] == "30 7 * * 1" and digest["owner_id"] == str(rep.users["colleague"])
        assert [(r["principal_id"], r["channels"]) for r in digest["recipients"]] == [
            (str(rep.users["colleague"]), ["in_app"])
        ]
        off = await api.put(
            "/v1/me/digest",
            json={"enabled": False, "schedule": "0 7 * * *"},
            headers=rep.headers("colleague"),
        )
        assert off.status_code == 200, off.text
        assert off.json()["id"] == digest["id"]
        assert off.json()["status"] == "paused" and off.json()["cron"] == "0 7 * * *"
        for metrics in ([str(rep.other_metric)], [str(rep.draft_metric)]):
            refused = await api.put(
                "/v1/me/digest", json={"metrics": metrics}, headers=rep.headers("colleague")
            )
            assert refused.status_code == 422
        bad_cron = await api.put(
            "/v1/me/digest", json={"schedule": "monday"}, headers=rep.headers("colleague")
        )
        assert bad_cron.status_code == 422
        no_cap = await api.put("/v1/me/digest", json={}, headers=rep.headers("nocap"))
        assert no_cap.status_code == 403
        # One digest per reader and Data Space: the owner's is their own.
        owners = await api.put("/v1/me/digest", json={}, headers=rep.headers("owner"))
        assert owners.json()["id"] != digest["id"]
    events = rep.events("monitor", digest["id"])
    assert [e for e, _ in events] == ["report_schedule.created", "report_schedule.updated"]
    assert events[0][1]["metric_ids"] == [str(rep.metric)]
    assert events[1][1]["status"] == "paused"
    assert (
        rep.one(
            "SELECT count(*) FROM monitor.report_schedule WHERE owner_id = %s AND kind = 'digest'",
            rep.users["colleague"],
        )
        == 1
    )
