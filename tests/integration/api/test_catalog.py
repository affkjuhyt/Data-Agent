"""Sources, scans, the dataset catalogue and the certification of consolidated dataset versions
(A2 "Nguồn và danh mục", UC-04-01..05, UC-14-05) against PostgreSQL.

Each test builds its own organisation: a data admin (AC04), the Data Space's steward (AC03), a
second steward-capable user who is not the Data Space's steward, the editor of a consolidation
spec, and an analyst who only asks; two Data Spaces and a second organisation for isolation.
The connector worker is not running: the tests assert the outbox event, not the effect.
"""

from __future__ import annotations

import datetime
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
    seed: Seed
    # The second organisation: its own Data Space, a member with every capability, a source.
    foreign_org: uuid.UUID = field(default_factory=uuid.uuid4)
    foreign_space: uuid.UUID = field(default_factory=uuid.uuid4)
    foreign_source: uuid.UUID = field(default_factory=uuid.uuid4)
    tokens: dict[str, uuid.UUID] = field(default_factory=dict)

    def headers(self, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token(who)}",
            "X-Data-Space": str(space or self.space),
        }

    def token(self, who: str) -> str:
        return f"kc-{who}-{self.org.hex[:8]}"

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM catalog.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
            value = row[0] if row else None
            # Another suite may register psycopg2's uuid adapter: compare ids as text.
            return str(value) if isinstance(value, uuid.UUID) else value


def _user(o: Org, org: uuid.UUID, name: str, role: uuid.UUID, spaces: list[uuid.UUID]) -> uuid.UUID:
    token = f"kc-{name}-{o.org.hex[:8]}"
    user = o.seed.row(
        "org.app_user",
        org_id=org,
        external_id=token,
        username=f"{name}-{org.hex[:6]}",
        display_name=name,
        status="active",
    )
    o.tokens[token] = org
    o.seed.row("org.user_role", org_id=org, user_id=user, role_id=role, source="manual")
    for space in spaces:
        o.seed.row(
            "org.data_space_member",
            org_id=org,
            data_space_id=space,
            principal_type="user",
            principal_id=user,
            role_id=role,
        )
    return user


def _roles(seed: Seed, org: uuid.UUID) -> dict[str, uuid.UUID]:
    caps = {
        "ADMIN_DATA": ["ask", "admin_data"],
        "STEWARD": ["ask", "steward"],
        "ASK": ["ask"],
        # Holds admin_data but not ask: the 403 of the ask endpoints.
        "ADMIN_ONLY": ["admin_data"],
        "ALL": ["ask", "admin_data", "steward"],
    }
    return {
        code: seed.row("org.role", org_id=org, code=code, name=code, capabilities=c)
        for code, c in caps.items()
    }


@pytest.fixture
def o(seed: Seed) -> Org:
    org = seed.org()
    roles = _roles(seed, org)
    keeper = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"keeper-{org.hex[:6]}",
        username="keeper",
        display_name="Keeper",
    )
    space = seed.row("org.data_space", org_id=org, code="DS-TD", name="Tín dụng", steward_id=keeper)
    other = seed.row(
        "org.data_space", org_id=org, code="DS-TT", name="Thị trường", steward_id=keeper
    )
    out = Org(org, space, other, {}, seed)
    both = [space, other]
    out.users = {
        "admin": _user(out, org, "admin", roles["ADMIN_DATA"], both),
        "steward": _user(out, org, "steward", roles["STEWARD"], both),
        "steward2": _user(out, org, "steward2", roles["STEWARD"], both),
        "editor": _user(out, org, "editor", roles["STEWARD"], [space]),
        "analyst": _user(out, org, "analyst", roles["ASK"], [space]),
        "adminonly": _user(out, org, "adminonly", roles["ADMIN_ONLY"], [space]),
    }
    # The Data Space's data steward is `steward`, its backup `editor` (BR-156).
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE org.data_space SET steward_id = %s, backup_steward_id = %s WHERE id = %s",
            (str(out.users["steward"]), str(out.users["editor"]), str(space)),
        )
    # A second organisation, whose ids the first one must never reach.
    foreign = seed.org()
    out.foreign_org = foreign
    froles = _roles(seed, foreign)
    fkeeper = seed.row(
        "org.app_user",
        org_id=foreign,
        external_id=f"fkeeper-{foreign.hex[:6]}",
        username="fkeeper",
        display_name="Keeper",
    )
    out.foreign_space = seed.row(
        "org.data_space", org_id=foreign, code="DS-X", name="X", steward_id=fkeeper
    )
    out.users["foreign"] = _user(out, foreign, "foreign", froles["ALL"], [out.foreign_space])
    out.foreign_source = seed.row(
        "catalog.source",
        org_id=foreign,
        data_space_id=out.foreign_space,
        name="Core",
        kind="postgresql",
        secret_ref=f"secret/tenants/{foreign}/core",
        status="active",
    )
    return out


class Tokens:
    def __init__(self, o: Org) -> None:
        self.o = o

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.o.tokens.get(token, self.o.org))}


def client(o: Org) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(o))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


def _ref(o: Org, name: str = "core") -> dict[str, str]:
    return {"secret_ref": f"secret/tenants/{o.org}/{name}"}


def _source_body(o: Org, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Core banking",
        "kind": "postgresql",
        "host": "core.bank.local",
        "port": 5432,
        "database_name": "core",
        "authority_level": "level_1",
        "scan_schemas": ["credit"],
        "credentials": _ref(o),
    }
    body.update(extra)
    return body


async def _create_source(api: httpx.AsyncClient, o: Org, **extra: Any) -> dict[str, Any]:
    made = await api.post("/v1/sources", json=_source_body(o, **extra), headers=o.headers("admin"))
    assert made.status_code == 201, made.text
    out: dict[str, Any] = made.json()
    return out


async def test_connection_picker_lists_catalogue_and_org_secret_refs_only(o: Org) -> None:
    _source_row(o, name="Core banking", secret_ref=f"secret/tenants/{o.org}/core")
    _source_row(o, space=o.other, name="Market data", secret_ref=f"secret/tenants/{o.org}/market")
    async with client(o) as api:
        connectors = await api.get("/v1/connectors", headers=o.headers("admin"))
        refs = await api.get("/v1/secret-refs", headers=o.headers("admin"))

    assert connectors.status_code == 200, connectors.text
    connector_items = connectors.json()["items"]
    assert {item["code"] for item in connector_items} >= {"postgresql", "clickhouse", "snowflake"}
    assert all("password" not in str(item).lower() for item in connector_items)

    assert refs.status_code == 200, refs.text
    assert {item["ref"] for item in refs.json()["items"]} == {f"secret/tenants/{o.org}/core"}
    assert all(set(item) == {"ref", "label"} for item in refs.json()["items"])
    assert "secret_value" not in refs.text


async def test_connection_catalogue_and_secret_refs_require_admin_data(o: Org) -> None:
    async with client(o) as api:
        denied_catalogue = await api.get("/v1/connectors", headers=o.headers("analyst"))
        denied_refs = await api.get("/v1/secret-refs", headers=o.headers("analyst"))

    assert denied_catalogue.status_code == 403
    assert denied_refs.status_code == 403


def _source_row(o: Org, space: uuid.UUID | None = None, **values: Any) -> uuid.UUID:
    values.setdefault("name", f"src-{uuid.uuid4().hex[:6]}")
    values.setdefault("kind", "postgresql")
    values.setdefault("secret_ref", f"secret/tenants/{o.org}/x")
    values.setdefault("status", "active")
    return o.seed.row("catalog.source", org_id=o.org, data_space_id=space or o.space, **values)


def _no_secret(view: dict[str, Any]) -> None:
    # A3: every secret reference is dropped from a response (BR-14).
    text = str(view)
    assert "secret_ref" not in view and "writeback_secret_ref" not in view
    assert "secret/tenants" not in text


# --- sources ------------------------------------------------------------------------------------


# AC-04-01-05
@pytest.mark.req("AC-04-01-05")
async def test_admin_registers_a_source_as_a_draft_without_returning_its_secret(o: Org) -> None:
    async with client(o) as api:
        made = await _create_source(
            api,
            o,
            writeback_credentials=_ref(o, "core-write"),
            max_concurrency=8,
        )
        _no_secret(made)
        assert made["status"] == "draft" and made["mode"] == "federated"
        assert made["has_credentials"] is True and made["has_writeback_credentials"] is True
        assert made["authority_level"] == "level_1" and made["max_concurrency"] == 8
        # BR-99 default where the body is silent.
        assert made["query_timeout_s"] == 120
        # Only the OpenBao path is stored, never a password (BR-14).
        stored = o.one("SELECT secret_ref FROM catalog.source WHERE id = %s", made["id"])
        assert stored == f"secret/tenants/{o.org}/core"
        assert o.one("SELECT created_by FROM catalog.source WHERE id = %s", made["id"]) == str(
            o.users["admin"]
        )
        events = o.events(made["id"])
        assert [e for e, _ in events] == ["source.created"]
        assert events[0][1]["source_id"] == made["id"]
        assert events[0][1]["data_space_id"] == str(o.space)

        listed = await api.get("/v1/sources", headers=o.headers("admin"))
        assert listed.status_code == 200
        assert [s["id"] for s in listed.json()["items"]] == [made["id"]]
        _no_secret(listed.json())
        filtered = await api.get("/v1/sources?status=active", headers=o.headers("admin"))
        assert filtered.json()["items"] == []
        by_kind = await api.get("/v1/sources?kind=postgresql", headers=o.headers("admin"))
        assert len(by_kind.json()["items"]) == 1

        # The same name in the Data Space is the UNIQUE's 409.
        again = await api.post("/v1/sources", json=_source_body(o), headers=o.headers("admin"))
        assert again.status_code == 409, again.text
        assert again.json()["code"] == "conflict"
        # Another Data Space holds its own list.
        there = await api.get("/v1/sources", headers=o.headers("admin", o.other))
        assert there.json()["items"] == []


async def test_a_source_without_credentials_and_the_default_mode(o: Org) -> None:
    async with client(o) as api:
        made = await _create_source(api, o, name="Cổng API", kind="rest_api", credentials=None)
    # UC-04-01 step 3: an API is ingest only, and that is the default for it.
    assert made["mode"] == "ingest"
    assert made["has_credentials"] is False and made["has_writeback_credentials"] is False


# AC-04-01-02
@pytest.mark.req("AC-04-01-02")
async def test_federated_mode_on_an_ingest_only_kind_is_a_business_rule(o: Org) -> None:
    async with client(o) as api:
        refused = await api.post(
            "/v1/sources",
            json=_source_body(o, kind="rest_api", mode="federated"),
            headers=o.headers("admin"),
        )
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "business_rule_violated"
    assert o.one("SELECT count(*) FROM catalog.source WHERE org_id = %s", o.org) == 0


# AC-04-01-04
@pytest.mark.req("AC-04-01-04")
async def test_a_secret_path_outside_the_organisation_is_refused(o: Org) -> None:
    bad_refs = [
        f"secret/tenants/{o.foreign_org}/core",
        f"secret/tenants/{o.org}/../{o.foreign_org}/core",
        f"secret/tenants/{o.org}/",
        "secret/core",
        f"secret/tenants/{o.org}//core",
    ]
    async with client(o) as api:
        for ref in bad_refs:
            refused = await api.post(
                "/v1/sources",
                json=_source_body(o, credentials={"secret_ref": ref}),
                headers=o.headers("admin"),
            )
            assert refused.status_code == 422, (ref, refused.text)
            assert refused.json()["code"] == "validation_failed"
        writeback = await api.post(
            "/v1/sources",
            json=_source_body(
                o, writeback_credentials={"secret_ref": f"secret/tenants/{o.foreign_org}/w"}
            ),
            headers=o.headers("admin"),
        )
        assert writeback.status_code == 422
        # A password is never accepted, only a reference (BR-14).
        password = await api.post(
            "/v1/sources",
            json=_source_body(o, credentials={**_ref(o), "password": "s3cret"}),
            headers=o.headers("admin"),
        )
        assert password.status_code == 422
    # No source row is written for a refused reference (UC-04-01 E2).
    assert o.one("SELECT count(*) FROM catalog.source WHERE org_id = %s", o.org) == 0


# AC-04-01-06
@pytest.mark.req("AC-04-01-06")
async def test_load_limits_outside_br99_are_validation_errors(o: Org) -> None:
    async with client(o) as api:
        for extra in ({"max_concurrency": 0}, {"max_concurrency": 65}, {"query_timeout_s": 4}):
            refused = await api.post(
                "/v1/sources", json=_source_body(o, **extra), headers=o.headers("admin")
            )
            assert refused.status_code == 422, refused.text
            assert refused.json()["code"] == "validation_failed"
        made = await _create_source(api, o)
        assert made["max_concurrency"] == 4
        patched = await api.patch(
            f"/v1/sources/{made['id']}", json={"query_timeout_s": 3601}, headers=o.headers("admin")
        )
        assert patched.status_code == 422
        assert patched.json()["code"] == "validation_failed"


async def test_sources_need_admin_data(o: Org) -> None:
    source = _source_row(o)
    async with client(o) as api:
        for who in ("analyst", "steward"):
            h = o.headers(who)
            calls = [
                await api.get("/v1/sources", headers=h),
                await api.post("/v1/sources", json=_source_body(o), headers=h),
                await api.patch(f"/v1/sources/{source}", json={"name": "x"}, headers=h),
                await api.post(f"/v1/sources/{source}:test", headers=h),
                await api.put(f"/v1/sources/{source}/scan-schedule", json={}, headers=h),
                await api.post(f"/v1/sources/{source}/scans", json={}, headers=h),
                await api.get(f"/v1/scan-runs/{uuid.uuid4()}", headers=h),
            ]
            assert [c.status_code for c in calls] == [403] * len(calls), [c.text for c in calls]
    assert o.events(source) == []


async def test_a_source_of_another_data_space_or_organisation_is_not_found(o: Org) -> None:
    there = _source_row(o, o.other)
    async with client(o) as api:
        for source in (there, o.foreign_source):
            h = o.headers("admin")
            calls = [
                await api.patch(f"/v1/sources/{source}", json={"name": "x"}, headers=h),
                await api.post(f"/v1/sources/{source}:test", headers=h),
                await api.put(
                    f"/v1/sources/{source}/scan-schedule", json={"scan_cron": "@daily"}, headers=h
                ),
                await api.post(f"/v1/sources/{source}/scans", json={}, headers=h),
            ]
            assert [c.status_code for c in calls] == [404] * 4, [c.text for c in calls]
        # The foreign org's member cannot name our Data Space either.
        foreign = await api.get("/v1/sources", headers=o.headers("foreign"))
        assert foreign.status_code == 404
        listed = await api.get("/v1/sources", headers=o.headers("foreign", o.foreign_space))
        assert listed.status_code == 200
        _no_secret(listed.json())
        assert [s["id"] for s in listed.json()["items"]] == [str(o.foreign_source)]
    assert o.events(there) == [] and o.events(o.foreign_source) == []
    assert o.one("SELECT name FROM catalog.source WHERE id = %s", there).startswith("src-")


async def test_patch_updates_fields_and_status_rules(o: Org) -> None:
    async with client(o) as api:
        made = await _create_source(api, o)
        url = f"/v1/sources/{made['id']}"
        h = o.headers("admin")
        patched = await api.patch(
            url,
            json={"name": " Core T24 ", "max_concurrency": 2, "freshness_sla": "PT6H"},
            headers=h,
        )
        assert patched.status_code == 200, patched.text
        view = patched.json()
        _no_secret(view)
        assert view["name"] == "Core T24" and view["max_concurrency"] == 2
        assert o.one("SELECT max_concurrency FROM catalog.source WHERE id = %s", made["id"]) == 2
        updated = [p for e, p in o.events(made["id"]) if e == "source.updated"]
        assert updated[-1]["fields"] == ["freshness_sla", "max_concurrency", "name"]
        assert updated[-1]["source_id"] == made["id"]

        # A draft never passed its test: it cannot be made active nor paused by a person.
        to_active = await api.patch(url, json={"status": "active"}, headers=h)
        assert to_active.status_code == 409 and to_active.json()["code"] == "invalid_state"
        to_paused = await api.patch(url, json={"status": "paused"}, headers=h)
        assert to_paused.status_code == 409
        # draft, testing and needs_fix are the system's, not a person's.
        system = await api.patch(url, json={"status": "testing"}, headers=h)
        assert system.status_code == 422
        nulled = await api.patch(url, json={"name": None}, headers=h)
        assert nulled.status_code == 422
        removed = await api.patch(url, json={"credentials": None}, headers=h)
        assert removed.status_code == 422

        # A tested, active source is paused and resumed.
        o.seed.conn.cursor().execute(
            "UPDATE catalog.source SET status = 'active', last_checked_at = now() WHERE id = %s",
            (made["id"],),
        )
        paused = await api.patch(url, json={"status": "paused"}, headers=h)
        assert paused.status_code == 200 and paused.json()["status"] == "paused"
        resumed = await api.patch(url, json={"status": "active"}, headers=h)
        assert resumed.status_code == 200 and resumed.json()["status"] == "active"

        # A new account goes back to draft until it passes a test again (UC-04-01 E1).
        swapped = await api.patch(url, json={"credentials": _ref(o, "core-v2")}, headers=h)
        assert swapped.status_code == 200, swapped.text
        assert swapped.json()["status"] == "draft"
        assert swapped.json()["status_reason"] == "credentials_changed"
        assert swapped.json()["last_checked_at"] is None
        _no_secret(swapped.json())
        assert (
            o.one("SELECT secret_ref FROM catalog.source WHERE id = %s", made["id"])
            == f"secret/tenants/{o.org}/core-v2"
        )
        foreign_ref = await api.patch(
            url,
            json={"credentials": {"secret_ref": f"secret/tenants/{o.foreign_org}/core"}},
            headers=h,
        )
        assert foreign_ref.status_code == 422
        assert (
            o.one("SELECT secret_ref FROM catalog.source WHERE id = %s", made["id"])
            == f"secret/tenants/{o.org}/core-v2"
        )

        # Archived is final.
        archived = await api.patch(url, json={"status": "archived"}, headers=h)
        assert archived.status_code == 200 and archived.json()["status"] == "archived"
        back = await api.patch(url, json={"status": "paused"}, headers=h)
        assert back.status_code == 409
        account = await api.patch(url, json={"credentials": _ref(o, "core-v3")}, headers=h)
        assert account.status_code == 409

        # Renaming onto another source's name is the UNIQUE's 409.
        second = await _create_source(api, o, name="DWH")
        clash = await api.patch(f"/v1/sources/{second['id']}", json={"name": "Core T24"}, headers=h)
        assert clash.status_code == 409


async def test_connection_test_is_202_through_the_connector_worker(o: Org) -> None:
    async with client(o) as api:
        made = await _create_source(api, o)
        h = o.headers("admin")
        tested = await api.post(f"/v1/sources/{made['id']}:test", headers=h)
        assert tested.status_code == 202, tested.text
        body = tested.json()
        assert body["id"] == made["id"] and body["status"] == "testing"
        assert body["poll_after_ms"] > 0
        assert tested.headers["Location"]
        assert o.one("SELECT status FROM catalog.source WHERE id = %s", made["id"]) == "testing"
        event = dict(o.events(made["id"]))["source.test_requested"]
        assert event["source_id"] == made["id"] and event["previous_status"] == "draft"
        assert event["requested_by"] == str(o.users["admin"])
        # The worker reads the account at secret_ref; the event never carries it.
        assert "secret" not in str(event)

        # A source in use stays usable while it is re-checked.
        active = _source_row(o, last_checked_at="2026-10-01T00:00:00Z")
        retest = await api.post(f"/v1/sources/{active}:test", headers=h)
        assert retest.status_code == 202 and retest.json()["status"] == "active"

        archived = _source_row(o, status="archived")
        refused = await api.post(f"/v1/sources/{archived}:test", headers=h)
        assert refused.status_code == 409 and refused.json()["code"] == "invalid_state"
        assert o.events(archived) == []


# AC-04-05-01
@pytest.mark.req("AC-04-05-01")
async def test_scan_schedule_returns_the_next_five_runs_on_working_days(o: Org) -> None:
    calendar = o.seed.row(
        "org.work_calendar", org_id=o.org, code="VN", name="Lịch VN", weekend_days=[6, 7]
    )
    source = _source_row(o)
    async with client(o) as api:
        h = o.headers("admin")
        body = {
            "scan_cron": "0 1 * * *",
            "profile_cron": "@weekly",
            "scan_window": {"mon": ["22:00", "05:00"]},
            "work_calendar_id": str(calendar),
        }
        put = await api.put(f"/v1/sources/{source}/scan-schedule", json=body, headers=h)
        assert put.status_code == 200, put.text
        view = put.json()
        _no_secret(view)
        assert view["scan_cron"] == "0 1 * * *" and view["profile_cron"] == "@weekly"
        assert view["work_calendar_id"] == str(calendar)
        assert len(view["next_scans"]) == 5
        # Weekend days of the calendar are skipped (UC-04-05 step 2).
        days = [datetime.datetime.fromisoformat(t) for t in view["next_scans"]]
        assert all(d.isoweekday() not in (6, 7) for d in days)
        assert all(d.hour == 1 and d.minute == 0 for d in days)
        assert days == sorted(days)
        assert o.one("SELECT scan_cron FROM catalog.source WHERE id = %s", source) == "0 1 * * *"
        event = dict(o.events(source))["source.scan_schedule_changed"]
        assert event["scan_cron"] == "0 1 * * *"
        assert event["work_calendar_id"] == str(calendar)

        bad = await api.put(
            f"/v1/sources/{source}/scan-schedule", json={"scan_cron": "61 * * * *"}, headers=h
        )
        assert bad.status_code == 422 and bad.json()["code"] == "validation_failed"
        # Another organisation's calendar is not ours.
        foreign_calendar = o.seed.row(
            "org.work_calendar", org_id=o.foreign_org, code="VN", name="Lịch"
        )
        foreign = await api.put(
            f"/v1/sources/{source}/scan-schedule",
            json={"work_calendar_id": str(foreign_calendar)},
            headers=h,
        )
        assert foreign.status_code == 422
        cleared = await api.put(f"/v1/sources/{source}/scan-schedule", json={}, headers=h)
        assert cleared.status_code == 200
        assert cleared.json()["scan_cron"] is None and cleared.json()["next_scans"] == []

        archived = _source_row(o, status="archived")
        refused = await api.put(
            f"/v1/sources/{archived}/scan-schedule", json={"scan_cron": "@daily"}, headers=h
        )
        assert refused.status_code == 409


# AC-04-05-04
@pytest.mark.req("AC-04-05-04")
async def test_scan_now_is_202_and_one_running_scan_per_kind(o: Org) -> None:
    source = _source_row(o)
    async with client(o) as api:
        h = o.headers("admin")
        started = await api.post(
            f"/v1/sources/{source}/scans", json={"tables": ["credit.loan"]}, headers=h
        )
        assert started.status_code == 202, started.text
        run = started.json()["id"]
        assert started.json()["status"] == "running"
        assert started.headers["Location"] == f"/v1/scan-runs/{run}"
        assert o.one("SELECT trigger FROM catalog.scan_run WHERE id = %s", run) == "manual"
        event = dict(o.events(run))["scan_run.created"]
        assert event["source_id"] == str(source) and event["tables"] == ["credit.loan"]
        assert event["kind"] == "schema"

        # BR-103: never two running scans of the same kind on one source.
        second = await api.post(f"/v1/sources/{source}/scans", json={}, headers=h)
        assert second.status_code == 409, second.text
        assert second.json()["code"] == "conflict"
        profile = await api.post(f"/v1/sources/{source}/scans", json={"kind": "profile"}, headers=h)
        assert profile.status_code == 202
        no_body = await api.post(f"/v1/sources/{_source_row(o)}/scans", headers=h)
        assert no_body.status_code == 202, no_body.text

        fetched = await api.get(f"/v1/scan-runs/{run}", headers=h)
        assert fetched.status_code == 200
        assert fetched.json()["source_id"] == str(source)
        assert fetched.json()["status"] == "running" and fetched.json()["tables_scanned"] == 0

        # UC-04-02 precondition: a source that has not passed its test is not scanned.
        for status in ("draft", "testing", "paused", "archived"):
            not_ready = _source_row(o, status=status)
            refused = await api.post(f"/v1/sources/{not_ready}/scans", json={}, headers=h)
            assert refused.status_code == 409, (status, refused.text)
            assert refused.json()["code"] == "invalid_state"

        # A run of another Data Space or organisation is not found.
        there = _source_row(o, o.other)
        other_run = o.seed.row(
            "catalog.scan_run", org_id=o.org, source_id=there, kind="schema", trigger="manual"
        )
        foreign_run = o.seed.row(
            "catalog.scan_run",
            org_id=o.foreign_org,
            source_id=o.foreign_source,
            kind="schema",
            trigger="manual",
        )
        for hidden in (other_run, foreign_run, uuid.uuid4()):
            missing = await api.get(f"/v1/scan-runs/{hidden}", headers=h)
            assert missing.status_code == 404


# --- datasets -----------------------------------------------------------------------------------


def _dataset(
    o: Org, source: uuid.UUID | None, space: uuid.UUID | None = None, **v: Any
) -> uuid.UUID:
    v.setdefault("schema_name", "credit")
    v.setdefault("table_name", f"t_{uuid.uuid4().hex[:6]}")
    if source is None:
        v.setdefault("origin", "derived")
    return o.seed.row(
        "catalog.dataset", org_id=o.org, data_space_id=space or o.space, source_id=source, **v
    )


def _column(o: Org, dataset: uuid.UUID, name: str, ordinal: int, **v: Any) -> uuid.UUID:
    v.setdefault("data_type", "varchar")
    v.setdefault("normalized_type", "string")
    return o.seed.row(
        "catalog.column_meta",
        org_id=o.org,
        dataset_id=dataset,
        dataset_version=1,
        name=name,
        ordinal=ordinal,
        **v,
    )


# AC-04-02-02
@pytest.mark.req("AC-04-02-02")
async def test_dataset_detail_masks_the_values_of_personal_columns(o: Org) -> None:
    source = _source_row(o)
    dataset = _dataset(o, source, table_name="khach_hang", description="Khách hàng cá nhân")
    run = o.seed.row(
        "catalog.scan_run",
        org_id=o.org,
        source_id=source,
        kind="profile",
        trigger="manual",
        status="succeeded",
    )
    cccd = _column(o, dataset, "so_cccd", 1, pii_label="national_id")
    city = _column(o, dataset, "tinh", 2)
    _column(o, dataset, "cu", 3, removed_in_version=2)
    for column, top in ((cccd, "001203004567"), (city, "Hà Nội")):
        o.seed.row(
            "catalog.column_profile",
            org_id=o.org,
            column_id=column,
            scan_run_id=run,
            sample_rows=1000,
            null_ratio=0.1,
            min_value=top,
            max_value=top,
            top_values=Json([{"value": top, "count": 7}]),
        )
    async with client(o) as api:
        got = await api.get(f"/v1/datasets/{dataset}", headers=o.headers("analyst"))
    assert got.status_code == 200, got.text
    columns = {c["name"]: c for c in got.json()["columns"]}
    # The removed column is not current.
    assert list(columns) == ["so_cccd", "tinh"]
    masked = columns["so_cccd"]["profile"]
    assert masked["top_values"] == [{"value": "***", "count": 7}]
    assert masked["min_value"] is None and masked["max_value"] is None
    assert "001203004567" not in got.text
    assert columns["tinh"]["profile"]["top_values"] == [{"value": "Hà Nội", "count": 7}]


async def test_dataset_list_filters_and_isolation(o: Org) -> None:
    source = _source_row(o)
    loan = _dataset(o, source, table_name="khoan_vay", enabled=True)
    _dataset(o, source, table_name="tai_khoan", display_name="Tài khoản")
    derived = _dataset(o, None, table_name="du_no_tong_hop")
    mine = _dataset(
        o,
        None,
        table_name="tai_tay",
        origin="upload",
        scope="personal",
        created_by=o.users["analyst"],
        expires_at="2027-01-01T00:00:00Z",
    )
    theirs = _dataset(
        o,
        None,
        table_name="cua_nguoi_khac",
        origin="upload",
        scope="personal",
        created_by=o.users["admin"],
        expires_at="2027-01-01T00:00:00Z",
    )
    elsewhere = _dataset(o, None, o.other, table_name="khac")
    deleted = _dataset(o, source, table_name="da_xoa", deleted_at="2026-10-01T00:00:00Z")
    async with client(o) as api:
        h = o.headers("analyst")

        async def ids(query: str = "") -> set[str]:
            r = await api.get(f"/v1/datasets{query}", headers=h)
            assert r.status_code == 200, r.text
            return {d["id"] for d in r.json()["items"]}

        everything = await ids()
        # BR-111: a personal table is its owner's only.
        assert str(mine) in everything and str(theirs) not in everything
        assert str(elsewhere) not in everything and str(deleted) not in everything
        assert len(everything) == 4
        assert await ids("?enabled=true") == {str(loan)}
        assert await ids(f"?source_id={source}") == {str(loan), *(await ids("?q=tai_khoan"))}
        assert await ids("?origin=derived") == {str(derived)}
        assert len(await ids("?q=Tài khoản")) == 1
        # LIKE wildcards in q are literal.
        assert await ids("?q=%25") == set()
        paged = await api.get("/v1/datasets?page_size=2", headers=h)
        assert len(paged.json()["items"]) == 2 and paged.json()["next_page_token"]
        rest = await api.get(
            f"/v1/datasets?page_size=2&page_token={paged.json()['next_page_token']}", headers=h
        )
        assert {d["id"] for d in paged.json()["items"] + rest.json()["items"]} == everything

        for hidden in (theirs, elsewhere, deleted):
            assert (await api.get(f"/v1/datasets/{hidden}", headers=h)).status_code == 404
            assert (await api.get(f"/v1/datasets/{hidden}/versions", headers=h)).status_code == 404
        assert (await api.get(f"/v1/datasets/{mine}", headers=h)).status_code == 200
        # A user without ask reads no catalogue.
        no_ask = o.headers("adminonly")
        for path in ("/v1/datasets", f"/v1/datasets/{loan}", f"/v1/datasets/{loan}/versions"):
            assert (await api.get(path, headers=no_ask)).status_code == 403


# AC-04-04-01
@pytest.mark.req("AC-04-04-01")
async def test_enabling_a_table_needs_its_personal_columns_confirmed(o: Org) -> None:
    source = _source_row(o)
    dataset = _dataset(o, source, table_name="khach_hang")
    cccd = _column(o, dataset, "so_cccd", 1, pii_label="national_id")
    phone = _column(o, dataset, "dien_thoai", 2, pii_label="phone")
    day = _column(o, dataset, "ngay", 3, normalized_type="date", data_type="date")
    async with client(o) as api:
        h = o.headers("admin")
        url = f"/v1/datasets/{dataset}"
        refused = await api.patch(
            url, json={"enabled": True, "steward_id": str(o.users["steward"])}, headers=h
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "business_rule_violated"
        assert o.one("SELECT enabled FROM catalog.dataset WHERE id = %s", dataset) is False
        assert o.events(dataset) == []

        enabled = await api.patch(
            url,
            json={
                "enabled": True,
                "steward_id": str(o.users["steward"]),
                "description": "Danh sách khách hàng",
                "business_date_col": "ngay",
                "columns": [
                    {"id": str(cccd), "pii_confirmed": True},
                    {"id": str(phone), "pii_confirmed": True, "description": "Số điện thoại"},
                ],
            },
            headers=h,
        )
        assert enabled.status_code == 200, enabled.text
        view = enabled.json()
        assert view["enabled"] is True and view["steward_id"] == str(o.users["steward"])
        assert view["business_date_col"] == "ngay"
        assert all(c["pii_confirmed"] for c in view["columns"] if c["pii_label"])
        event = dict(o.events(dataset))["dataset.updated"]
        assert event["enabled"] is True and event["dataset_id"] == str(dataset)
        assert "columns" in event["fields"] and "enabled" in event["fields"]

        # A new label on an enabled table is unconfirmed, so the change is refused.
        relabel = await api.patch(
            url, json={"columns": [{"id": str(day), "pii_label": "other"}]}, headers=h
        )
        assert relabel.status_code == 422
        assert o.one("SELECT pii_label FROM catalog.column_meta WHERE id = %s", day) is None
        # Editing the description of an enabled table stays possible.
        described = await api.patch(url, json={"display_name": "Khách hàng"}, headers=h)
        assert described.status_code == 200

        unknown_col = await api.patch(url, json={"business_date_col": "khong_co"}, headers=h)
        assert unknown_col.status_code == 422
        unknown_id = await api.patch(url, json={"columns": [{"id": str(uuid.uuid4())}]}, headers=h)
        assert unknown_id.status_code == 422
        stranger = await api.patch(url, json={"steward_id": str(o.users["foreign"])}, headers=h)
        assert stranger.status_code == 422
        nulled = await api.patch(url, json={"enabled": None}, headers=h)
        assert nulled.status_code == 422

        # The analyst only reads; another Data Space does not see the table.
        assert (
            await api.patch(url, json={"enabled": False}, headers=o.headers("analyst"))
        ).status_code == 403
        hidden = await api.patch(url, json={"enabled": False}, headers=o.headers("admin", o.other))
        assert hidden.status_code == 404
        foreign = await api.patch(
            url, json={"enabled": False}, headers=o.headers("foreign", o.foreign_space)
        )
        assert foreign.status_code == 404
        assert o.one("SELECT enabled FROM catalog.dataset WHERE id = %s", dataset) is True


async def test_dataset_versions_newest_first(o: Org) -> None:
    source = _source_row(o)
    dataset = _dataset(o, source)
    for version in (1, 2, 3):
        o.seed.row(
            "catalog.dataset_version",
            org_id=o.org,
            dataset_id=dataset,
            version=version,
            schema_hash=f"h{version}",
            change_kind="initial" if version == 1 else "additive",
            valid_from=f"2026-0{version}-01T00:00:00Z",
        )
    async with client(o) as api:
        h = o.headers("analyst")
        first = await api.get(f"/v1/datasets/{dataset}/versions?page_size=2", headers=h)
        assert first.status_code == 200, first.text
        assert [v["version"] for v in first.json()["items"]] == [3, 2]
        token = first.json()["next_page_token"]
        rest = await api.get(f"/v1/datasets/{dataset}/versions?page_token={token}", headers=h)
        assert [v["version"] for v in rest.json()["items"]] == [1]
        assert not rest.json()["next_page_token"]


# --- certification (BR-156) ---------------------------------------------------------------------


def _consolidated_version(o: Org, space: uuid.UUID | None = None) -> uuid.UUID:
    dataset = _dataset(o, None, space, origin="consolidated", table_name="lai_suat_huy_dong")
    return o.seed.row(
        "catalog.dataset_version",
        org_id=o.org,
        dataset_id=dataset,
        version=1,
        schema_hash="h1",
        valid_from="2026-10-01T00:00:00Z",
        created_by=o.users["editor"],
    )


# AC-14-05-05, AC-14-05-08
@pytest.mark.req("AC-14-05-05", "AC-14-05-08")
async def test_the_steward_certifies_revokes_and_recertifies(o: Org) -> None:
    version = _consolidated_version(o)
    async with client(o) as api:
        h = o.headers("steward")
        certified = await api.post(
            f"/v1/dataset-versions/{version}:certify", json={"note": "Đã đối chiếu"}, headers=h
        )
        assert certified.status_code == 200, certified.text
        view = certified.json()
        assert view["certified_by"] == str(o.users["steward"]) and view["certified_at"]
        assert view["certification"]["action"] == "certify"
        assert view["certification"]["note"] == "Đã đối chiếu"
        events = o.events(version)
        assert [e for e, _ in events] == ["catalog.dataset_version.certified"]
        assert events[0][1]["dataset_version_id"] == str(version)

        twice = await api.post(f"/v1/dataset-versions/{version}:certify", headers=h)
        assert twice.status_code == 409 and twice.json()["code"] == "invalid_state"

        revoked = await api.post(
            f"/v1/dataset-versions/{version}:revoke-certification",
            json={"reason": "ngân hàng nguồn sửa số tuần 41"},
            headers=h,
        )
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["certified_at"] is None and revoked.json()["certified_by"] is None
        assert revoked.json()["certification"]["reason"] == "ngân hàng nguồn sửa số tuần 41"
        again = await api.post(
            f"/v1/dataset-versions/{version}:revoke-certification",
            json={"reason": "lần hai"},
            headers=h,
        )
        assert again.status_code == 409 and again.json()["code"] == "invalid_state"

        recertified = await api.post(f"/v1/dataset-versions/{version}:certify", headers=h)
        assert recertified.status_code == 200
    assert [e for e, _ in o.events(version)] == [
        "catalog.dataset_version.certified",
        "catalog.dataset_version.certification_revoked",
        "catalog.dataset_version.certified",
    ]
    # The history keeps all three (append only).
    assert (
        o.one(
            "SELECT count(*) FROM catalog.dataset_version_certification"
            " WHERE dataset_version_id = %s",
            version,
        )
        == 3
    )


# AC-14-05-07
@pytest.mark.req("AC-14-05-07")
async def test_certification_refusals(o: Org) -> None:
    version = _consolidated_version(o)
    async with client(o) as api:
        url = f"/v1/dataset-versions/{version}"
        # The spec's last editor is the backup steward here, and still cannot certify.
        creator = await api.post(f"{url}:certify", headers=o.headers("editor"))
        assert creator.status_code == 403 and creator.json()["code"] == "policy_denied"
        # The steward capability alone is not the Data Space's steward.
        not_steward = await api.post(f"{url}:certify", headers=o.headers("steward2"))
        assert not_steward.status_code == 403 and not_steward.json()["code"] == "policy_denied"
        no_capability = await api.post(f"{url}:certify", headers=o.headers("admin"))
        assert no_capability.status_code == 403
        assert (
            o.one("SELECT certified_at FROM catalog.dataset_version WHERE id = %s", version) is None
        )

        h = o.headers("steward")
        assert (await api.post(f"{url}:certify", headers=h)).status_code == 200
        for reason in ("", "   "):
            blank = await api.post(
                f"{url}:revoke-certification", json={"reason": reason}, headers=h
            )
            assert blank.status_code == 422 and blank.json()["code"] == "validation_failed"
        missing = await api.post(f"{url}:revoke-certification", json={}, headers=h)
        assert missing.status_code == 422
        stranger = await api.post(
            f"{url}:revoke-certification", json={"reason": "x"}, headers=o.headers("steward2")
        )
        assert stranger.status_code == 403 and stranger.json()["code"] == "policy_denied"
        assert o.one(
            "SELECT certified_by FROM catalog.dataset_version WHERE id = %s", version
        ) == str(o.users["steward"])

        # Only a consolidated dataset's versions are certified.
        plain = o.seed.row(
            "catalog.dataset_version",
            org_id=o.org,
            dataset_id=_dataset(o, _source_row(o)),
            version=1,
            schema_hash="h",
            valid_from="2026-10-01T00:00:00Z",
        )
        not_consolidated = await api.post(f"/v1/dataset-versions/{plain}:certify", headers=h)
        assert not_consolidated.status_code == 422
        assert not_consolidated.json()["code"] == "business_rule_violated"

        # A version of another Data Space is not found from this one.
        elsewhere = _consolidated_version(o, o.other)
        hidden = await api.post(f"/v1/dataset-versions/{elsewhere}:certify", headers=h)
        assert hidden.status_code == 404
        unknown = await api.post(f"/v1/dataset-versions/{uuid.uuid4()}:certify", headers=h)
        assert unknown.status_code == 404
    assert o.events(elsewhere) == []


async def test_one_source_is_read_and_names_its_writeback_tables(o: Org) -> None:
    # 12/10/2026: GET /v1/sources/{id} is where :test's Location points; writeback_tables lists
    # the tables a writeback may write (UC-06-04).
    source = _source_row(o, writeback_secret_ref=f"secret/tenants/{o.org}/wb")
    elsewhere = _source_row(o, o.other)
    async with client(o) as api:
        h = o.headers("admin")
        got = await api.get(f"/v1/sources/{source}", headers=h)
        assert got.status_code == 200, got.text
        _no_secret(got.json())
        assert got.json()["has_writeback_credentials"] is True
        assert got.json()["writeback_tables"] == []
        patched = await api.patch(
            f"/v1/sources/{source}",
            json={"writeback_tables": ["core.du_no", "core.du_no", "core.tai_khoan"]},
            headers=h,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["writeback_tables"] == ["core.du_no", "core.tai_khoan"]
        bad = await api.patch(
            f"/v1/sources/{source}", json={"writeback_tables": ["du_no; drop"]}, headers=h
        )
        assert bad.status_code == 422
        tested = await api.post(f"/v1/sources/{source}:test", headers=h)
        assert tested.headers["Location"] == f"/v1/sources/{source}"
        hidden = await api.get(f"/v1/sources/{elsewhere}", headers=h)
        assert hidden.status_code == 404
        no_admin = await api.get(f"/v1/sources/{source}", headers=o.headers("steward"))
        assert no_admin.status_code == 403


async def test_activation_keeps_tables_in_the_sources_data_space_and_their_rules(o: Org) -> None:
    source = _source_row(o)
    plain = _dataset(o, source, table_name="du_no")
    personal = _dataset(o, source, table_name="khach_hang")
    _column(o, personal, "so_cccd", 1, pii_label="national_id")
    url = f"/v1/sources/{source}:activate"

    def table(name: str, **extra: Any) -> dict[str, Any]:
        row = {"name": f"credit.{name}", "data_space_id": str(o.space)}
        row["owner_id"] = str(o.users["steward"])
        row.update(extra)
        return row

    async with client(o) as api:
        h = o.headers("admin")
        # A table cannot be moved into another Data Space, even one the caller also administers.
        moved = await api.post(
            url, json={"tables": [table("du_no", data_space_id=str(o.other))]}, headers=h
        )
        assert moved.status_code == 422, moved.text
        foreign = await api.post(
            url, json={"tables": [table("du_no", data_space_id=str(o.foreign_space))]}, headers=h
        )
        assert foreign.status_code == 422, foreign.text
        stranger = await api.post(
            url, json={"tables": [table("du_no", owner_id=str(o.users["foreign"]))]}, headers=h
        )
        assert stranger.status_code == 422, stranger.text
        # UC-04-04 step 3 holds here as on PATCH /v1/datasets/{id}.
        unmasked = await api.post(
            url, json={"tables": [table("du_no"), table("khach_hang")]}, headers=h
        )
        assert unmasked.status_code == 422, unmasked.text
        assert unmasked.json()["code"] == "business_rule_violated"
        assert "so_cccd" in unmasked.json()["detail"]
        assert o.one("SELECT enabled FROM catalog.dataset WHERE id = %s", plain) is False

        done = await api.post(url, json={"tables": [table("du_no")]}, headers=h)
        assert done.status_code == 200, done.text
        assert o.one("SELECT enabled FROM catalog.dataset WHERE id = %s", plain) is True
        assert o.one("SELECT enabled FROM catalog.dataset WHERE id = %s", personal) is False
        assert str(o.one("SELECT data_space_id FROM catalog.dataset WHERE id = %s", plain)) == str(
            o.space
        )
        denied = await api.post(url, json={"tables": []}, headers=o.headers("analyst"))
        assert denied.status_code == 403
