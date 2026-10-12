"""One policy read back and switched off (UC-11-02, UC-07-08): GET /v1/policies/{id} and
POST /v1/policies/{id}:disable against PostgreSQL, and the Data Space scope every policy route
shares.

Each test builds its own organisation: an admin who is a member of two Data Spaces, a steward and
an asker of the first, and a member holding neither `ask` nor `admin`. Policies are seeded: access
policies in each Data Space, an organisation-wide behaviour policy, one of the first Data Space and
an expired one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from psycopg2.extras import Json

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]


@dataclass
class Gov:
    org: uuid.UUID
    space: uuid.UUID
    other: uuid.UUID
    users: dict[str, uuid.UUID]
    rows: dict[str, uuid.UUID]
    seed: Seed

    def headers(self, who: str, space: uuid.UUID | None | bool = True) -> dict[str, str]:
        out = {"Authorization": f"Bearer kc-{who}"}
        if space is not False and space is not None:
            out["X-Data-Space"] = str(self.space if space is True else space)
        return out

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM governance.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def status(self, table: str, row_id: uuid.UUID) -> str:
        with self.seed.conn.cursor() as cur:
            cur.execute(f"SELECT status FROM governance.{table} WHERE id = %s", (str(row_id),))
            row = cur.fetchone()
        return str(row[0])


@pytest.fixture
def gov(seed: Seed) -> Gov:
    org = seed.org()
    roles = {
        "admin": seed.row(
            "org.role", org_id=org, code="ADMIN", name="Admin", capabilities=["ask", "admin"]
        ),
        "steward": seed.row(
            "org.role", org_id=org, code="STEWARD", name="Steward", capabilities=["ask", "steward"]
        ),
        "ask": seed.row("org.role", org_id=org, code="ASK", name="Ask", capabilities=["ask"]),
        "none": seed.row(
            "org.role", org_id=org, code="VIEW", name="View", capabilities=["view_detail"]
        ),
    }
    users = {}
    for name in ("admin", "steward", "ask", "none"):
        users[name] = seed.row(
            "org.app_user",
            org_id=org,
            external_id=f"kc-{name}",
            username=name,
            display_name=name,
            status="active",
        )
        seed.row(
            "org.user_role", org_id=org, user_id=users[name], role_id=roles[name], source="manual"
        )
    space = seed.row(
        "org.data_space", org_id=org, code="DS-POL", name="Pol", steward_id=users["steward"]
    )
    other = seed.row(
        "org.data_space", org_id=org, code="DS-POL-B", name="Pol B", steward_id=users["steward"]
    )
    for name, spaces in (
        ("admin", (space, other)),
        ("steward", (space,)),
        ("ask", (space,)),
        ("none", (space,)),
    ):
        for s in spaces:
            seed.row(
                "org.data_space_member",
                org_id=org,
                data_space_id=s,
                principal_type="user",
                principal_id=users[name],
                role_id=roles[name],
            )
    dataset = seed.row(
        "catalog.dataset",
        org_id=org,
        data_space_id=space,
        origin="derived",
        schema_name="silver",
        table_name="khach_hang",
        enabled=True,
    )
    other_dataset = seed.row(
        "catalog.dataset",
        org_id=org,
        data_space_id=other,
        origin="derived",
        schema_name="silver",
        table_name="khach_hang_b",
        enabled=True,
    )

    def access(space_id: uuid.UUID, table: uuid.UUID, status: str) -> uuid.UUID:
        return seed.row(
            "governance.access_policy",
            org_id=org,
            data_space_id=space_id,
            dataset_id=table,
            name="Chi nhánh chỉ thấy khách hàng của mình",
            kind="row",
            condition_expr="branch_code = :user.branch_code",
            status=status,
            simulated_at="2026-10-01T00:00:00Z",
        )

    def behavior(status: str, space_id: uuid.UUID | None, **extra: Any) -> uuid.UUID:
        return seed.row(
            "governance.behavior_policy",
            org_id=org,
            scope="data_space" if space_id else "org",
            data_space_id=space_id,
            kind="auto_handle",
            description="Lệch dưới 0,05% ngày chốt kỳ thì dùng nguồn A",
            condition=Json({"lech_duoi": 0.0005}),
            action=Json({"dung_nguon": "A"}),
            status=status,
            **extra,
        )

    rows = {
        "access": access(space, dataset, "active"),
        "access_draft": access(space, dataset, "draft"),
        "access_other": access(other, other_dataset, "active"),
        "org_wide": behavior("active", None),
        "behavior": behavior("draft", space),
        "expired": behavior("expired", space, review_at="2026-07-01"),
    }
    for created_at, matched in (("2026-10-01T00:00:00Z", 3), ("2026-10-05T00:00:00Z", 7)):
        seed.row(
            "governance.policy_backtest",
            org_id=org,
            policy_kind="access",
            policy_id=rows["access"],
            window_from="2026-09-01T00:00:00Z",
            window_to="2026-09-08T00:00:00Z",
            matched_count=matched,
            detail_ref="s3://backtests/one.json",
            created_at=created_at,
        )
    return Gov(org, space, other, users, rows, seed)


class Tokens:
    def __init__(self, gov: Gov) -> None:
        self.gov = gov

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.gov.org)}


def client(gov: Gov) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(gov))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


REASON = {"reason": "Quy tắc sai, áp nhầm cho cả chi nhánh"}


# ---------------------------------------------------------------- read one


async def test_a_policy_is_read_with_its_newest_backtest(gov: Gov) -> None:
    async with client(gov) as api:
        got = await api.get(f"/v1/policies/{gov.rows['access']}", headers=gov.headers("steward"))
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["type"] == "access" and body["condition_expr"].startswith("branch_code")
        backtest = body["latest_backtest"]
        assert backtest["matched_count"] == 7 and "detail_ref" not in backtest
        rule = await api.get(f"/v1/policies/{gov.rows['behavior']}", headers=gov.headers("ask"))
        assert rule.status_code == 200, rule.text
        assert rule.json()["type"] == "behavior" and rule.json()["action"] == {"dung_nguon": "A"}
        assert rule.json()["latest_backtest"] is None
        # An organisation-wide policy is read under any Data Space.
        org_wide = await api.get(f"/v1/policies/{gov.rows['org_wide']}", headers=gov.headers("ask"))
        assert org_wide.status_code == 200 and org_wide.json()["data_space_id"] is None


async def test_reading_a_policy_follows_its_data_space_and_who_reads_access(gov: Gov) -> None:
    async with client(gov) as api:
        # Who sees what is read by admin or steward, not every asker (UC-11-02).
        asker = await api.get(f"/v1/policies/{gov.rows['access']}", headers=gov.headers("ask"))
        assert asker.status_code == 403
        other = await api.get(
            f"/v1/policies/{gov.rows['access_other']}", headers=gov.headers("admin")
        )
        assert other.status_code == 404 and other.json()["code"] == "not_found"
        there = await api.get(
            f"/v1/policies/{gov.rows['access_other']}", headers=gov.headers("admin", gov.other)
        )
        assert there.status_code == 200
        # An admin without a header oversees every Data Space.
        all_spaces = await api.get(
            f"/v1/policies/{gov.rows['access_other']}", headers=gov.headers("admin", False)
        )
        assert all_spaces.status_code == 200
        missing = await api.get(f"/v1/policies/{uuid.uuid4()}", headers=gov.headers("admin"))
        assert missing.status_code == 404
        denied = await api.get(f"/v1/policies/{gov.rows['behavior']}", headers=gov.headers("none"))
        assert denied.status_code == 403


# ---------------------------------------------------------------- disable


async def test_disabling_switches_a_policy_off_and_audits_the_reason(gov: Gov) -> None:
    policy = gov.rows["access"]
    async with client(gov) as api:
        done = await api.post(
            f"/v1/policies/{policy}:disable", json=REASON, headers=gov.headers("admin")
        )
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "disabled" and done.json()["type"] == "access"
        assert gov.status("access_policy", policy) == "disabled"
        events = dict(gov.events(policy))
        domain = events["policy.disabled"]
        assert domain["previous_status"] == "active" and domain["status"] == "disabled"
        # The reason is the audit record's, not the domain event's.
        assert REASON["reason"] not in str(domain)
        audit = events["audit.requested"]
        assert audit["action"] == "policy.disable" and audit["after"]["reason"] == REASON["reason"]
        assert audit["before"] == {"status": "active"}

        again = await api.post(
            f"/v1/policies/{policy}:disable", json=REASON, headers=gov.headers("admin")
        )
        assert again.status_code == 409 and again.json()["code"] == "invalid_state"


async def test_a_draft_is_dropped_and_an_expired_one_is_left(gov: Gov) -> None:
    async with client(gov) as api:
        dropped = await api.post(
            f"/v1/policies/{gov.rows['behavior']}:disable",
            json=REASON,
            headers=gov.headers("admin"),
        )
        assert dropped.status_code == 200, dropped.text
        assert dropped.json()["status"] == "disabled"
        expired = await api.post(
            f"/v1/policies/{gov.rows['expired']}:disable", json=REASON, headers=gov.headers("admin")
        )
        assert expired.status_code == 409 and expired.json()["code"] == "invalid_state"
        for body in ({}, {"reason": ""}, {"reason": "   "}):
            refused = await api.post(
                f"/v1/policies/{gov.rows['access_draft']}:disable",
                json=body,
                headers=gov.headers("admin"),
            )
            assert refused.status_code == 422, (body, refused.text)
        steward = await api.post(
            f"/v1/policies/{gov.rows['access_draft']}:disable",
            json=REASON,
            headers=gov.headers("steward"),
        )
        assert steward.status_code == 403
    assert gov.status("behavior_policy", gov.rows["expired"]) == "expired"
    assert gov.status("access_policy", gov.rows["access_draft"]) == "draft"
    assert gov.events(gov.rows["access_draft"]) == []


async def test_policy_changes_stay_in_the_callers_data_space(gov: Gov) -> None:
    other = gov.rows["access_other"]
    async with client(gov) as api:
        # Under the first Data Space's header, the second one's policy is not there to change.
        disable = await api.post(
            f"/v1/policies/{other}:disable", json=REASON, headers=gov.headers("admin")
        )
        assert disable.status_code == 404, disable.text
        from_other = await api.post(
            f"/v1/policies/{gov.rows['access_draft']}:disable",
            json=REASON,
            headers=gov.headers("admin", gov.other),
        )
        assert from_other.status_code == 404, from_other.text
        activate = await api.post(
            f"/v1/policies/{gov.rows['access_draft']}:activate",
            headers=gov.headers("admin", gov.other),
        )
        assert activate.status_code == 404, activate.text
        # A steward does not switch off an organisation-wide policy.
        org_wide = await api.post(
            f"/v1/policies/{gov.rows['org_wide']}:disable",
            json=REASON,
            headers=gov.headers("steward"),
        )
        assert org_wide.status_code == 403
        # Under its own header, or as an admin without one, the change goes through.
        own = await api.post(
            f"/v1/policies/{other}:disable", json=REASON, headers=gov.headers("admin", gov.other)
        )
        assert own.status_code == 200, own.text
        everywhere = await api.post(
            f"/v1/policies/{gov.rows['org_wide']}:disable",
            json=REASON,
            headers=gov.headers("admin", False),
        )
        assert everywhere.status_code == 200, everywhere.text
    assert gov.status("access_policy", gov.rows["access_draft"]) == "draft"
    assert gov.status("behavior_policy", gov.rows["org_wide"]) == "disabled"
