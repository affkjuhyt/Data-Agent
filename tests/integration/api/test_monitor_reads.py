"""Monitors and scheduled reports read back, and a closed alert reopened (UC-08-01, UC-08-02,
UC-08-03, BR-34): GET /v1/monitors, POST /v1/alerts/{id}:reopen, GET /v1/reports and
GET /v1/reports/{id} against PostgreSQL, on the organisation of test_monitor.py."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from psycopg2.extras import Json

from .conftest import Seed
from .test_monitor import Org, build, client, seed_alert, seed_monitor

pytestmark = [pytest.mark.integration, pytest.mark.contract]

REASON = {"reason": "Đóng nhầm: số liệu T9 đã được nạp lại và vẫn vượt ngưỡng"}


@pytest.fixture
def mon(seed: Seed) -> Org:
    return build(seed)


# ---------------------------------------------------------------- monitors


async def test_monitors_of_the_data_space_are_listed(mon: Org) -> None:
    first = seed_monitor(mon)
    second = seed_monitor(mon)
    mon.run("UPDATE monitor.monitor SET status = 'paused' WHERE id = %s", second)
    gone = seed_monitor(mon)
    mon.run("UPDATE monitor.monitor SET deleted_at = now() WHERE id = %s", gone)
    seed_monitor(mon, mon.other, mon.other_metric)
    async with client(mon) as api:
        listed = await api.get("/v1/monitors", headers=mon.headers("colleague"))
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        # The other Data Space's monitor and the deleted one are not listed.
        assert {m["id"] for m in items} == {str(first), str(second)}
        assert items[0]["rule_kind"] == "threshold" and "noise_warning" in items[0]
        paused = await api.get(
            "/v1/monitors", params={"status": "paused"}, headers=mon.headers("colleague")
        )
        assert [m["id"] for m in paused.json()["items"]] == [str(second)]
        page = await api.get("/v1/monitors", params={"page_size": 1}, headers=mon.headers("owner"))
        token = page.json()["next_page_token"]
        rest = await api.get(
            "/v1/monitors", params={"page_size": 1, "cursor": token}, headers=mon.headers("owner")
        )
        assert {page.json()["items"][0]["id"], rest.json()["items"][0]["id"]} == {
            str(first),
            str(second),
        }
        assert rest.json()["next_page_token"] == ""
        assert (await api.get("/v1/monitors", headers=mon.headers("nocap"))).status_code == 403


# ---------------------------------------------------------------- reopen an alert


async def test_a_closed_alert_is_reopened_and_the_reason_audited(mon: Org) -> None:
    monitor_id = seed_monitor(mon)
    alert = seed_alert(
        mon, monitor_id, status="closed", closed_at="2026-10-01T00:00:00Z", close_note="Báo nhầm"
    )
    async with client(mon) as api:
        done = await api.post(
            f"/v1/alerts/{alert}:reopen", json=REASON, headers=mon.headers("colleague")
        )
        assert done.status_code == 200, done.text
        body = done.json()
        assert body["status"] == "open" and body["closed_at"] is None and body["close_note"] is None
        assert mon.one("SELECT status FROM monitor.alert WHERE id = %s", alert) == "open"
        events = dict(mon.events("monitor", alert))
        domain = events["alert.reopened"]
        assert domain["previous_status"] == "closed" and domain["status"] == "open"
        # The reason is the audit record's, not the domain event's.
        assert REASON["reason"] not in str(domain)
        audit = events["audit.requested"]
        assert audit["action"] == "alert.reopen" and audit["after"]["reason"] == REASON["reason"]
        assert audit["before"] == {"status": "closed", "close_note": "Báo nhầm"}

        again = await api.post(
            f"/v1/alerts/{alert}:reopen", json=REASON, headers=mon.headers("colleague")
        )
        assert again.status_code == 409 and again.json()["code"] == "invalid_state"


async def test_only_a_closed_alert_with_a_reason_is_reopened(mon: Org) -> None:
    monitor_id = seed_monitor(mon)
    acked = seed_alert(mon, monitor_id, status="ack")
    closed = seed_alert(mon, monitor_id, status="closed", closed_at="2026-10-01T00:00:00Z")
    # The monitor has fired again on the same rule and period since it was closed.
    key = mon.one("SELECT dedup_key FROM monitor.alert WHERE id = %s", closed)
    seed_alert(mon, monitor_id, dedup_key=key)
    elsewhere = seed_alert(
        mon,
        seed_monitor(mon, mon.other, mon.other_metric),
        status="closed",
        closed_at="2026-10-01T00:00:00Z",
    )
    async with client(mon) as api:
        open_one = await api.post(
            f"/v1/alerts/{acked}:reopen", json=REASON, headers=mon.headers("owner")
        )
        assert open_one.status_code == 409 and open_one.json()["code"] == "invalid_state"
        twice = await api.post(
            f"/v1/alerts/{closed}:reopen", json=REASON, headers=mon.headers("owner")
        )
        assert twice.status_code == 409, twice.text
        for body in ({}, {"reason": " "}):
            refused = await api.post(
                f"/v1/alerts/{closed}:reopen", json=body, headers=mon.headers("owner")
            )
            assert refused.status_code == 422, (body, refused.text)
        other_space = await api.post(
            f"/v1/alerts/{elsewhere}:reopen", json=REASON, headers=mon.headers("owner")
        )
        assert other_space.status_code == 404
        denied = await api.post(
            f"/v1/alerts/{closed}:reopen", json=REASON, headers=mon.headers("nocap")
        )
        assert denied.status_code == 403
    assert mon.one("SELECT status FROM monitor.alert WHERE id = %s", closed) == "closed"
    assert mon.events("monitor", closed) == []


# ---------------------------------------------------------------- reports


def _schedule(mon: Org, title: str, owner: str = "owner", **values: Any) -> uuid.UUID:
    return mon.seed.row(
        "monitor.report_schedule",
        org_id=mon.org,
        data_space_id=values.pop("space", mon.space),
        kind="digest",
        owner_id=mon.users[owner],
        title=title,
        cron="30 7 * * 1",
        as_of_rule="end_of_previous_day",
        **values,
    )


def _recipient(mon: Org, schedule: uuid.UUID, who: str, group: str) -> None:
    mon.seed.row(
        "monitor.report_recipient",
        org_id=mon.org,
        report_schedule_id=schedule,
        principal_type="user",
        principal_id=mon.users[who],
        permission_group=group,
    )


async def test_reports_are_listed_to_their_owner_and_recipients(mon: Org) -> None:
    mine = _schedule(mon, "Bản tin tuần")
    shared = _schedule(mon, "Báo cáo nợ xấu", owner="steward", status="paused")
    _recipient(mon, shared, "owner", "full")
    _schedule(mon, "Của người khác", owner="steward")
    _schedule(mon, "Không gian khác", space=mon.other)
    async with client(mon) as api:
        listed = await api.get("/v1/reports", headers=mon.headers("owner"))
        assert listed.status_code == 200, listed.text
        # Not the steward's schedule the owner does not receive, nor the other space's.
        assert {r["id"] for r in listed.json()["items"]} == {str(mine), str(shared)}
        paused = await api.get(
            "/v1/reports", params={"status": "paused"}, headers=mon.headers("owner")
        )
        assert [r["id"] for r in paused.json()["items"]] == [str(shared)]
        (row,) = paused.json()["items"]
        assert row["recipients"][0]["principal_id"] == str(mon.users["owner"])
        colleague = await api.get("/v1/reports", headers=mon.headers("colleague"))
        assert colleague.json()["items"] == []
        assert (await api.get("/v1/reports", headers=mon.headers("nocap"))).status_code == 403


async def test_one_report_shows_its_newest_run_as_the_reader_may_see_it(mon: Org) -> None:
    schedule = _schedule(mon, "Bản tin tuần")
    _recipient(mon, schedule, "colleague", "limited")
    outputs = [
        {"permission_group": "full", "format": "pdf", "object_key": "reports/full.pdf"},
        {"permission_group": "limited", "format": "pdf", "object_key": "reports/limited.pdf"},
    ]
    for as_of, status in (("2026-09-28", "sent"), ("2026-10-05", "partially_sent")):
        mon.seed.row(
            "monitor.report_run",
            org_id=mon.org,
            report_schedule_id=schedule,
            kind="digest",
            as_of=as_of,
            trigger="schedule",
            outputs=Json(outputs),
            status=status,
        )
    elsewhere = _schedule(mon, "Không gian khác", space=mon.other)
    async with client(mon) as api:
        owner = await api.get(f"/v1/reports/{schedule}", headers=mon.headers("owner"))
        assert owner.status_code == 200, owner.text
        body = owner.json()
        assert body["title"] == "Bản tin tuần" and body["cron"] == "30 7 * * 1"
        assert [r["principal_id"] for r in body["recipients"]] == [str(mon.users["colleague"])]
        run = body["latest_run"]
        assert run["as_of"] == "2026-10-05" and run["status"] == "partially_sent"
        # The owner sees every rendering; no storage key leaves the api.
        assert [o["permission_group"] for o in run["outputs"]] == ["full", "limited"]
        assert all("object_key" not in o for o in run["outputs"])
        reader = await api.get(f"/v1/reports/{schedule}", headers=mon.headers("colleague"))
        assert reader.status_code == 200
        assert [o["permission_group"] for o in reader.json()["latest_run"]["outputs"]] == [
            "limited"
        ]
        stranger = await api.get(f"/v1/reports/{schedule}", headers=mon.headers("runner"))
        assert stranger.status_code == 404 and stranger.json()["code"] == "not_found"
        other_space = await api.get(f"/v1/reports/{elsewhere}", headers=mon.headers("owner"))
        assert other_space.status_code == 404
        missing = await api.get(f"/v1/reports/{uuid.uuid4()}", headers=mon.headers("owner"))
        assert missing.status_code == 404
        denied = await api.get(f"/v1/reports/{schedule}", headers=mon.headers("nocap"))
        assert denied.status_code == 403
