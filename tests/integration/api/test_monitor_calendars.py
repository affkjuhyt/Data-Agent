"""Lịch sự kiện ngoài, kỳ and bản tin (UC-08-08, BR-187) against PostgreSQL, on the organisation of
test_monitor.py: the steward keeps the calendars, the others follow them."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import httpx
import pytest

from .conftest import Seed
from .test_monitor import Org, build, client

pytestmark = [pytest.mark.integration, pytest.mark.contract]

ICT = datetime.timezone(datetime.timedelta(hours=7))


@pytest.fixture
def cal(seed: Seed) -> Org:
    return build(seed)


def _body(**extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": "ky_dieu_hanh_xang_dau",
        "name": "Kỳ điều hành giá xăng dầu",
        "legal_basis": "Nghị định 80/2023/NĐ-CP",
        "recurrence_hint": "Thứ Năm hằng tuần",
        "default_local_time": "15:00:00",
        "forecast_model_ids": [str(uuid.uuid4())],
    }
    body.update(extra)
    return body


async def _create(api: httpx.AsyncClient, cal: Org, **extra: Any) -> dict[str, Any]:
    made = await api.post(
        "/v1/external-calendars", json=_body(**extra), headers=cal.headers("steward")
    )
    assert made.status_code == 201, made.text
    out: dict[str, Any] = made.json()
    return out


def seed_event(cal: Org, calendar: object, expected_at: str, **values: Any) -> uuid.UUID:
    values.setdefault("origin", "recurrence")
    return cal.seed.row(
        "monitor.external_event",
        org_id=cal.org,
        calendar_id=calendar,
        period_label=expected_at[:10],
        expected_at=expected_at,
        **values,
    )


def seed_calendar(cal: Org, space: uuid.UUID) -> uuid.UUID:
    return cal.seed.row(
        "monitor.external_calendar",
        org_id=cal.org,
        data_space_id=space,
        code=f"lich_{uuid.uuid4().hex[:6]}",
        name="Lịch khác",
        owner_id=cal.users["steward"],
        recurrence_hint="hằng tháng",
    )


def _at(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


async def test_steward_declares_a_calendar(cal: Org) -> None:
    async with client(cal) as api:
        made = await _create(api, cal)
        assert made["status"] == "active" and made["owner_id"] == str(cal.users["steward"])
        assert made["timezone"] == "Asia/Ho_Chi_Minh"
        assert (
            made["pre_bulletin_days_before"] == 1 and made["pre_bulletin_local_time"] == "20:00:00"
        )
        events = cal.events("monitor", made["id"])
        assert [e for e, _ in events] == ["external_calendar.created"]
        assert events[0][1]["calendar_id"] == made["id"]
        assert events[0][1]["forecast_model_ids"] == made["forecast_model_ids"]

        twice = await api.post(
            "/v1/external-calendars", json=_body(), headers=cal.headers("steward")
        )
        assert twice.status_code == 409
        elsewhere = await api.post(
            "/v1/external-calendars", json=_body(), headers=cal.headers("steward", cal.other)
        )
        assert elsewhere.status_code == 201, elsewhere.text
        # BR-187: periods come from a hint or a detection source, never from nowhere.
        nothing = await api.post(
            "/v1/external-calendars",
            json=_body(code="khong_nguon", recurrence_hint=None),
            headers=cal.headers("steward"),
        )
        assert nothing.status_code == 422 and nothing.json()["code"] == "validation_failed"
        detected = await api.post(
            "/v1/external-calendars",
            json=_body(
                code="phat_hien", recurrence_hint=None, detection_source_id=str(uuid.uuid4())
            ),
            headers=cal.headers("steward"),
        )
        assert detected.status_code == 201, detected.text
        for extra in (
            {"timezone": "Mars/Olympus"},
            {"code": "Có Dấu"},
            {"pre_bulletin_days_before": 8},
            {"work_calendar_id": str(uuid.uuid4())},
        ):
            refused = await api.post(
                "/v1/external-calendars",
                json=_body(**{"code": "khac", **extra}),
                headers=cal.headers("steward"),
            )
            assert refused.status_code == 422, (extra, refused.text)
        asker = await api.post(
            "/v1/external-calendars", json=_body(code="cua_toi"), headers=cal.headers("owner")
        )
        assert asker.status_code == 403


async def test_calendars_list_the_next_period_and_followers(cal: Org) -> None:
    async with client(cal) as api:
        made = await _create(api, cal)
        paused = await _create(api, cal, code="tam_dung")
        await api.patch(
            f"/v1/external-calendars/{paused['id']}",
            json={"status": "paused"},
            headers=cal.headers("steward"),
        )
        seed_event(
            cal,
            made["id"],
            "2026-01-08T15:00:00+07:00",
            status="occurred",
            occurred_at="2026-01-08T15:30:00+07:00",
        )
        nxt = seed_event(cal, made["id"], "2099-07-09T15:00:00+07:00")
        seed_event(cal, made["id"], "2099-07-16T15:00:00+07:00")
        seed_calendar(cal, cal.other)
        for who in ("owner", "colleague"):
            followed = await api.put(
                f"/v1/external-calendars/{made['id']}/subscription",
                json={"kinds": ["pre"], "channels": ["in_app"]},
                headers=cal.headers(who),
            )
            assert followed.status_code == 200, followed.text

        listed = await api.get("/v1/external-calendars", headers=cal.headers("colleague"))
        assert listed.status_code == 200, listed.text
        items = {c["id"]: c for c in listed.json()["items"]}
        assert set(items) == {made["id"], paused["id"]}
        assert items[made["id"]]["subscriber_count"] == 2
        assert items[made["id"]]["next_event"]["id"] == str(nxt)
        assert items[paused["id"]]["next_event"] is None
        active = await api.get(
            "/v1/external-calendars", params={"status": "active"}, headers=cal.headers("colleague")
        )
        assert [c["id"] for c in active.json()["items"]] == [made["id"]]
        no_cap = await api.get("/v1/external-calendars", headers=cal.headers("nocap"))
        assert no_cap.status_code == 403


async def test_a_new_bulletin_time_retimes_the_periods_to_come(cal: Org) -> None:
    async with client(cal) as api:
        made = await _create(api, cal)
        url = f"/v1/external-calendars/{made['id']}"
        past = seed_event(
            cal,
            made["id"],
            "2026-01-08T15:00:00+07:00",
            status="occurred",
            occurred_at="2026-01-08T15:30:00+07:00",
        )
        coming = seed_event(cal, made["id"], "2099-07-09T15:00:00+07:00")
        before_past = cal.one(
            "SELECT pre_bulletin_at FROM monitor.external_event WHERE id = %s", past
        )
        assert cal.one(
            "SELECT pre_bulletin_at FROM monitor.external_event WHERE id = %s", coming
        ) == datetime.datetime(2099, 7, 8, 20, 0, tzinfo=ICT)

        asker = await api.patch(url, json={"name": "x"}, headers=cal.headers("owner"))
        assert asker.status_code == 403
        null = await api.patch(url, json={"name": None}, headers=cal.headers("steward"))
        assert null.status_code == 422
        # Active needs a way to know its periods: the table's CHECK (BR-187).
        no_source = await api.patch(
            url, json={"recurrence_hint": None}, headers=cal.headers("steward")
        )
        assert no_source.status_code == 422
        assert no_source.json()["code"] == "business_rule_violated"
        elsewhere = await api.patch(
            url, json={"name": "x"}, headers=cal.headers("steward", cal.other)
        )
        assert elsewhere.status_code == 404

        changed = await api.patch(
            url,
            json={"pre_bulletin_local_time": "19:00:00", "post_bulletin": False},
            headers=cal.headers("steward"),
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["pre_bulletin_local_time"] == "19:00:00"
        assert changed.json()["post_bulletin"] is False
    assert cal.one(
        "SELECT pre_bulletin_at FROM monitor.external_event WHERE id = %s", coming
    ) == datetime.datetime(2099, 7, 8, 19, 0, tzinfo=ICT)
    assert (
        cal.one("SELECT pre_bulletin_at FROM monitor.external_event WHERE id = %s", past)
        == before_past
    )
    assert [e for e, _ in cal.events("monitor", coming)] == ["monitor.external_event.changed"]
    assert cal.events("monitor", past) == []
    updated = [p for e, p in cal.events("monitor", made["id"]) if e == "external_calendar.updated"]
    assert updated[-1]["changed"] == ["post_bulletin", "pre_bulletin_local_time"]


async def test_periods_are_listed_with_both_bulletins(cal: Org) -> None:
    async with client(cal) as api:
        made = await _create(api, cal)
        first = seed_event(cal, made["id"], "2099-07-09T15:00:00+07:00")
        second = seed_event(
            cal,
            made["id"],
            "2099-07-16T15:00:00+07:00",
            status="confirmed",
            confirmed_by=cal.users["steward"],
        )
        pre = cal.seed.row("monitor.event_bulletin", org_id=cal.org, event_id=first, kind="pre")
        url = f"/v1/external-calendars/{made['id']}/events"
        listed = await api.get(url, headers=cal.headers("colleague"))
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert [e["id"] for e in items] == [str(first), str(second)]
        assert items[0]["pre_bulletin"]["id"] == str(pre) and items[0]["post_bulletin"] is None
        assert items[1]["pre_bulletin"] is None
        paged = await api.get(url, params={"page_size": 1}, headers=cal.headers("colleague"))
        rest = await api.get(
            url,
            params={"page_size": 1, "page_token": paged.json()["next_page_token"]},
            headers=cal.headers("colleague"),
        )
        assert [e["id"] for e in rest.json()["items"]] == [str(second)]
        confirmed = await api.get(
            url, params={"status": "confirmed"}, headers=cal.headers("colleague")
        )
        assert [e["id"] for e in confirmed.json()["items"]] == [str(second)]
        window = await api.get(
            url,
            params={"from": "2099-07-10T00:00:00Z", "to": "2099-08-01T00:00:00Z"},
            headers=cal.headers("colleague"),
        )
        assert [e["id"] for e in window.json()["items"]] == [str(second)]
        elsewhere = await api.get(url, headers=cal.headers("colleague", cal.other))
        assert elsewhere.status_code == 404
        hidden = seed_calendar(cal, cal.other)
        other = await api.get(
            f"/v1/external-calendars/{hidden}/events", headers=cal.headers("owner")
        )
        assert other.status_code == 404


# AC-08-08-03
async def test_a_shifted_period_keeps_its_old_time_and_gets_a_new_bulletin_time(cal: Org) -> None:
    async with client(cal) as api:
        made = await _create(api, cal)
        event = seed_event(cal, made["id"], "2099-07-09T15:00:00+07:00")
        url = f"/v1/external-events/{event}:confirm"
        for body in (
            {"action": "shift"},
            {"action": "shift", "expected_at": "2099-07-09T15:00:00+07:00"},
            {"action": "shift", "expected_at": "2099-07-10T15:00:00"},
            {"action": "postpone"},
        ):
            refused = await api.post(url, json=body, headers=cal.headers("steward"))
            assert refused.status_code == 422, (body, refused.text)
        asker = await api.post(url, json={"action": "confirm"}, headers=cal.headers("owner"))
        assert asker.status_code == 403
        shifted = await api.post(
            url,
            json={
                "action": "shift",
                "expected_at": "2099-07-10T15:00:00+07:00",
                "note": "Bộ thông báo dời kỳ",
            },
            headers=cal.headers("steward"),
        )
        assert shifted.status_code == 200, shifted.text
        body = shifted.json()
        assert body["status"] == "shifted"
        assert _at(body["shifted_from"]) == datetime.datetime(2099, 7, 9, 15, 0, tzinfo=ICT)
        assert _at(body["pre_bulletin_at"]) == datetime.datetime(2099, 7, 9, 20, 0, tzinfo=ICT)
        assert body["confirmed_by"] == str(cal.users["steward"])
        assert body["evidence"]["note"] == "Bộ thông báo dời kỳ"
        changed = cal.events("monitor", event)
        assert [e for e, _ in changed] == ["monitor.external_event.changed"]
        assert (
            changed[0][1]["status"] == "shifted" and changed[0][1]["previous_status"] == "expected"
        )

        confirmed = await api.post(url, json={"action": "confirm"}, headers=cal.headers("steward"))
        assert confirmed.status_code == 200 and confirmed.json()["status"] == "confirmed"
        cancelled = await api.post(url, json={"action": "cancel"}, headers=cal.headers("steward"))
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        final = await api.post(url, json={"action": "confirm"}, headers=cal.headers("steward"))
        assert final.status_code == 409 and final.json()["code"] == "invalid_state"


async def test_an_occurred_period_is_final(cal: Org) -> None:
    async with client(cal) as api:
        made = await _create(api, cal)
        done = seed_event(
            cal,
            made["id"],
            "2026-01-08T15:00:00+07:00",
            status="occurred",
            occurred_at="2026-01-08T15:30:00+07:00",
        )
        for action in ("confirm", "cancel"):
            refused = await api.post(
                f"/v1/external-events/{done}:confirm",
                json={"action": action},
                headers=cal.headers("steward"),
            )
            assert refused.status_code == 409
        late = await api.post(
            f"/v1/external-events/{done}:run-bulletin", json={}, headers=cal.headers("steward")
        )
        assert late.status_code == 409 and late.json()["code"] == "invalid_state"
        hidden = seed_event(cal, seed_calendar(cal, cal.other), "2099-07-09T15:00:00+07:00")
        for verb, body in (("confirm", {"action": "confirm"}), ("run-bulletin", {})):
            other = await api.post(
                f"/v1/external-events/{hidden}:{verb}", json=body, headers=cal.headers("steward")
            )
            assert other.status_code == 404
    assert cal.events("monitor", done) == []


async def test_follow_a_calendar_with_in_app_always_on(cal: Org) -> None:
    async with client(cal) as api:
        made = await _create(api, cal)
        url = f"/v1/external-calendars/{made['id']}/subscription"
        no_app = await api.put(
            url, json={"kinds": ["pre"], "channels": ["email"]}, headers=cal.headers("owner")
        )
        assert no_app.status_code == 422 and no_app.json()["code"] == "validation_failed"
        no_kind = await api.put(
            url, json={"kinds": [], "channels": ["in_app"]}, headers=cal.headers("owner")
        )
        assert no_kind.status_code == 422
        followed = await api.put(
            url,
            json={"kinds": ["pre", "post"], "channels": ["in_app", "zalo", "in_app"]},
            headers=cal.headers("owner"),
        )
        assert followed.status_code == 200, followed.text
        assert followed.json()["channels"] == ["in_app", "zalo"]
        assert followed.json()["user_id"] == str(cal.users["owner"])
        stopped = await api.put(
            url,
            json={"kinds": ["pre"], "channels": ["in_app"], "status": "paused"},
            headers=cal.headers("owner"),
        )
        assert (
            stopped.json()["id"] == followed.json()["id"] and stopped.json()["status"] == "paused"
        )
        no_cap = await api.put(
            url, json={"kinds": ["pre"], "channels": ["in_app"]}, headers=cal.headers("nocap")
        )
        assert no_cap.status_code == 403
        elsewhere = await api.put(
            url,
            json={"kinds": ["pre"], "channels": ["in_app"]},
            headers=cal.headers("owner", cal.other),
        )
        assert elsewhere.status_code == 404
        await api.patch(
            f"/v1/external-calendars/{made['id']}",
            json={"status": "retired"},
            headers=cal.headers("steward"),
        )
        retired = await api.put(
            url, json={"kinds": ["pre"], "channels": ["in_app"]}, headers=cal.headers("colleague")
        )
        assert retired.status_code == 409
    events = cal.events("monitor", followed.json()["id"])
    assert [e for e, _ in events] == ["event_subscription.created", "event_subscription.updated"]
    assert events[0][1]["calendar_id"] == made["id"] and events[1][1]["status"] == "paused"


# AC-08-08-04
async def test_run_the_bulletin_now(cal: Org) -> None:
    async with client(cal) as api:
        made = await _create(api, cal)
        event = seed_event(cal, made["id"], "2099-07-09T15:00:00+07:00")
        url = f"/v1/external-events/{event}:run-bulletin"
        asker = await api.post(url, json={}, headers=cal.headers("owner"))
        assert asker.status_code == 403
        started = await api.post(
            url, json={"reason": "Giá dầu thế giới tăng mạnh"}, headers=cal.headers("steward")
        )
        assert started.status_code == 202, started.text
        bulletin = started.json()["id"]
        assert started.headers["Location"] == f"/v1/event-bulletins/{bulletin}"
        assert started.json()["status"] == "preparing"
        again = await api.post(url, json={}, headers=cal.headers("steward"))
        assert again.status_code == 409
        # The forecast agent finished it; a second manual run re-issues the same pre bulletin.
        ledger = uuid.uuid4()
        cal.run(
            "UPDATE monitor.event_bulletin SET status = 'sent', sent_at = now(), ledger_entry_ids = %s::uuid[]"
            " WHERE id = %s",
            [str(ledger)],
            bulletin,
        )
        read = await api.get(f"/v1/event-bulletins/{bulletin}", headers=cal.headers("colleague"))
        assert read.status_code == 200, read.text
        assert read.json()["status"] == "sent" and read.json()["kind"] == "pre"
        assert read.json()["ledger_entry_links"] == [f"/v1/forecast-ledger/{ledger}"]
        assert read.json()["content"]["manual_run"]["reason"] == "Giá dầu thế giới tăng mạnh"
        reissued = await api.post(url, headers=cal.headers("steward"))
        assert reissued.status_code == 202, reissued.text
        assert reissued.json()["id"] == bulletin
        hidden = await api.get(
            f"/v1/event-bulletins/{bulletin}", headers=cal.headers("owner", cal.other)
        )
        assert hidden.status_code == 404
        no_cap = await api.get(f"/v1/event-bulletins/{bulletin}", headers=cal.headers("nocap"))
        assert no_cap.status_code == 403
        missing = await api.get(f"/v1/event-bulletins/{uuid.uuid4()}", headers=cal.headers("owner"))
        assert missing.status_code == 404
    assert (
        cal.one("SELECT status FROM monitor.event_bulletin WHERE id = %s", bulletin) == "preparing"
    )
    requested = [p for e, p in cal.events("monitor", bulletin) if e == "event_bulletin.requested"]
    assert len(requested) == 2
    assert requested[0]["event_id"] == str(event) and requested[0]["trigger"] == "manual"
    assert requested[0]["requested_by"] == str(cal.users["steward"])
