"""Kỳ chốt (UC-10-02 to UC-10-05) against PostgreSQL.

Each test builds its own organisation: two stewards (one presses Close, the other files), an
analyst who only asks, an analyst with view_detail, a controller (AC06, compliance) and a user
without any of those, in one Data Space with a period calendar and certified metrics. The close
and publish workflows are not running: the tests assert the row and the event, not the effect.
"""

from __future__ import annotations

import decimal
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
import psycopg2
import pytest
from psycopg2.extras import Json

from services.api.app import create_app
from services.api.config import Settings

from .conftest import PASSWORD, Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]

REPORT = "BC-NHNN-02"


@dataclass
class Space:
    org: uuid.UUID
    space: uuid.UUID
    other: uuid.UUID
    calendar: uuid.UUID
    users: dict[str, uuid.UUID]
    metric: uuid.UUID
    mv1: uuid.UUID
    mv2: uuid.UUID
    draft_mv: uuid.UUID
    other_mv: uuid.UUID
    seed: Seed

    def headers(self, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer kc-{who}", "X-Data-Space": str(space or self.space)}

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM period.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
            return row[0] if row else None

    def period(
        self,
        label: str,
        status: str = "open",
        *,
        version: int = 1,
        space: uuid.UUID | None = None,
        business_date: str | None = None,
        closed_by: uuid.UUID | None = None,
        org: uuid.UUID | None = None,
    ) -> uuid.UUID:
        frozen = status in ("closed", "filed", "retro_detected")
        values: dict[str, Any] = {
            "org_id": org or self.org,
            "data_space_id": space or self.space,
            "period_label": label,
            "business_date": business_date or f"{label}-28",
            "version": version,
            "status": status,
            "closed_by": closed_by,
        }
        if org is None:
            values["period_calendar_id"] = self.calendar
        if frozen:
            values["snapshot_hash"] = f"sha-{label}-{version}"
            values["closed_at"] = "2026-10-01T08:00:00Z"
        if status in ("filed", "retro_detected"):
            values["filed_report_code"] = REPORT
            values["filed_at"] = "2026-10-10"
        return self.seed.row("period.period_close", **values)

    def published(
        self, period: uuid.UUID, mv: uuid.UUID, value: str, key: dict[str, str]
    ) -> uuid.UUID:
        return self.seed.row(
            "period.published_result",
            org_id=self.org,
            period_close_id=period,
            metric_version_id=mv,
            dimension_key=Json(key),
            value=value,
            report_ref=REPORT,
            sha256="ab" * 32,
        )

    def retro(
        self,
        period: uuid.UUID,
        result: uuid.UUID,
        current: str,
        delta: str,
        *,
        below: bool = False,
        new_period: uuid.UUID | None = None,
    ) -> uuid.UUID:
        return self.seed.row(
            "period.retro_change",
            org_id=self.org,
            period_close_id=period,
            published_result_id=result,
            current_value=current,
            delta=delta,
            below_materiality=below,
            new_period_close_id=new_period,
        )


def _user(seed: Seed, org: uuid.UUID, name: str, role: uuid.UUID, space: uuid.UUID) -> uuid.UUID:
    user = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-{name}",
        username=name,
        display_name=name,
        status="active",
    )
    seed.row("org.user_role", org_id=org, user_id=user, role_id=role, source="manual")
    seed.row(
        "org.data_space_member",
        org_id=org,
        data_space_id=space,
        principal_type="user",
        principal_id=user,
        role_id=role,
    )
    return user


def _metric(seed: Seed, org: uuid.UUID, space: uuid.UUID, code: str, owner: uuid.UUID) -> uuid.UUID:
    return seed.row(
        "context.metric",
        org_id=org,
        data_space_id=space,
        code=code,
        owner_id=owner,
        current_version=1,
        status="active",
    )


def _version(
    seed: Seed, org: uuid.UUID, metric: uuid.UUID, version: int, status: str, owner: uuid.UUID
) -> uuid.UUID:
    certified = status in ("certified", "superseded")
    return seed.row(
        "context.metric_version",
        org_id=org,
        metric_id=metric,
        version=version,
        display_name=f"NPL v{version}",
        expression="sum(no_xau) / sum(du_no)",
        period_semantics="end_of_period",
        status=status,
        certified_by=owner if certified else None,
        certified_at="2026-06-01T00:00:00Z" if certified else None,
        valid_from="2026-06-01T00:00:00Z" if certified else None,
    )


@pytest.fixture
def ps(seed: Seed) -> Space:
    org = seed.org()
    steward_role = seed.row(
        "org.role", org_id=org, code="AC03", name="Steward", capabilities=["ask", "steward"]
    )
    ask_role = seed.row("org.role", org_id=org, code="ASK", name="Ask", capabilities=["ask"])
    detail_role = seed.row(
        "org.role", org_id=org, code="DETAIL", name="Detail", capabilities=["ask", "view_detail"]
    )
    controller_role = seed.row(
        "org.role", org_id=org, code="AC06", name="AC06", capabilities=["ask", "compliance"]
    )
    none_role = seed.row("org.role", org_id=org, code="UP", name="Upload", capabilities=["upload"])
    keeper = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-keeper-{org.hex[:6]}",
        username="keeper",
        display_name="Keeper",
    )
    space = seed.row("org.data_space", org_id=org, code="DS-TD", name="Tín dụng", steward_id=keeper)
    other = seed.row("org.data_space", org_id=org, code="DS-KT", name="Kế toán", steward_id=keeper)
    users = {
        "steward": _user(seed, org, "steward", steward_role, space),
        "closer": _user(seed, org, "closer", steward_role, space),
        "analyst": _user(seed, org, "analyst", ask_role, space),
        "detail": _user(seed, org, "detail", detail_role, space),
        "controller": _user(seed, org, "controller", controller_role, space),
        "nobody": _user(seed, org, "nobody", none_role, space),
    }
    # The steward and the detail analyst also work in the other Data Space: its rows stay out of
    # this one all the same.
    for who, role in (("steward", steward_role), ("detail", detail_role)):
        seed.row(
            "org.data_space_member",
            org_id=org,
            data_space_id=other,
            principal_type="user",
            principal_id=users[who],
            role_id=role,
        )
    calendar = seed.row(
        "period.period_calendar",
        org_id=org,
        data_space_id=space,
        period_kind="month",
        close_rule="last_business_day",
        dataset_ids=f"{{{uuid.uuid4()}}}",
        report_codes=[REPORT],
    )
    metric = _metric(seed, org, space, "NPL_RATIO", users["controller"])
    # One certified version per metric: v1 was replaced by v2.
    mv1 = _version(seed, org, metric, 1, "superseded", users["controller"])
    mv2 = _version(seed, org, metric, 2, "certified", users["controller"])
    draft = _metric(seed, org, space, "NPL_DRAFT", users["controller"])
    draft_mv = _version(seed, org, draft, 1, "draft", users["controller"])
    elsewhere = _metric(seed, org, other, "CHI_PHI", users["controller"])
    other_mv = _version(seed, org, elsewhere, 1, "certified", users["controller"])
    return Space(org, space, other, calendar, users, metric, mv1, mv2, draft_mv, other_mv, seed)


class Tokens:
    def __init__(self, ps: Space) -> None:
        self.ps = ps

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.ps.org)}


def client(ps: Space) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(ps))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


# --- list and read --------------------------------------------------------------------------------


async def test_periods_are_listed_and_read_in_their_data_space(ps: Space) -> None:
    june = ps.period("2026-06", "filed", business_date="2026-06-30")
    june2 = ps.period("2026-06", "closing", version=2, business_date="2026-06-30")
    july = ps.period("2026-07", "closed", business_date="2026-07-31")
    sept = ps.period("2026-09", "open", business_date="2026-09-30")
    hidden = ps.period("2026-09", "open", space=ps.other, business_date="2026-09-30")
    ps.seed.row(
        "period.period_snapshot_file",
        org_id=ps.org,
        period_close_id=june,
        dataset_id=uuid.uuid4(),
        dataset_version=3,
        object_key="period/2026-06/du_no.parquet",
        row_count=50_000,
        sha256="cd" * 32,
    )
    for code, status in (("TONG_DU_NO", "matched"), ("SO_KHOAN_VAY", "matched")):
        ps.seed.row(
            "period.period_reconciliation",
            org_id=ps.org,
            period_close_id=june,
            item_code=code,
            snapshot_value="100",
            ledger_value="100",
            tolerance="0",
            delta="0",
            status=status,
        )
    async with client(ps) as api:
        listed = await api.get("/v1/periods", headers=ps.headers("analyst"))
        assert listed.status_code == 200, listed.text
        ids = [p["id"] for p in listed.json()["items"]]
        assert ids == [str(sept), str(july), str(june2), str(june)]
        assert str(hidden) not in ids

        filtered = await api.get(
            "/v1/periods",
            params={"status": "filed", "from": "2026-06-01", "to": "2026-06-30"},
            headers=ps.headers("analyst"),
        )
        assert [p["id"] for p in filtered.json()["items"]] == [str(june)]

        first = await api.get("/v1/periods?page_size=2", headers=ps.headers("analyst"))
        token = first.json()["next_page_token"]
        assert token
        rest = await api.get(
            "/v1/periods",
            params={"page_size": 2, "page_token": token},
            headers=ps.headers("analyst"),
        )
        assert [p["id"] for p in first.json()["items"] + rest.json()["items"]] == ids

        detail = await api.get(f"/v1/periods/{june}", headers=ps.headers("analyst"))
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["status"] == "filed" and body["snapshot_hash"] == "sha-2026-06-1"
        assert [(v["version"], v["status"]) for v in body["versions"]] == [
            (1, "filed"),
            (2, "closing"),
        ]
        assert body["snapshot_files"][0]["row_count"] == 50_000
        assert "object_key" not in body["snapshot_files"][0]
        assert body["reconciliation"] == {"matched": 2}
        assert "org_id" not in body and "created_by" not in body

        other_space = await api.get(f"/v1/periods/{hidden}", headers=ps.headers("steward"))
        assert other_space.status_code == 404
        missing = await api.get(f"/v1/periods/{uuid.uuid4()}", headers=ps.headers("analyst"))
        assert missing.status_code == 404
        no_ask = await api.get("/v1/periods", headers=ps.headers("nobody"))
        assert no_ask.status_code == 403


async def test_another_organisation_s_period_does_not_exist(ps: Space) -> None:
    stranger = ps.seed.org()
    keeper = ps.seed.row(
        "org.app_user", org_id=stranger, external_id="kc-x", username="x", display_name="X"
    )
    their_space = ps.seed.row(
        "org.data_space", org_id=stranger, code="DS-X", name="X", steward_id=keeper
    )
    theirs = ps.period("2026-09", "closed", space=their_space, org=stranger)
    async with client(ps) as api:
        for method, path in (
            ("GET", f"/v1/periods/{theirs}"),
            ("POST", f"/v1/periods/{theirs}:close"),
            ("GET", f"/v1/periods/{theirs}/reconciliation"),
            ("GET", f"/v1/periods/{theirs}/retro-changes"),
        ):
            got = await api.request(method, path, headers=ps.headers("steward"))
            assert got.status_code == 404, (path, got.text)
        filed = await api.post(
            f"/v1/periods/{theirs}:publish",
            json={"report_code": REPORT, "filed_at": "2026-10-10"},
            headers=ps.headers("steward"),
        )
        assert filed.status_code == 404
    assert ps.one("SELECT filed_report_code FROM period.period_close WHERE id = %s", theirs) is None


# --- close ---------------------------------------------------------------------------------------


async def test_close_moves_the_period_to_closing_and_asks_the_workflow(ps: Space) -> None:
    sept = ps.period("2026-09", "open")
    async with client(ps) as api:
        no_steward = await api.post(f"/v1/periods/{sept}:close", headers=ps.headers("analyst"))
        assert no_steward.status_code == 403
        closed = await api.post(f"/v1/periods/{sept}:close", headers=ps.headers("closer"))
        assert closed.status_code == 202, closed.text
        assert closed.headers["Location"] == f"/v1/periods/{sept}"
        assert closed.json()["status"] == "closing" and closed.json()["id"] == str(sept)
        assert ps.one("SELECT status FROM period.period_close WHERE id = %s", sept) == "closing"
        assert (
            ps.one("SELECT closed_by FROM period.period_close WHERE id = %s", sept)
            == (ps.users["closer"])
        )
        [(event, payload)] = ps.events(sept)
        assert event == "period.close_requested"
        assert payload["period_close_id"] == str(sept)
        assert payload["previous_status"] == "open"
        assert payload["closed_by"] == str(ps.users["closer"])
        assert payload["business_date"] == "2026-09-28"

        again = await api.post(f"/v1/periods/{sept}:close", headers=ps.headers("closer"))
        assert again.status_code == 409
        assert again.json()["code"] == "invalid_state"
        other_space = await api.post(
            f"/v1/periods/{sept}:close", headers=ps.headers("steward", ps.other)
        )
        assert other_space.status_code == 404


# AC-10-02-02
@pytest.mark.req("AC-10-02-02")
async def test_one_close_at_a_time_per_data_space(ps: Space) -> None:
    ps.period("2026-08", "closing")
    sept = ps.period("2026-09", "open")
    elsewhere = ps.period("2026-09", "open", space=ps.other)
    async with client(ps) as api:
        refused = await api.post(f"/v1/periods/{sept}:close", headers=ps.headers("steward"))
        assert refused.status_code == 409, refused.text
        assert "2026-08" in refused.json()["detail"]
        # Nothing was asked of the workflow, so no file is written.
        assert ps.events(sept) == []
        assert ps.one("SELECT status FROM period.period_close WHERE id = %s", sept) == "open"
        # Another Data Space closes on its own.
        theirs = await api.post(
            f"/v1/periods/{elsewhere}:close", headers=ps.headers("steward", ps.other)
        )
        assert theirs.status_code == 202, theirs.text


async def test_a_closed_period_is_locked(ps: Space) -> None:
    async with client(ps) as api:
        for label, status in (
            ("2026-06", "closed"),
            ("2026-07", "filed"),
            ("2026-08", "retro_detected"),
        ):
            locked = ps.period(label, status)
            got = await api.post(f"/v1/periods/{locked}:close", headers=ps.headers("steward"))
            assert got.status_code == 423, (status, got.text)
            assert got.json()["code"] == "period_locked"
            assert ps.events(locked) == []


# AC-10-02-03
@pytest.mark.req("AC-10-02-03")
async def test_a_failed_close_is_closed_again_unless_an_exception_waits(ps: Space) -> None:
    failed = ps.period("2026-09", "close_failed", closed_by=ps.users["closer"])
    item = ps.seed.row(
        "period.period_reconciliation",
        org_id=ps.org,
        period_close_id=failed,
        item_code="SO_KHACH_HANG",
        snapshot_value="1000",
        ledger_value="1003",
        tolerance="1",
        delta="-3",
        status="exception_pending",
        hitl_request_id=uuid.uuid4(),
    )
    async with client(ps) as api:
        waits = await api.post(f"/v1/periods/{failed}:close", headers=ps.headers("steward"))
        assert waits.status_code == 409, waits.text
        ps.seed.conn.cursor().execute(
            "UPDATE period.period_reconciliation SET status = 'mismatched' WHERE id = %s",
            (str(item),),
        )
        again = await api.post(f"/v1/periods/{failed}:close", headers=ps.headers("steward"))
        assert again.status_code == 202, again.text
        assert dict(ps.events(failed))["period.close_requested"]["previous_status"] == (
            "close_failed"
        )


# --- reconciliation and exceptions ---------------------------------------------------------------


def _item(ps: Space, period: uuid.UUID, code: str, status: str = "mismatched") -> uuid.UUID:
    return ps.seed.row(
        "period.period_reconciliation",
        org_id=ps.org,
        period_close_id=period,
        item_code=code,
        snapshot_value="125000.50",
        ledger_value="125010.00" if status != "matched" else "125000.50",
        tolerance="1",
        delta="-9.50" if status != "matched" else "0",
        status=status,
        cause_rows_ref="s3://recon/rows.parquet",
    )


async def test_reconciliation_lists_each_item(ps: Space) -> None:
    failed = ps.period("2026-09", "close_failed")
    for code in ("A_TONG_DU_NO", "B_SO_KHOAN_VAY", "C_SO_KHACH_HANG"):
        _item(ps, failed, code, "matched" if code != "C_SO_KHACH_HANG" else "mismatched")
    elsewhere = ps.period("2026-09", "close_failed", space=ps.other)
    async with client(ps) as api:
        first = await api.get(
            f"/v1/periods/{failed}/reconciliation?page_size=2", headers=ps.headers("analyst")
        )
        assert first.status_code == 200, first.text
        assert [i["item_code"] for i in first.json()["items"]] == ["A_TONG_DU_NO", "B_SO_KHOAN_VAY"]
        rest = await api.get(
            f"/v1/periods/{failed}/reconciliation",
            params={"page_token": first.json()["next_page_token"]},
            headers=ps.headers("analyst"),
        )
        [last] = rest.json()["items"]
        assert last["item_code"] == "C_SO_KHACH_HANG" and last["status"] == "mismatched"
        # Amounts are decimals, sent as strings (A1.1).
        assert last["snapshot_value"] == "125000.50" and last["delta"] == "-9.50"
        assert "cause_rows_ref" not in last
        hidden = await api.get(
            f"/v1/periods/{elsewhere}/reconciliation", headers=ps.headers("steward")
        )
        assert hidden.status_code == 404
        no_ask = await api.get(f"/v1/periods/{failed}/reconciliation", headers=ps.headers("nobody"))
        assert no_ask.status_code == 403


# AC-10-02-04
# AC-10-02-08
@pytest.mark.req("AC-10-02-04", "AC-10-02-08")
async def test_an_exception_is_a_high_risk_request_for_two_other_people(ps: Space) -> None:
    failed = ps.period("2026-09", "close_failed", closed_by=ps.users["closer"])
    item = _item(ps, failed, "SO_KHACH_HANG")
    _item(ps, failed, "TONG_DU_NO", "matched")
    url = f"/v1/periods/{failed}/exceptions"
    body = {"item_code": "SO_KHACH_HANG", "reason": "  Sổ cái ghi nhận trễ 3 khách hàng  "}
    async with client(ps) as api:
        no_steward = await api.post(url, json=body, headers=ps.headers("analyst"))
        assert no_steward.status_code == 403
        asked = await api.post(url, json=body, headers=ps.headers("steward"))
        assert asked.status_code == 202, asked.text
        out = asked.json()
        assert asked.headers["Location"] == f"/v1/hitl-requests/{out['id']}"
        assert out["risk"] == "high" and out["kind"] == "period_exception"
        assert out["status"] == "open" and out["source_ref"] == str(item)
        assert [s["approver_role"] for s in out["approval_steps"]] == ["steward", "compliance"]
        # BR-132: whoever pressed Close and the requester never approve it.
        assert set(out["excluded_approver_ids"]) == {
            str(ps.users["closer"]),
            str(ps.users["steward"]),
        }
        assert (
            ps.one("SELECT status FROM period.period_reconciliation WHERE id = %s", item)
            == "exception_pending"
        )
        assert (
            str(
                ps.one(
                    "SELECT hitl_request_id FROM period.period_reconciliation WHERE id = %s", item
                )
            )
            == out["id"]
        )
        [(event, payload)] = ps.events(item)
        assert event == "period.exception_requested"
        assert payload["hitl_request_id"] == out["id"]
        assert payload["reason"] == "Sổ cái ghi nhận trễ 3 khách hàng"
        assert payload["delta"] == "-9.50" and payload["risk"] == "high"
        assert len(payload["approval_steps"]) == 2
        assert set(payload["excluded_approver_ids"]) == set(out["excluded_approver_ids"])

        twice = await api.post(url, json=body, headers=ps.headers("steward"))
        assert twice.status_code == 409
        matched = await api.post(
            url, json={**body, "item_code": "TONG_DU_NO"}, headers=ps.headers("steward")
        )
        assert matched.status_code == 409
        unknown = await api.post(
            url, json={**body, "item_code": "KHONG_CO"}, headers=ps.headers("steward")
        )
        assert unknown.status_code == 404
        blank = await api.post(url, json={**body, "reason": ""}, headers=ps.headers("steward"))
        assert blank.status_code == 422


async def test_exceptions_follow_the_period_state(ps: Space) -> None:
    opened = ps.period("2026-09", "open")
    closed = ps.period("2026-08", "closed")
    elsewhere = ps.period("2026-09", "close_failed", space=ps.other)
    for period in (opened, closed, elsewhere):
        _item(ps, period, "SO_KHACH_HANG")
    body = {"item_code": "SO_KHACH_HANG", "reason": "lệch"}
    async with client(ps) as api:
        not_failed = await api.post(
            f"/v1/periods/{opened}/exceptions", json=body, headers=ps.headers("steward")
        )
        assert not_failed.status_code == 409
        locked = await api.post(
            f"/v1/periods/{closed}/exceptions", json=body, headers=ps.headers("steward")
        )
        assert locked.status_code == 423
        hidden = await api.post(
            f"/v1/periods/{elsewhere}/exceptions", json=body, headers=ps.headers("steward")
        )
        assert hidden.status_code == 404


# --- publish -------------------------------------------------------------------------------------


# AC-10-03-04
@pytest.mark.req("AC-10-03-04")
async def test_only_a_closed_period_is_filed(ps: Space) -> None:
    body = {"report_code": REPORT, "filed_at": "2026-10-10"}
    async with client(ps) as api:
        for label, status in (
            ("2026-06", "open"),
            ("2026-07", "closing"),
            ("2026-08", "close_failed"),
            ("2026-09", "filed"),
        ):
            period = ps.period(label, status)
            refused = await api.post(
                f"/v1/periods/{period}:publish", json=body, headers=ps.headers("steward")
            )
            assert refused.status_code == 409, (status, refused.text)
            assert ps.events(period) == []


async def test_publish_records_the_filing_and_asks_the_workflow(ps: Space) -> None:
    sept = ps.period("2026-09", "closed")
    url = f"/v1/periods/{sept}:publish"
    body = {"report_code": REPORT, "filed_at": "2026-10-10"}
    async with client(ps) as api:
        no_steward = await api.post(url, json=body, headers=ps.headers("analyst"))
        assert no_steward.status_code == 403
        wrong_report = await api.post(
            url, json={**body, "report_code": "BC-KHAC"}, headers=ps.headers("steward")
        )
        assert wrong_report.status_code == 422
        assert wrong_report.json()["code"] == "validation_failed"
        unknown_field = await api.post(
            url,
            json={**body, "approved_by": str(ps.users["steward"])},
            headers=ps.headers("steward"),
        )
        assert unknown_field.status_code == 422

        filed = await api.post(url, json=body, headers=ps.headers("steward"))
        assert filed.status_code == 200, filed.text
        assert filed.json()["filed_report_code"] == REPORT
        assert filed.json()["filed_at"] == "2026-10-10"
        # The workflow freezes the numbers and moves the period to filed.
        assert filed.json()["status"] == "closed"
        [(event, payload)] = ps.events(sept)
        assert event == "period.publish_requested"
        assert payload["report_code"] == REPORT and payload["overrides"] == []
        assert payload["requested_by"] == str(ps.users["steward"])

        twice = await api.post(url, json=body, headers=ps.headers("steward"))
        assert twice.status_code == 409


# AC-10-03-03
@pytest.mark.req("AC-10-03-03")
async def test_an_uncertified_metric_is_not_frozen(ps: Space) -> None:
    sept = ps.period("2026-09", "closed")
    override = {
        "metric_version_id": str(ps.draft_mv),
        "reported_value": "2.31",
        "override_reason": "số đã nộp",
        "approver_id": str(ps.users["controller"]),
    }
    async with client(ps) as api:
        # A draft, a superseded definition and a metric of another Data Space.
        for mv in (ps.draft_mv, ps.mv1, ps.other_mv):
            refused = await api.post(
                f"/v1/periods/{sept}:publish",
                json={
                    "report_code": REPORT,
                    "filed_at": "2026-10-10",
                    "overrides": [{**override, "metric_version_id": str(mv)}],
                },
                headers=ps.headers("steward"),
            )
            assert refused.status_code == 422, refused.text
            assert refused.json()["code"] == "business_rule_violated"
            # It names the metric version it refuses.
            assert str(mv) in refused.json()["detail"]
    assert ps.events(sept) == []
    assert ps.one("SELECT filed_report_code FROM period.period_close WHERE id = %s", sept) is None
    assert (
        ps.one("SELECT count(*) FROM period.published_result WHERE period_close_id = %s", sept) == 0
    )


# AC-10-03-02
# AC-10-03-06
@pytest.mark.req("AC-10-03-02", "AC-10-03-06")
async def test_a_manual_number_is_approved_by_the_controller_the_filer_names(ps: Space) -> None:
    sept = ps.period("2026-09", "closed")
    url = f"/v1/periods/{sept}:publish"
    override = {
        "metric_version_id": str(ps.mv2),
        "reported_value": "2.31",
        "override_reason": "Báo cáo đã nộp ghi 2,31%",
        "approver_id": str(ps.users["controller"]),
    }

    def body(**changes: Any) -> dict[str, Any]:
        return {
            "report_code": REPORT,
            "filed_at": "2026-10-10",
            "overrides": [{**override, **changes}],
        }

    async with client(ps) as api:
        own = await api.post(
            url, json=body(approver_id=str(ps.users["steward"])), headers=ps.headers("steward")
        )
        assert own.status_code == 422, own.text
        assert own.json()["code"] == "business_rule_violated"
        # Another steward is not a controller.
        not_controller = await api.post(
            url, json=body(approver_id=str(ps.users["closer"])), headers=ps.headers("steward")
        )
        assert not_controller.status_code == 422
        nobody = await api.post(
            url, json=body(approver_id=str(uuid.uuid4())), headers=ps.headers("steward")
        )
        assert nobody.status_code == 422
        no_reason = await api.post(
            url, json=body(override_reason="   "), headers=ps.headers("steward")
        )
        assert no_reason.status_code == 422
        twice = await api.post(
            url,
            json={**body(), "overrides": [override, {**override, "reported_value": "2.30"}]},
            headers=ps.headers("steward"),
        )
        assert twice.status_code == 422
        assert ps.events(sept) == []

        filed = await api.post(url, json=body(), headers=ps.headers("steward"))
        assert filed.status_code == 200, filed.text
        events = dict(ps.events(sept))
        assert set(events) == {"period.publish_requested", "period.override_approval_requested"}
        requested = events["period.publish_requested"]
        approval = events["period.override_approval_requested"]
        [frozen] = requested["overrides"]
        assert frozen["reported_value"] == "2.31"
        assert frozen["metric_version_id"] == str(ps.mv2)
        assert frozen["approval_id"] == approval["hitl_request_id"]
        # The filer states no approval: the controller decides in Cần bạn.
        assert "approved_by" not in frozen
        assert approval["assignee_id"] == str(ps.users["controller"])
        assert approval["excluded_approver_ids"] == [str(ps.users["steward"])]
        assert approval["hitl_kind"] == "period_exception"


# AC-10-03-05
@pytest.mark.req("AC-10-03-05")
def test_published_results_are_insert_only_for_svc_period(ps: Space, api_database: str) -> None:
    filed = ps.period("2026-06", "filed")
    result = ps.published(filed, ps.mv1, "2.29", {"vung": "Bac"})
    parts = urlsplit(api_database)
    dsn = urlunsplit(
        (parts.scheme, f"svc_period:{PASSWORD}@{parts.hostname}:{parts.port}", parts.path, "", "")
    )
    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("SELECT set_config('app.org_id', %s, false)", (str(ps.org),))
        for sql in (
            "UPDATE period.published_result SET value = 2.31 WHERE id = %s",
            "DELETE FROM period.published_result WHERE id = %s",
        ):
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute(sql, (str(result),))
            conn.rollback()
            cur.execute("SELECT set_config('app.org_id', %s, false)", (str(ps.org),))
        cur.execute(
            "INSERT INTO period.published_result (id, org_id, period_close_id, metric_version_id,"
            " dimension_key, value, report_ref, sha256) VALUES (%s, %s, %s, %s, '{}', 2.29, %s, %s)",
            (str(uuid.uuid4()), str(ps.org), str(filed), str(ps.mv2), REPORT, "ef" * 32),
        )
        conn.commit()
    finally:
        conn.close()
    assert ps.one("SELECT value FROM period.published_result WHERE id = %s", result) == (
        decimal.Decimal("2.29")
    )


# --- retroactive changes -------------------------------------------------------------------------


async def test_retro_changes_are_listed_per_period(ps: Space) -> None:
    june = ps.period("2026-06", "retro_detected")
    north = ps.published(june, ps.mv1, "2.29", {"vung": "Bac"})
    south = ps.published(june, ps.mv1, "1.80", {"vung": "Nam"})
    first = ps.retro(june, north, "2.41", "0.12")
    second = ps.retro(june, south, "1.81", "0.01", below=True)
    elsewhere = ps.period("2026-06", "filed", space=ps.other)
    async with client(ps) as api:
        listed = await api.get(
            f"/v1/periods/{june}/retro-changes?page_size=1", headers=ps.headers("analyst")
        )
        assert listed.status_code == 200, listed.text
        rest = await api.get(
            f"/v1/periods/{june}/retro-changes",
            params={"page_token": listed.json()["next_page_token"]},
            headers=ps.headers("analyst"),
        )
        items = listed.json()["items"] + rest.json()["items"]
        assert {i["id"] for i in items} == {str(first), str(second)}
        by_id = {i["id"]: i for i in items}
        assert by_id[str(first)]["current_value"] == "2.41"
        assert by_id[str(second)]["below_materiality"] is True
        assert by_id[str(first)]["decision"] is None
        hidden = await api.get(
            f"/v1/periods/{elsewhere}/retro-changes", headers=ps.headers("steward")
        )
        assert hidden.status_code == 404
        no_ask = await api.get(f"/v1/periods/{june}/retro-changes", headers=ps.headers("nobody"))
        assert no_ask.status_code == 403


# AC-10-04-02
# AC-10-04-06
@pytest.mark.req("AC-10-04-02", "AC-10-04-06")
async def test_no_action_keeps_the_reason_and_still_tells_the_controller(ps: Space) -> None:
    june = ps.period("2026-06", "retro_detected")
    material = ps.retro(june, ps.published(june, ps.mv1, "2.29", {}), "2.41", "0.12")
    minor = ps.retro(
        june, ps.published(june, ps.mv1, "1.80", {"vung": "Nam"}), "1.81", "0.01", below=True
    )
    async with client(ps) as api:
        no_steward = await api.post(
            f"/v1/retro-changes/{material}:decide",
            json={"decision": "no_action", "decision_note": "x"},
            headers=ps.headers("analyst"),
        )
        assert no_steward.status_code == 403
        blank = await api.post(
            f"/v1/retro-changes/{material}:decide",
            json={"decision": "no_action", "decision_note": "   "},
            headers=ps.headers("steward"),
        )
        assert blank.status_code == 422
        bogus = await api.post(
            f"/v1/retro-changes/{material}:decide",
            json={"decision": "ignore", "decision_note": "x"},
            headers=ps.headers("steward"),
        )
        assert bogus.status_code == 422

        decided = await api.post(
            f"/v1/retro-changes/{material}:decide",
            json={
                "decision": "no_action",
                "decision_note": "Chênh do phân loại lại, không nộp lại",
            },
            headers=ps.headers("steward"),
        )
        assert decided.status_code == 200, decided.text
        out = decided.json()
        assert out["decision"] == "no_action" and out["new_period_close_id"] is None
        assert out["decision_note"] == "Chênh do phân loại lại, không nộp lại"
        # BR-134: a material change reaches the controller whatever the decision.
        assert out["compliance_notified"] is True
        [(event, payload)] = ps.events(material)
        assert event == "retro_change.decided"
        assert payload["notify_compliance"] is True and payload["decision"] == "no_action"
        assert payload["decided_by"] == str(ps.users["steward"])

        again = await api.post(
            f"/v1/retro-changes/{material}:decide",
            json={"decision": "new_version", "decision_note": "đổi ý"},
            headers=ps.headers("steward"),
        )
        assert again.status_code == 409

        small = await api.post(
            f"/v1/retro-changes/{minor}:decide",
            json={"decision": "no_action", "decision_note": "dưới ngưỡng"},
            headers=ps.headers("steward"),
        )
        assert small.json()["compliance_notified"] is False
        assert dict(ps.events(minor))["retro_change.decided"]["notify_compliance"] is False
    # No new version and no change to the period.
    assert ps.one("SELECT count(*) FROM period.period_close WHERE org_id = %s", ps.org) == 1


# AC-10-04-03
@pytest.mark.req("AC-10-04-03")
async def test_new_version_leaves_version_one_untouched(ps: Space) -> None:
    june = ps.period("2026-06", "retro_detected")
    north = ps.published(june, ps.mv1, "2.29", {"vung": "Bac"})
    south = ps.published(june, ps.mv1, "1.80", {"vung": "Nam"})
    first = ps.retro(june, north, "2.41", "0.12")
    second = ps.retro(june, south, "1.95", "0.15")
    async with client(ps) as api:
        decided = await api.post(
            f"/v1/retro-changes/{first}:decide",
            json={"decision": "new_version", "decision_note": "12 khoản vay chuyển nhóm nợ"},
            headers=ps.headers("steward"),
        )
        assert decided.status_code == 200, decided.text
        version2 = decided.json()["new_period_close_id"]
        assert version2
        row = ps.one(
            "SELECT row(version, status, period_label, business_date::text, closed_by::text)"
            " FROM period.period_close WHERE id = %s",
            version2,
        )
        assert row == f"(2,closing,2026-06,2026-06-28,{ps.users['steward']})"
        events = [e for e, _ in ps.events(first)]
        assert events == ["retro_change.decided"]
        [(event, payload)] = ps.events(version2)
        assert event == "period.close_requested"
        assert payload["retro_of_period_close_id"] == str(june) and payload["version"] == 2

        # Version 1 keeps its hash, status and frozen numbers (BR-43).
        assert ps.one("SELECT snapshot_hash FROM period.period_close WHERE id = %s", june) == (
            "sha-2026-06-1"
        )
        assert ps.one("SELECT status FROM period.period_close WHERE id = %s", june) == (
            "retro_detected"
        )
        assert (
            ps.one("SELECT count(*) FROM period.published_result WHERE period_close_id = %s", june)
            == 2
        )

        # The period's other change joins the same version 2.
        joined = await api.post(
            f"/v1/retro-changes/{second}:decide",
            json={"decision": "new_version", "decision_note": "cùng đợt"},
            headers=ps.headers("steward"),
        )
        assert joined.status_code == 200, joined.text
        assert joined.json()["new_period_close_id"] == version2
        assert (
            ps.one(
                "SELECT count(*) FROM period.period_close WHERE period_label = '2026-06'"
                " AND org_id = %s",
                ps.org,
            )
            == 2
        )


async def test_new_version_waits_for_a_running_close(ps: Space) -> None:
    june = ps.period("2026-06", "retro_detected")
    ps.period("2026-09", "closing")
    change = ps.retro(june, ps.published(june, ps.mv1, "2.29", {}), "2.41", "0.12")
    async with client(ps) as api:
        busy = await api.post(
            f"/v1/retro-changes/{change}:decide",
            json={"decision": "new_version", "decision_note": "bản 2"},
            headers=ps.headers("steward"),
        )
        assert busy.status_code == 409, busy.text
    assert ps.one("SELECT decision FROM period.retro_change WHERE id = %s", change) is None


async def test_a_retro_change_of_another_data_space_is_not_found(ps: Space) -> None:
    elsewhere = ps.period("2026-06", "filed", space=ps.other)
    change = ps.retro(elsewhere, ps.published(elsewhere, ps.other_mv, "5", {}), "6", "1")
    async with client(ps) as api:
        hidden = await api.post(
            f"/v1/retro-changes/{change}:decide",
            json={"decision": "no_action", "decision_note": "x"},
            headers=ps.headers("steward"),
        )
        assert hidden.status_code == 404
        missing = await api.post(
            f"/v1/retro-changes/{uuid.uuid4()}:decide",
            json={"decision": "no_action", "decision_note": "x"},
            headers=ps.headers("steward"),
        )
        assert missing.status_code == 404
    assert ps.one("SELECT decision FROM period.retro_change WHERE id = %s", change) is None


# --- compare -------------------------------------------------------------------------------------


@pytest.fixture
def versions(ps: Space) -> dict[str, uuid.UUID]:
    """June filed as version 1, its retro version 2, and September filed under definition v2."""
    june = ps.period("2026-06", "retro_detected")
    north = ps.published(june, ps.mv1, "2.29", {"vung": "Bac"})
    ps.published(june, ps.mv1, "1.80", {"vung": "Nam"})
    june2 = ps.period("2026-06", "closing", version=2)
    ps.retro(june, north, "2.41", "0.12", new_period=june2)
    sept = ps.period("2026-09", "filed")
    ps.published(sept, ps.mv2, "2.29", {"vung": "Bac"})
    ps.published(sept, ps.mv2, "1.70", {"vung": "Nam"})
    ps.published(sept, ps.mv2, "3.00", {"vung": "Trung"})
    return {"june": june, "june2": june2, "sept": sept}


# AC-10-05-01
@pytest.mark.req("AC-10-05-01")
async def test_compare_two_versions_names_the_cause(
    ps: Space, versions: dict[str, uuid.UUID]
) -> None:
    async with client(ps) as api:
        got = await api.get(
            "/v1/periods/compare",
            params={"a": str(versions["june"]), "b": str(versions["june2"])},
            headers=ps.headers("analyst"),
        )
        assert got.status_code == 200, got.text
        out = got.json()
        assert out["a"]["values_from"] == "published" and out["b"]["values_from"] == "retro"
        assert out["rows_id"] == str(versions["june2"])
        [item] = out["items"]
        assert item["cause"] == "retro" and item["metric_code"] == "NPL_RATIO"
        assert item["dimension_key"] == {"vung": "Bac"}
        assert (item["a_value"], item["b_value"], item["delta"]) == ("2.29", "2.41", "0.12")

        # AC-10-05-04: another definition is told apart from data.
        defs = await api.get(
            "/v1/periods/compare",
            params={"a": str(versions["june"]), "b": str(versions["sept"]), "metric": "NPL_RATIO"},
            headers=ps.headers("analyst"),
        )
        causes = {
            tuple(sorted(i["dimension_key"].items())): i["cause"] for i in defs.json()["items"]
        }
        assert causes == {
            (("vung", "Bac"),): "definition",
            (("vung", "Nam"),): "definition",
            (("vung", "Trung"),): "added",
        }
        assert defs.json()["rows_id"] is None
        none = await api.get(
            "/v1/periods/compare",
            params={"a": str(versions["june"]), "b": str(versions["sept"]), "metric": "KHAC"},
            headers=ps.headers("analyst"),
        )
        assert none.json()["items"] == []
    # Comparing writes nothing.
    assert ps.one("SELECT count(*) FROM period.outbox WHERE org_id = %s", ps.org) == 0


async def test_compare_refuses_what_it_cannot_compare(
    ps: Space, versions: dict[str, uuid.UUID]
) -> None:
    opened = ps.period("2026-10", "open")
    elsewhere = ps.period("2026-06", "filed", space=ps.other)
    async with client(ps) as api:
        same = await api.get(
            "/v1/periods/compare",
            params={"a": str(versions["june"]), "b": str(versions["june"])},
            headers=ps.headers("analyst"),
        )
        assert same.status_code == 422
        nothing = await api.get(
            "/v1/periods/compare",
            params={"a": str(versions["june"]), "b": str(opened)},
            headers=ps.headers("analyst"),
        )
        assert nothing.status_code == 409
        hidden = await api.get(
            "/v1/periods/compare",
            params={"a": str(versions["june"]), "b": str(elsewhere)},
            headers=ps.headers("steward"),
        )
        assert hidden.status_code == 404
        no_ask = await api.get(
            "/v1/periods/compare",
            params={"a": str(versions["june"]), "b": str(versions["june2"])},
            headers=ps.headers("nobody"),
        )
        assert no_ask.status_code == 403


# AC-10-05-02
@pytest.mark.req("AC-10-05-02")
async def test_comparison_rows_need_view_detail(ps: Space, versions: dict[str, uuid.UUID]) -> None:
    url = f"/v1/periods/compare/{versions['june2']}/rows"
    async with client(ps) as api:
        ask_only = await api.get(url, headers=ps.headers("analyst"))
        assert ask_only.status_code == 403
        rows = await api.get(url, headers=ps.headers("detail"))
        assert rows.status_code == 200, rows.text
        [row] = rows.json()["items"]
        assert row["period_close_id"] == str(versions["june"])
        assert row["dimension_key"] == {"vung": "Bac"}
        assert (row["published_value"], row["current_value"], row["delta"]) == (
            "2.29",
            "2.41",
            "0.12",
        )
        hidden = await api.get(url, headers=ps.headers("detail", ps.other))
        assert hidden.status_code == 404
        unknown = await api.get(
            f"/v1/periods/compare/{uuid.uuid4()}/rows", headers=ps.headers("detail")
        )
        assert unknown.status_code == 404
