"""Theo dõi chỉ số and cảnh báo (UC-08-01, UC-08-02, BR-32, BR-124) against PostgreSQL.

Each test builds its own organisation: a monitor owner and a colleague who only ask, a steward
(external calendars), a runner who may hand tasks to agents (UC-08-05), a user with no capability
of this group, and a user who is a member of the second Data Space only. One published metric
lives in the first Data Space, a draft one beside it and a published one in the second.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from psycopg2.extras import Json

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]


@dataclass
class Org:
    org: uuid.UUID
    space: uuid.UUID
    other: uuid.UUID
    users: dict[str, uuid.UUID]
    roles: dict[str, uuid.UUID]
    metric: uuid.UUID
    draft_metric: uuid.UUID
    other_metric: uuid.UUID
    seed: Seed
    # Tokens of users of other organisations: token → org id.
    foreign: dict[str, uuid.UUID] = field(default_factory=dict)

    def headers(self, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer kc-{who}", "X-Data-Space": str(space or self.space)}

    def events(self, schema: str, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                f"SELECT event_type, payload FROM {schema}.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
        # Whether psycopg2 hands back UUID or str depends on which suite registered its uuid
        # adapter first; ids are compared as text.
        value = row[0] if row else None
        return str(value) if isinstance(value, uuid.UUID) else value

    def run(self, sql: str, *params: object) -> None:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])


def member(seed: Seed, org: uuid.UUID, space: uuid.UUID, user: uuid.UUID, role: uuid.UUID) -> None:
    seed.row(
        "org.data_space_member",
        org_id=org,
        data_space_id=space,
        principal_type="user",
        principal_id=user,
        role_id=role,
    )


def user(
    seed: Seed, org: uuid.UUID, name: str, role: uuid.UUID, status: str = "active"
) -> uuid.UUID:
    made = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-{name}",
        username=name,
        display_name=name,
        status=status,
    )
    seed.row("org.user_role", org_id=org, user_id=made, role_id=role, source="manual")
    return made


def metric(
    seed: Seed, org: uuid.UUID, space: uuid.UUID, code: str, status: str, owner: uuid.UUID
) -> uuid.UUID:
    return seed.row(
        "context.metric",
        org_id=org,
        data_space_id=space,
        code=code,
        owner_id=owner,
        current_version=1,
        status=status,
    )


def build(seed: Seed) -> Org:
    org = seed.org()
    roles = {
        "ask": seed.row("org.role", org_id=org, code="ASK", name="Ask", capabilities=["ask"]),
        "steward": seed.row(
            "org.role", org_id=org, code="STEWARD", name="Steward", capabilities=["ask", "steward"]
        ),
        "runner": seed.row(
            "org.role", org_id=org, code="RUN", name="Run", capabilities=["ask", "run_agent"]
        ),
        "none": seed.row(
            "org.role", org_id=org, code="VIEW", name="View", capabilities=["view_detail"]
        ),
    }
    users = {
        "owner": user(seed, org, "owner", roles["ask"]),
        "colleague": user(seed, org, "colleague", roles["ask"]),
        "steward": user(seed, org, "steward", roles["steward"]),
        "runner": user(seed, org, "runner", roles["runner"]),
        "nocap": user(seed, org, "nocap", roles["none"]),
        "outsider": user(seed, org, "outsider", roles["ask"]),
        "gone": user(seed, org, "gone", roles["ask"], status="disabled"),
    }
    space = seed.row(
        "org.data_space", org_id=org, code="DS-MON", name="Mon", steward_id=users["steward"]
    )
    other = seed.row(
        "org.data_space", org_id=org, code="DS-MON-B", name="Mon B", steward_id=users["steward"]
    )
    # A membership's role adds its capabilities in the space: each keeps their own role there.
    space_roles = {"steward": "steward", "runner": "runner", "nocap": "none"}
    for name in ("owner", "colleague", "steward", "runner", "nocap", "gone"):
        member(seed, org, space, users[name], roles[space_roles.get(name, "ask")])
    # The owner and the steward are in both spaces: a row of the other space is still not theirs
    # to reach with this space's header, and vice versa.
    for name in ("owner", "steward", "runner", "outsider"):
        member(seed, org, other, users[name], roles[space_roles.get(name, "ask")])
    return Org(
        org,
        space,
        other,
        users,
        roles,
        metric(seed, org, space, "npl_ratio", "active", users["owner"]),
        metric(seed, org, space, "npl_ratio_draft", "draft", users["owner"]),
        metric(seed, org, other, "npl_ratio_b", "active", users["owner"]),
        seed,
    )


@pytest.fixture
def mon(seed: Seed) -> Org:
    return build(seed)


class Tokens:
    def __init__(self, mon: Org) -> None:
        self.mon = mon

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.mon.foreign.get(token, self.mon.org))}


def client(mon: Org, overrides: dict[Any, Any] | None = None) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(mon))
    app.dependency_overrides.update(overrides or {})
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


def _monitor_body(mon: Org, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "metric_id": str(mon.metric),
        "scope_filter": {"vung": ["mien_bac"]},
        "rule_kind": "threshold",
        "rule": {"op": ">", "value": 0.02},
        "schedule_cron": "0 8 * * *",
        "recipients": [{"user_id": str(mon.users["owner"]), "channels": ["in_app", "teams"]}],
    }
    body.update(extra)
    return body


async def _create_monitor(api: httpx.AsyncClient, mon: Org, **extra: Any) -> dict[str, Any]:
    made = await api.post(
        "/v1/monitors", json=_monitor_body(mon, **extra), headers=mon.headers("owner")
    )
    assert made.status_code == 201, made.text
    out: dict[str, Any] = made.json()
    return out


def seed_monitor(
    mon: Org, space: uuid.UUID | None = None, metric_id: uuid.UUID | None = None
) -> uuid.UUID:
    return mon.seed.row(
        "monitor.monitor",
        org_id=mon.org,
        data_space_id=space or mon.space,
        owner_id=mon.users["owner"],
        metric_id=metric_id or mon.metric,
        rule_kind="threshold",
        rule=Json({"op": ">", "value": 0.02}),
        schedule_cron="0 8 * * *",
    )


def seed_alert(mon: Org, monitor_id: uuid.UUID, **values: Any) -> uuid.UUID:
    values.setdefault("summary", "Nợ xấu miền Bắc 2,3%")
    values.setdefault("dedup_key", f"npl:{uuid.uuid4().hex[:8]}")
    return mon.seed.row("monitor.alert", org_id=mon.org, monitor_id=monitor_id, **values)


# ---------------------------------------------------------------- monitors (UC-08-01)


# AC-08-01-01
async def test_owner_sets_a_monitor_and_the_agent_is_asked_to_backtest_it(mon: Org) -> None:
    async with client(mon) as api:
        made = await _create_monitor(api, mon)
    assert made["status"] == "active" and made["owner_id"] == str(mon.users["owner"])
    assert made["data_space_id"] == str(mon.space) and made["notify_mode"] == "alert_only"
    # UC-08-01 step 3: the backtest on the last 12 periods is pending; no noise verdict yet.
    assert made["backtest"]["status"] == "pending" and made["backtest"]["periods"] == 12
    assert made["noise_warning"] is None
    assert mon.one("SELECT rule_kind FROM monitor.monitor WHERE id = %s", made["id"]) == "threshold"
    events = mon.events("monitor", made["id"])
    assert [e for e, _ in events] == ["monitor.created"]
    payload = events[0][1]
    assert payload["monitor_id"] == made["id"] and payload["metric_id"] == str(mon.metric)
    assert payload["data_space_id"] == str(mon.space) and payload["backtest_periods"] == 12


async def test_a_monitor_watches_a_published_metric_of_its_data_space(mon: Org) -> None:
    async with client(mon) as api:
        for metric_id in (mon.draft_metric, mon.other_metric, uuid.uuid4()):
            refused = await api.post(
                "/v1/monitors",
                json=_monitor_body(mon, metric_id=str(metric_id)),
                headers=mon.headers("owner"),
            )
            assert refused.status_code == 422, refused.text
            assert refused.json()["errors"][0]["field"] == "metric_id"
        bad_cron = await api.post(
            "/v1/monitors",
            json=_monitor_body(mon, schedule_cron="every day"),
            headers=mon.headers("owner"),
        )
        assert bad_cron.status_code == 422
        bad_kind = await api.post(
            "/v1/monitors", json=_monitor_body(mon, rule_kind="guess"), headers=mon.headers("owner")
        )
        assert bad_kind.status_code == 422
        no_space = await api.post(
            "/v1/monitors", json=_monitor_body(mon), headers={"Authorization": "Bearer kc-owner"}
        )
        assert no_space.status_code == 400
        assert no_space.json()["code"] == "data_space_required"
    assert mon.one("SELECT count(*) FROM monitor.monitor WHERE org_id = %s", mon.org) == 0


async def test_monitors_need_ask_and_the_data_space(mon: Org) -> None:
    async with client(mon) as api:
        no_cap = await api.post(
            "/v1/monitors", json=_monitor_body(mon), headers=mon.headers("nocap")
        )
        assert no_cap.status_code == 403, no_cap.text
        # Not a member of the first Data Space: it does not exist for them.
        stranger = await api.post(
            "/v1/monitors", json=_monitor_body(mon), headers=mon.headers("outsider")
        )
        assert stranger.status_code == 404, stranger.text
        made = await _create_monitor(api, mon)
        for call in (
            api.patch(
                f"/v1/monitors/{made['id']}",
                json={"notify_mode": "with_analysis"},
                headers=mon.headers("nocap"),
            ),
            api.post(f"/v1/monitors/{made['id']}:backtest", json={}, headers=mon.headers("nocap")),
        ):
            assert (await call).status_code == 403
        for call in (
            api.patch(
                f"/v1/monitors/{made['id']}",
                json={"notify_mode": "with_analysis"},
                headers=mon.headers("owner", mon.other),
            ),
            api.post(
                f"/v1/monitors/{made['id']}:backtest",
                json={},
                headers=mon.headers("owner", mon.other),
            ),
            api.patch(
                f"/v1/monitors/{uuid.uuid4()}",
                json={"status": "paused"},
                headers=mon.headers("owner"),
            ),
        ):
            assert (await call).status_code == 404


# AC-08-01-04
async def test_noise_warning_above_thirty_percent_of_the_backtest(mon: Org) -> None:
    async with client(mon) as api:
        made = await _create_monitor(api, mon)
        for fired, warned in ((4, True), (3, False)):
            # What the monitor agent writes when the backtest has run.
            mon.run(
                "UPDATE monitor.monitor SET backtest = %s WHERE id = %s",
                Json({"periods": 12, "fired": fired, "fired_periods": ["2026-03"]}),
                made["id"],
            )
            read = await api.patch(
                f"/v1/monitors/{made['id']}",
                json={"notify_mode": "with_analysis"},
                headers=mon.headers("owner"),
            )
            assert read.status_code == 200, read.text
            assert read.json()["noise_warning"] is warned
            assert read.json()["backtest"]["fired"] == fired


async def test_only_the_owner_changes_a_monitor(mon: Org) -> None:
    async with client(mon) as api:
        made = await _create_monitor(api, mon)
        url = f"/v1/monitors/{made['id']}"
        mon.run(
            "UPDATE monitor.monitor SET backtest = %s WHERE id = %s",
            Json({"periods": 12, "fired": 1}),
            made["id"],
        )
        theirs = await api.patch(url, json={"status": "paused"}, headers=mon.headers("colleague"))
        assert theirs.status_code == 403
        null = await api.patch(url, json={"rule": None}, headers=mon.headers("owner"))
        assert null.status_code == 422
        assert null.json()["errors"][0]["field"] == "rule"
        bad = await api.patch(url, json={"status": "deleted"}, headers=mon.headers("owner"))
        assert bad.status_code == 422

        paused = await api.patch(url, json={"status": "paused"}, headers=mon.headers("owner"))
        assert paused.status_code == 200 and paused.json()["status"] == "paused"
        # The old backtest stays while the rule does not change.
        assert paused.json()["backtest"]["fired"] == 1
        mon.run(
            "UPDATE monitor.monitor SET paused_reason = 'metric_version' WHERE id = %s", made["id"]
        )
        resumed = await api.patch(
            url,
            json={"status": "active", "rule": {"op": ">", "value": 0.025}},
            headers=mon.headers("owner"),
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["paused_reason"] is None
        # A changed rule makes the old backtest meaningless: a new one is pending.
        assert resumed.json()["backtest"]["status"] == "pending"
        assert resumed.json()["noise_warning"] is None
    updates = [p for e, p in mon.events("monitor", made["id"]) if e == "monitor.updated"]
    assert [(u["previous_status"], u["status"]) for u in updates] == [
        ("active", "paused"),
        ("paused", "active"),
    ]
    assert updates[1]["changed"] == ["rule", "status"]


async def test_backtest_on_demand(mon: Org) -> None:
    async with client(mon) as api:
        made = await _create_monitor(api, mon)
        url = f"/v1/monitors/{made['id']}:backtest"
        for periods in (0, 37):
            refused = await api.post(url, json={"periods": periods}, headers=mon.headers("owner"))
            assert refused.status_code == 422
        default = await api.post(url, headers=mon.headers("owner"))
        assert default.status_code == 200, default.text
        assert default.json()["backtest"]["periods"] == 12
        asked = await api.post(url, json={"periods": 24}, headers=mon.headers("owner"))
        assert asked.status_code == 200, asked.text
        assert asked.json()["backtest"] == {
            **asked.json()["backtest"],
            "status": "pending",
            "periods": 24,
        }
    requested = [
        p for e, p in mon.events("monitor", made["id"]) if e == "monitor.backtest_requested"
    ]
    assert [r["periods"] for r in requested] == [12, 24]
    assert requested[1]["monitor_id"] == made["id"]


# ---------------------------------------------------------------- alerts (UC-08-02)


async def test_alerts_list_stays_in_the_data_space(mon: Org) -> None:
    mine = seed_monitor(mon)
    second = seed_monitor(mon)
    theirs = seed_monitor(mon, mon.other, mon.other_metric)
    old = seed_alert(
        mon,
        mine,
        fired_at="2026-07-01T08:00:00Z",
        status="closed",
        closed_at="2026-07-02T00:00:00Z",
    )
    new = seed_alert(mon, mine, fired_at="2026-07-03T08:00:00Z")
    other_monitor = seed_alert(mon, second, fired_at="2026-07-02T08:00:00Z", status="ack")
    hidden = seed_alert(mon, theirs, fired_at="2026-07-04T08:00:00Z")
    async with client(mon) as api:
        listed = await api.get("/v1/alerts", headers=mon.headers("colleague"))
        assert listed.status_code == 200, listed.text
        assert [a["id"] for a in listed.json()["items"]] == [str(new), str(other_monitor), str(old)]
        assert str(hidden) not in {a["id"] for a in listed.json()["items"]}

        first = await api.get(
            "/v1/alerts", params={"page_size": 2}, headers=mon.headers("colleague")
        )
        token = first.json()["next_page_token"]
        assert token and len(first.json()["items"]) == 2
        rest = await api.get(
            "/v1/alerts",
            params={"page_size": 2, "page_token": token},
            headers=mon.headers("colleague"),
        )
        assert [a["id"] for a in rest.json()["items"]] == [str(old)]
        assert rest.json()["next_page_token"] == ""

        by_status = await api.get(
            "/v1/alerts", params={"status": "open"}, headers=mon.headers("colleague")
        )
        assert [a["id"] for a in by_status.json()["items"]] == [str(new)]
        by_monitor = await api.get(
            "/v1/alerts", params={"monitor_id": str(second)}, headers=mon.headers("colleague")
        )
        assert [a["id"] for a in by_monitor.json()["items"]] == [str(other_monitor)]
        window = await api.get(
            "/v1/alerts",
            params={"from": "2026-07-02T00:00:00Z", "to": "2026-07-03T00:00:00Z"},
            headers=mon.headers("colleague"),
        )
        assert [a["id"] for a in window.json()["items"]] == [str(other_monitor)]

        there = await api.get("/v1/alerts", headers=mon.headers("owner", mon.other))
        assert [a["id"] for a in there.json()["items"]] == [str(hidden)]
        no_cap = await api.get("/v1/alerts", headers=mon.headers("nocap"))
        assert no_cap.status_code == 403
        bad_token = await api.get(
            "/v1/alerts", params={"page_token": "bm9wZQ"}, headers=mon.headers("colleague")
        )
        assert bad_token.status_code == 400


# AC-08-02-02
async def test_alert_is_acknowledged_assigned_and_closed_with_an_audit_each(mon: Org) -> None:
    alert = seed_alert(mon, seed_monitor(mon), value=0.023, threshold_value=0.02)
    url = f"/v1/alerts/{alert}"
    async with client(mon) as api:
        acked = await api.post(f"{url}:ack", headers=mon.headers("owner"))
        assert acked.status_code == 200, acked.text
        assert acked.json()["status"] == "ack"
        again = await api.post(f"{url}:ack", headers=mon.headers("owner"))
        assert again.status_code == 409 and again.json()["code"] == "invalid_state"

        inactive = await api.post(
            f"{url}:assign",
            json={"assignee_id": str(mon.users["gone"])},
            headers=mon.headers("owner"),
        )
        assert inactive.status_code == 422
        unknown = await api.post(
            f"{url}:assign", json={"assignee_id": str(uuid.uuid4())}, headers=mon.headers("owner")
        )
        assert unknown.status_code == 422
        # The outsider cannot open this Data Space, so cannot work on its alert.
        outside = await api.post(
            f"{url}:assign",
            json={"assignee_id": str(mon.users["outsider"])},
            headers=mon.headers("owner"),
        )
        assert outside.status_code == 422, outside.text
        assert outside.json()["errors"][0]["field"] == "assignee_id"
        assigned = await api.post(
            f"{url}:assign",
            json={"assignee_id": str(mon.users["colleague"])},
            headers=mon.headers("owner"),
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["status"] == "assigned"
        assert assigned.json()["assigned_to"] == str(mon.users["colleague"])

        closed = await api.post(
            f"{url}:close",
            json={"close_note": "Đã kiểm tra, do khoản vay lớn"},
            headers=mon.headers("colleague"),
        )
        assert closed.status_code == 200, closed.text
        body = closed.json()
        assert body["status"] == "closed" and body["closed_at"] is not None
        assert body["close_note"] == "Đã kiểm tra, do khoản vay lớn"
        for verb, payload in (
            ("ack", None),
            ("assign", {"assignee_id": str(mon.users["owner"])}),
            ("close", {}),
        ):
            late = await api.post(f"{url}:{verb}", json=payload, headers=mon.headers("owner"))
            assert late.status_code == 409, (verb, late.text)
    assert mon.one("SELECT status FROM monitor.alert WHERE id = %s", alert) == "closed"
    # Audit takes each step from these events (BR-124).
    events = mon.events("monitor", alert)
    assert [e for e, _ in events] == ["alert.acknowledged", "alert.assigned", "alert.closed"]
    assert [(p["previous_status"], p["status"]) for _, p in events] == [
        ("open", "ack"),
        ("ack", "assigned"),
        ("assigned", "closed"),
    ]
    assert events[0][1]["actor_id"] == str(mon.users["owner"])
    assert events[2][1]["actor_id"] == str(mon.users["colleague"])
    assert events[1][1]["assigned_to"] == str(mon.users["colleague"])


# AC-08-02-05
async def test_closing_far_above_the_threshold_needs_a_note(mon: Org) -> None:
    monitor_id = seed_monitor(mon)
    far = seed_alert(mon, monitor_id, value=4.5, threshold_value=2)
    near = seed_alert(mon, monitor_id, value=3.5, threshold_value=2)
    async with client(mon) as api:
        refused = await api.post(f"/v1/alerts/{far}:close", json={}, headers=mon.headers("owner"))
        assert refused.status_code == 422
        assert refused.json()["code"] == "business_rule_violated"
        blank = await api.post(
            f"/v1/alerts/{far}:close", json={"close_note": "   "}, headers=mon.headers("owner")
        )
        assert blank.status_code == 422
        noted = await api.post(
            f"/v1/alerts/{far}:close",
            json={"close_note": "Số liệu chi nhánh HN01 sai"},
            headers=mon.headers("owner"),
        )
        assert noted.status_code == 200, noted.text
        quiet = await api.post(f"/v1/alerts/{near}:close", headers=mon.headers("owner"))
        assert quiet.status_code == 200, quiet.text
        assert quiet.json()["close_note"] is None
    assert [e for e, _ in mon.events("monitor", far)] == ["alert.closed"]


async def test_an_alert_repeated_more_than_twice_needs_a_note(mon: Org) -> None:
    repeated = seed_alert(mon, seed_monitor(mon), occurrences=3)
    async with client(mon) as api:
        refused = await api.post(
            f"/v1/alerts/{repeated}:close", json={}, headers=mon.headers("owner")
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "business_rule_violated"
        assert mon.one("SELECT status FROM monitor.alert WHERE id = %s", repeated) == "open"
        assert mon.events("monitor", repeated) == []
        noted = await api.post(
            f"/v1/alerts/{repeated}:close",
            json={"close_note": "Đã xử lý"},
            headers=mon.headers("owner"),
        )
        assert noted.status_code == 200, noted.text


async def test_an_alert_of_another_data_space_is_not_found(mon: Org) -> None:
    hidden = seed_alert(mon, seed_monitor(mon, mon.other, mon.other_metric))
    async with client(mon) as api:
        for verb, body in (
            ("ack", None),
            ("assign", {"assignee_id": str(mon.users["colleague"])}),
            ("close", {"close_note": "x"}),
        ):
            missing = await api.post(
                f"/v1/alerts/{hidden}:{verb}", json=body, headers=mon.headers("owner")
            )
            assert missing.status_code == 404, (verb, missing.text)
            no_cap = await api.post(
                f"/v1/alerts/{hidden}:{verb}", json=body, headers=mon.headers("nocap")
            )
            assert no_cap.status_code == 403
    assert mon.one("SELECT status FROM monitor.alert WHERE id = %s", hidden) == "open"
