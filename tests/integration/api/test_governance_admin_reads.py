"""Governance reads of Quản trị added on 12/10/2026, against PostgreSQL: the AI settings in force
(UC-11-05), agent usage per day and per agent (UC-11-04, UC-13-06), the schedule preview shared
by every "Lịch chạy" dialog (UC-06-03) and the list of data subject requests (UC-11-06)."""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx
import pytest
from psycopg2.extras import Json

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]

NOW = datetime.datetime.now(datetime.UTC)
TODAY = NOW.date()


@dataclass
class Org:
    org: uuid.UUID
    space: uuid.UUID
    users: dict[str, uuid.UUID]
    suffix: str
    seed: Seed

    def headers(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer kc-{who}-{self.suffix}"}

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
            return row[0] if row else None


def _org(seed: Seed) -> Org:
    suffix = uuid.uuid4().hex[:8]
    org = seed.org()
    roles = {
        "admin": ["ask", "admin"],
        "officer": ["ask", "compliance"],
        "analyst": ["ask"],
    }
    users: dict[str, uuid.UUID] = {}
    for name, caps in roles.items():
        role = seed.row("org.role", org_id=org, code=name.upper(), name=name, capabilities=caps)
        users[name] = seed.row(
            "org.app_user",
            org_id=org,
            external_id=f"kc-{name}-{suffix}",
            username=f"{name}-{suffix}",
            display_name=name,
            status="active",
        )
        seed.row("org.user_role", org_id=org, user_id=users[name], role_id=role, source="manual")
    space = seed.row(
        "org.data_space", org_id=org, code=f"DS-{suffix}", name="Gov", steward_id=users["admin"]
    )
    return Org(org=org, space=space, users=users, suffix=suffix, seed=seed)


@pytest.fixture
def gov(seed: Seed) -> Org:
    return _org(seed)


class Tokens:
    def __init__(self, gov: Org) -> None:
        self.gov = gov

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.gov.org)}


def client(gov: Org) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(gov))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


# ---------------------------------------------------------------- AI settings


async def test_ai_settings_in_force_for_admins(gov: Org, seed: Seed) -> None:
    seed.row(
        "governance.ai_setting", org_id=gov.org, key="max_cost_per_run", value=Json(5000), version=2
    )
    seed.row(
        "governance.ai_setting",
        org_id=gov.org,
        key="frontier_routing",
        value=Json({"enabled": False, "tasks": [], "data_space_ids": []}),
    )
    other = _org(seed)
    seed.row("governance.ai_setting", org_id=other.org, key="input_filter", value=Json("strict"))
    async with client(gov) as api:
        listed = await api.get("/v1/admin/ai-settings", headers=gov.headers("admin"))
        assert listed.status_code == 200, listed.text
        rows = listed.json()
        assert [(r["key"], r["value"], r["version"]) for r in rows] == [
            ("frontier_routing", {"enabled": False, "tasks": [], "data_space_ids": []}, 1),
            ("max_cost_per_run", 5000, 2),
        ]
        denied = await api.get("/v1/admin/ai-settings", headers=gov.headers("analyst"))
        assert denied.status_code == 403, denied.text


# ---------------------------------------------------------------- agent usage


def _agent(seed: Seed, gov: Org, name: str, budget: str | None = None) -> uuid.UUID:
    account = seed.row(
        "org.app_user",
        org_id=gov.org,
        kind="agent",
        username=f"agent-{name}-{gov.suffix}",
        display_name=name,
        owner_user_id=gov.users["admin"],
        status="active",
    )
    return seed.row(
        "agent.agent_def",
        org_id=gov.org,
        data_space_id=gov.space,
        code=f"{name}-{gov.suffix}",
        name=name,
        kind="reconcile",
        owner_user_id=gov.users["admin"],
        account_user_id=account,
        status="running",
        monthly_budget=budget,
        spent_this_month="250.00",
    )


def _run(
    seed: Seed,
    gov: Org,
    agent: uuid.UUID,
    at: datetime.datetime,
    status: str,
    *,
    seconds: int | None = None,
    cost: str = "0",
    is_test: bool = False,
) -> uuid.UUID:
    finished = status in ("succeeded", "failed", "cancelled")
    return seed.row(
        "agent.agent_run",
        org_id=gov.org,
        agent_id=agent,
        config_version=1,
        data_space_id=gov.space,
        trigger_kind="schedule",
        status=status,
        is_test=is_test,
        cost=cost,
        created_at=at,
        started_at=at,
        finished_at=at + datetime.timedelta(seconds=seconds or 0) if finished else None,
        stop_reason={
            "succeeded": "completed",
            "failed": "error",
            "cancelled": "user_cancelled",
        }.get(status),
    )


async def test_agent_usage_per_day_and_per_agent(gov: Org, seed: Seed) -> None:
    yesterday = datetime.datetime.combine(
        TODAY - datetime.timedelta(days=1), datetime.time(10), tzinfo=datetime.UTC
    )
    today = datetime.datetime.combine(TODAY, datetime.time(0, 5), tzinfo=datetime.UTC)
    recon = _agent(seed, gov, "Recon", budget="1000.00")
    report = _agent(seed, gov, "Report")
    ok = _run(seed, gov, recon, yesterday, "succeeded", seconds=60, cost="10.50")
    waited = _run(seed, gov, recon, yesterday, "succeeded", seconds=120, cost="4.50")
    failed = _run(seed, gov, recon, today, "failed", seconds=30, cost="1.00")
    _run(seed, gov, report, today, "running")
    # A test run is not production usage; a run outside the window is not counted.
    _run(seed, gov, recon, today, "succeeded", seconds=5, cost="99", is_test=True)
    _run(seed, gov, recon, yesterday - datetime.timedelta(days=30), "failed", seconds=1)
    # One run stopped twice for a person: counted once.
    for _ in range(2):
        seed.row(
            "agent.agent_checkpoint_hit",
            org_id=gov.org,
            run_id=waited,
            checkpoint="amount > 1bn",
            hitl_request_id=uuid.uuid4(),
        )
    for run, outcome, called in (
        (ok, "ok", yesterday),
        (ok, "error", yesterday),
        (failed, "ok", today),
    ):
        seed.row(
            "governance.model_usage",
            org_id=gov.org,
            task="planning",
            model_name="qwen",
            caller_kind="agent_run",
            caller_id=run,
            input_tokens=100,
            output_tokens=20,
            outcome=outcome,
            called_at=called,
        )
    # Another organisation's agent and runs stay out.
    other = _org(seed)
    _run(seed, other, _agent(seed, other, "Foreign"), today, "failed", seconds=3)

    window = {
        "from": (yesterday - datetime.timedelta(days=1)).isoformat(),
        "to": (NOW + datetime.timedelta(minutes=1)).isoformat(),
    }
    async with client(gov) as api:
        got = await api.get("/v1/admin/agent-usage", params=window, headers=gov.headers("admin"))
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["granularity"] == "day"
        totals = body["totals"]
        assert (totals["runs"], totals["needs_person"], totals["errors"]) == (4, 1, 1)
        assert totals["needs_person_rate"] == 0.25
        assert Decimal(totals["cost"]) == Decimal("16.00")
        assert (totals["model_calls"], totals["model_errors"], totals["tokens"]) == (3, 1, 360)
        # Three finished runs: 60, 120 and 30 seconds.
        assert totals["avg_duration_s"] == pytest.approx(70.0)
        days = {d["day"]: d for d in body["days"]}
        assert days[str(yesterday.date())]["runs"] == 2
        assert days[str(TODAY)]["runs"] == 2
        assert days[str(TODAY)]["errors"] == 1

        agents = {a["agent_id"]: a for a in body["agents"]}
        assert set(agents) == {str(recon), str(report)}
        assert agents[str(recon)]["name"] == "Recon"
        assert agents[str(recon)]["runs"] == 3
        assert agents[str(recon)]["error_rate"] == pytest.approx(1 / 3)
        assert agents[str(recon)]["budget_used_ratio"] == 0.25
        assert agents[str(report)]["budget_used_ratio"] is None
        assert [d["day"] for d in agents[str(recon)]["days"]] == [
            str(yesterday.date()),
            str(TODAY),
        ]

        backwards = await api.get(
            "/v1/admin/agent-usage",
            params={"from": window["to"], "to": window["from"]},
            headers=gov.headers("admin"),
        )
        assert backwards.status_code == 422, backwards.text
        too_long = await api.get(
            "/v1/admin/agent-usage",
            params={"from": "2020-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
            headers=gov.headers("admin"),
        )
        assert too_long.status_code == 422, too_long.text
        hourly = await api.get(
            "/v1/admin/agent-usage?granularity=hour", headers=gov.headers("admin")
        )
        assert hourly.status_code == 422, hourly.text
        denied = await api.get("/v1/admin/agent-usage", headers=gov.headers("analyst"))
        assert denied.status_code == 403, denied.text


# ---------------------------------------------------------------- schedule preview


def _next(day: datetime.date, weekday: int) -> datetime.date:
    """The first ISO `weekday` strictly after `day`."""
    ahead = (weekday - day.isoweekday()) % 7 or 7
    return day + datetime.timedelta(days=ahead)


async def test_schedule_preview_follows_the_work_calendar(gov: Org, seed: Seed) -> None:
    calendar = seed.row(
        "org.work_calendar",
        org_id=gov.org,
        code="VN",
        name="Lịch Việt Nam",
        time_zone="Asia/Ho_Chi_Minh",
        weekend_days=[6, 7],
        is_default=True,
    )
    tuesday = _next(TODAY + datetime.timedelta(days=1), 2)
    saturday = _next(tuesday, 6)
    seed.row(
        "org.work_calendar_day",
        org_id=gov.org,
        calendar_id=calendar,
        day=tuesday,
        kind="holiday",
        name="Nghỉ bù",
    )
    seed.row(
        "org.work_calendar_day",
        org_id=gov.org,
        calendar_id=calendar,
        day=saturday,
        kind="working_override",
        name="Làm bù",
    )
    other = _org(seed)
    foreign = seed.row("org.work_calendar", org_id=other.org, code="VN", name="Foreign")
    outbox_before = gov.one("SELECT count(*) FROM governance.outbox WHERE org_id = %s", gov.org)

    async with client(gov) as api:
        got = await api.post(
            "/v1/schedules:preview",
            json={"cron": "0 9 * * *", "work_calendar_id": str(calendar), "count": 12},
            headers=gov.headers("analyst"),
        )
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["timezone"] == "Asia/Ho_Chi_Minh"
        runs = [datetime.datetime.fromisoformat(r) for r in body["items"]]
        assert len(runs) == 12
        assert all(r.utcoffset() == datetime.timedelta(hours=7) for r in runs)
        assert all((r.hour, r.minute) == (9, 0) for r in runs)
        assert runs == sorted(runs) and runs[0] > NOW
        days = {r.date() for r in runs}
        assert tuesday not in days
        assert saturday in days
        assert all(d.isoweekday() < 6 or d == saturday for d in days)
        assert str(tuesday) in body["skipped_days"]

        utc = await api.post(
            "/v1/schedules:preview",
            json={"cron": "30 1 * * 1"},
            headers=gov.headers("analyst"),
        )
        assert utc.status_code == 200, utc.text
        assert utc.json()["timezone"] == "UTC"
        first = datetime.datetime.fromisoformat(utc.json()["items"][0])
        assert (first.isoweekday(), first.hour, first.minute) == (1, 1, 30)
        assert len(utc.json()["items"]) == 5

        for bad in (
            {"cron": "61 * * * *"},
            {"cron": "* * *"},
            {"cron": "0 9 * * *", "timezone": "Mars/Base"},
        ):
            refused = await api.post(
                "/v1/schedules:preview", json=bad, headers=gov.headers("analyst")
            )
            assert refused.status_code == 422, (bad, refused.text)
            assert refused.json()["code"] == "validation_failed"
        not_ours = await api.post(
            "/v1/schedules:preview",
            json={"cron": "0 9 * * *", "work_calendar_id": str(foreign)},
            headers=gov.headers("analyst"),
        )
        assert not_ours.status_code == 422, not_ours.text

        seed.row(
            "org.app_user",
            org_id=gov.org,
            external_id=f"kc-nobody-{gov.suffix}",
            username=f"nobody-{gov.suffix}",
            display_name="nobody",
            status="active",
        )
        denied = await api.post(
            "/v1/schedules:preview", json={"cron": "0 9 * * *"}, headers=gov.headers("nobody")
        )
        assert denied.status_code == 403, denied.text
    # A preview writes nothing.
    assert (
        gov.one("SELECT count(*) FROM governance.outbox WHERE org_id = %s", gov.org)
        == outbox_before
    )


# ---------------------------------------------------------------- data subject requests


async def test_dsr_list_shows_the_hash_never_an_identifier(gov: Org, seed: Seed) -> None:
    def dsr(status: str, received: datetime.datetime, org: uuid.UUID | None = None) -> uuid.UUID:
        return seed.row(
            "governance.dsr_request",
            org_id=org or gov.org,
            subject_hash=uuid.uuid4().hex * 2,
            request_kind="delete",
            received_at=received,
            due_at=received + datetime.timedelta(days=30),
            status=status,
            handler_id=gov.users["officer"],
            created_at=received,
        )

    first = dsr("review", NOW - datetime.timedelta(days=3))
    second = dsr("searching", NOW - datetime.timedelta(days=2))
    third = dsr("review", NOW - datetime.timedelta(days=1))
    other = _org(seed)
    dsr("review", NOW, org=other.org)

    async with client(gov) as api:
        page = await api.get("/v1/dsr?page_size=2", headers=gov.headers("officer"))
        assert page.status_code == 200, page.text
        assert [r["id"] for r in page.json()["items"]] == [str(third), str(second)]
        row = page.json()["items"][0]
        assert len(row["subject_hash"]) == 64
        assert "subject_identifier" not in row and "items" not in row
        rest = await api.get(
            f"/v1/dsr?page_size=2&cursor={page.json()['next_page_token']}",
            headers=gov.headers("officer"),
        )
        assert [r["id"] for r in rest.json()["items"]] == [str(first)]
        assert rest.json()["next_page_token"] == ""

        review = await api.get("/v1/dsr?status=review", headers=gov.headers("officer"))
        assert {r["id"] for r in review.json()["items"]} == {str(first), str(third)}
        by_due = await api.get("/v1/dsr?order_by=due_at asc", headers=gov.headers("officer"))
        assert [r["id"] for r in by_due.json()["items"]] == [str(first), str(second), str(third)]

        bad = await api.get("/v1/dsr?status=lost", headers=gov.headers("officer"))
        assert bad.status_code == 422, bad.text
        denied = await api.get("/v1/dsr", headers=gov.headers("admin"))
        assert denied.status_code == 403, denied.text
