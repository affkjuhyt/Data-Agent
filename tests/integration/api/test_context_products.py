"""Data products (UC-07-13, BR-175, BR-178) against PostgreSQL.

Each test builds its own organisation: a steward who writes products, an owner, an analyst who
only asks, and a data admin for the P06 published layer, in one Data Space with a versioned
dataset and a certified metric.
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
class Space:
    org: uuid.UUID
    space: uuid.UUID
    other: uuid.UUID
    users: dict[str, uuid.UUID]
    dataset: uuid.UUID
    flat_dataset: uuid.UUID
    metric: uuid.UUID
    draft_metric: uuid.UUID
    seed: Seed

    def headers(self, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer kc-{who}", "X-Data-Space": str(space or self.space)}

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM context.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
            return row[0] if row else None


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


@pytest.fixture
def ds(seed: Seed) -> Space:
    org = seed.org()
    steward_role = seed.row(
        "org.role", org_id=org, code="STEWARD", name="Steward", capabilities=["ask", "steward"]
    )
    ask_role = seed.row("org.role", org_id=org, code="ASK", name="Ask", capabilities=["ask"])
    admin_role = seed.row(
        "org.role", org_id=org, code="ADMIN_DATA", name="Data", capabilities=["ask", "admin_data"]
    )
    keeper = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-keeper-{org.hex[:6]}",
        username="keeper",
        display_name="Keeper",
    )
    space = seed.row("org.data_space", org_id=org, code="DS-DP", name="DP", steward_id=keeper)
    other = seed.row("org.data_space", org_id=org, code="DS-DP-B", name="DP B", steward_id=keeper)
    users = {
        "steward": _user(seed, org, "steward", steward_role, space),
        "owner": _user(seed, org, "owner", steward_role, space),
        "analyst": _user(seed, org, "analyst", ask_role, space),
        "dataadmin": _user(seed, org, "dataadmin", admin_role, space),
    }
    dataset = seed.row(
        "catalog.dataset",
        org_id=org,
        data_space_id=space,
        origin="consolidated",
        schema_name="silver",
        table_name="gia_xang_dau_ky_dieu_hanh",
        history_kind="snapshot",
        enabled=True,
    )
    flat = seed.row(
        "catalog.dataset",
        org_id=org,
        data_space_id=space,
        origin="derived",
        schema_name="silver",
        table_name="tai_cho",
        history_kind="none",
        enabled=True,
    )
    metric = seed.row(
        "context.metric",
        org_id=org,
        data_space_id=space,
        code="gia_tran_ban_le",
        owner_id=users["owner"],
        current_version=1,
        status="active",
    )
    seed.row(
        "context.metric_version",
        org_id=org,
        metric_id=metric,
        version=1,
        display_name="Giá trần bán lẻ",
        expression="max(gia_tran)",
        period_semantics="end_of_period",
        status="certified",
        certified_by=users["owner"],
        certified_at="2026-06-01T00:00:00Z",
        valid_from="2026-06-01T00:00:00Z",
    )
    draft_metric = seed.row(
        "context.metric",
        org_id=org,
        data_space_id=space,
        code="gia_co_so",
        owner_id=users["owner"],
        current_version=1,
        status="draft",
    )
    seed.row(
        "context.metric_version",
        org_id=org,
        metric_id=draft_metric,
        version=1,
        display_name="Giá cơ sở",
        expression="avg(gia_co_so)",
        period_semantics="end_of_period",
        status="draft",
    )
    return Space(org, space, other, users, dataset, flat, metric, draft_metric, seed)


class Tokens:
    def __init__(self, ds: Space) -> None:
        self.ds = ds

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.ds.org)}


def client(ds: Space) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(ds))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


def _questions(n: int) -> list[dict[str, Any]]:
    return [
        {"text": f"Giá trần E5RON92 kỳ {i}?", "metric_code": "gia_tran_ban_le", "expected": 19_730}
        for i in range(n)
    ]


def _body(ds: Space, **extra: Any) -> dict[str, Any]:
    body = {
        "code": "gia_xang_dau_trong_nuoc",
        "name": "Giá xăng dầu trong nước",
        "description": "Giá cơ sở và giá trần bán lẻ theo từng kỳ điều hành",
        "period_grain": "event",
        "period_from": "2024-01-01",
        "owner_id": str(ds.users["owner"]),
        "steward_id": str(ds.users["steward"]),
        "freshness_statement": "Cập nhật trước 16:30 ngày điều hành",
        "scope_entities": [{"dimension": "mat_hang", "values": ["E5RON92", "RON95-III"]}],
    }
    body.update(extra)
    return body


async def _create(api: httpx.AsyncClient, ds: Space, **extra: Any) -> dict[str, Any]:
    made = await api.post(
        "/v1/data-products", json=_body(ds, **extra), headers=ds.headers("steward")
    )
    assert made.status_code == 201, made.text
    out: dict[str, Any] = made.json()
    return out


async def _members(api: httpx.AsyncClient, ds: Space, product: str) -> None:
    for body in (
        {"member_kind": "metric", "member_ref": str(ds.metric)},
        {
            "member_kind": "dataset",
            "member_ref": str(ds.dataset),
            "role": "primary",
            "sla_kind": "max_age",
            "sla_max_age": "P1D",
        },
    ):
        added = await api.post(
            f"/v1/data-products/{product}/members", json=body, headers=ds.headers("steward")
        )
        assert added.status_code == 201, added.text


def _activate(ds: Space, product: str) -> None:
    # What accepting the data_product proposal does (inbox → context consumer), done as admin.
    with ds.seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE context.data_product SET status = 'active', activated_by = %s,"
            " activated_at = now(), sample_questions = %s WHERE id = %s",
            (str(ds.users["owner"]), Json(_questions(10)), product),
        )


async def test_steward_creates_a_draft_with_its_eval_set(ds: Space) -> None:
    async with client(ds) as api:
        made = await _create(ds=ds, api=api)
        assert made["layer"] == "context"
        assert made["status"] == "draft" and made["version"] == 1
        assert made["eval_set_id"]
        events = dict(ds.events(made["id"]))
        assert events["data_product.created"]["eval_set_id"] == made["eval_set_id"]
        assert events["data_product.created"]["eval_set_subject"] == "context"
        assert "context.data_product.changed" in events

        again = await api.post("/v1/data-products", json=_body(ds), headers=ds.headers("steward"))
        assert again.status_code == 409, again.text
        # Not a member of the other Data Space: it does not exist for the steward there.
        other = await api.post(
            "/v1/data-products", json=_body(ds), headers=ds.headers("steward", ds.other)
        )
        assert other.status_code == 404, other.text

        fetched = await api.get(f"/v1/data-products/{made['id']}", headers=ds.headers("analyst"))
        assert fetched.status_code == 200
        assert fetched.json()["members"] == [] and fetched.json()["freshness_state"] == "unknown"
        assert fetched.json()["open_error_book_entries"] == 0
        hidden = await api.get(
            f"/v1/data-products/{made['id']}", headers=ds.headers("steward", ds.other)
        )
        assert hidden.status_code == 404


async def test_owner_and_steward_are_two_people(ds: Space) -> None:
    same = _body(ds, owner_id=str(ds.users["steward"]))
    async with client(ds) as api:
        refused = await api.post("/v1/data-products", json=same, headers=ds.headers("steward"))
    assert refused.status_code == 422
    assert refused.json()["code"] == "validation_failed"


async def test_each_layer_needs_its_own_capability(ds: Space) -> None:
    async with client(ds) as api:
        # The analyst holds neither; the data admin has admin_data but not steward.
        no_steward = await api.post(
            "/v1/data-products", json=_body(ds), headers=ds.headers("dataadmin")
        )
        assert no_steward.status_code == 403, no_steward.text
        published = {
            "layer": "published",
            "code": "gia_ban_le_chia_se",
            "name": "Giá bán lẻ dùng chung",
            "dataset_id": str(ds.dataset),
            "contract": {"columns": [{"name": "gia", "type": "numeric"}]},
        }
        no_admin = await api.post(
            "/v1/data-products", json=published, headers=ds.headers("steward")
        )
        assert no_admin.status_code == 403, no_admin.text
        bogus = await api.post(
            "/v1/data-products", json={**published, "layer": "x"}, headers=ds.headers("dataadmin")
        )
        assert bogus.status_code == 422
        made = await api.post("/v1/data-products", json=published, headers=ds.headers("dataadmin"))
        assert made.status_code == 201, made.text
        assert made.json()["layer"] == "published"
        assert (
            ds.one("SELECT status FROM pipeline.data_product WHERE id = %s", made.json()["id"])
            == "draft"
        )
        mixed = await api.post(
            "/v1/data-products",
            json={**_body(ds), "dataset_id": str(ds.dataset)},
            headers=ds.headers("steward"),
        )
        assert mixed.status_code == 422


# AC-07-13-02
async def test_members_are_checked_before_they_join(ds: Space) -> None:
    async with client(ds) as api:
        product = (await _create(api, ds))["id"]
        url = f"/v1/data-products/{product}/members"
        uncertified = await api.post(
            url,
            json={"member_kind": "metric", "member_ref": str(ds.draft_metric)},
            headers=ds.headers("steward"),
        )
        assert uncertified.status_code == 422
        assert uncertified.json()["code"] == "business_rule_violated"
        in_place = await api.post(
            url,
            json={
                "member_kind": "dataset",
                "member_ref": str(ds.flat_dataset),
                "role": "reference",
            },
            headers=ds.headers("steward"),
        )
        assert in_place.status_code == 422
        no_commitment = await api.post(
            url,
            json={"member_kind": "dataset", "member_ref": str(ds.dataset), "role": "primary"},
            headers=ds.headers("steward"),
        )
        assert no_commitment.status_code == 422
        stranger = await api.post(
            url,
            json={"member_kind": "metric", "member_ref": str(uuid.uuid4())},
            headers=ds.headers("steward"),
        )
        assert stranger.status_code == 422

        primary = await api.post(
            url,
            json={
                "member_kind": "dataset",
                "member_ref": str(ds.dataset),
                "role": "primary",
                "sla_kind": "schedule",
                "sla_cron": "30 16 * * 4",
                "sla_grace": "PT30M",
            },
            headers=ds.headers("steward"),
        )
        assert primary.status_code == 201, primary.text
        member = primary.json()
        assert member["next_due_at"] is not None
        assert member["freshness_state"] == "missing"
        twice = await api.post(
            url,
            json={
                "member_kind": "dataset",
                "member_ref": str(ds.dataset),
                "role": "primary",
                "sla_kind": "max_age",
                "sla_max_age": "P1D",
            },
            headers=ds.headers("steward"),
        )
        assert twice.status_code == 409

        removed = await api.delete(f"{url}/{member['id']}", headers=ds.headers("steward"))
        assert removed.status_code == 204


# AC-07-13-05
async def test_submit_needs_ten_answered_questions_and_the_members(ds: Space) -> None:
    async with client(ds) as api:
        product = (await _create(api, ds, sample_questions=_questions(9)))["id"]
        await _members(api, ds, product)
        short = await api.post(
            f"/v1/data-products/{product}:submit", json={}, headers=ds.headers("steward")
        )
        assert short.status_code == 422
        assert "9 sample questions" in short.json()["detail"]

        patched = await api.patch(
            f"/v1/data-products/{product}",
            json={"sample_questions": _questions(10)},
            headers=ds.headers("steward"),
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["version"] == 1
        sent = await api.post(
            f"/v1/data-products/{product}:submit",
            json={"note": "đủ 10 câu"},
            headers=ds.headers("steward"),
        )
        assert sent.status_code == 202, sent.text
        run = sent.json()["eval_run_id"]
        assert sent.headers["Location"] == f"/v1/eval-runs/{run}"
        submitted = dict(ds.events(product))["data_product.submitted"]
        assert submitted["eval_run_id"] == run and submitted["min_items"] == 10
        assert submitted["assignee_id"] == str(ds.users["owner"])


# AC-07-13-03
async def test_a_change_to_an_active_product_is_a_new_version_in_review(ds: Space) -> None:
    async with client(ds) as api:
        product = (await _create(api, ds))["id"]
        await _members(api, ds, product)
        _activate(ds, product)
        draft = await api.patch(
            f"/v1/data-products/{product}",
            json={"freshness_statement": "Cập nhật trước 17:00 ngày điều hành"},
            headers=ds.headers("steward"),
        )
        assert draft.status_code == 200, draft.text
        assert draft.json()["version"] == 2 and draft.json()["status"] == "pending_review"
        # Version 1 keeps serving until the proposal is decided.
        assert ds.one("SELECT version FROM context.data_product WHERE id = %s", product) == 1
        assert ds.one("SELECT status FROM context.data_product WHERE id = %s", product) == "active"
        proposed = dict(ds.events(product))["data_product.change_proposed"]
        assert proposed["version"] == 2
        assert proposed["changes"] == {"freshness_statement": "Cập nhật trước 17:00 ngày điều hành"}
        second = await api.patch(
            f"/v1/data-products/{product}", json={"name": "Khác"}, headers=ds.headers("steward")
        )
        assert second.status_code == 409

        primary = ds.one(
            "SELECT id FROM context.data_product_member WHERE data_product_id = %s AND role = 'primary'",
            product,
        )
        kept = await api.delete(
            f"/v1/data-products/{product}/members/{primary}", headers=ds.headers("steward")
        )
        assert kept.status_code == 422


async def test_suspend_and_retire(ds: Space) -> None:
    async with client(ds) as api:
        product = (await _create(api, ds))["id"]
        not_active = await api.post(
            f"/v1/data-products/{product}:suspend",
            json={"reason": "kiểm tra"},
            headers=ds.headers("steward"),
        )
        assert not_active.status_code == 409
        await _members(api, ds, product)
        _activate(ds, product)
        suspended = await api.post(
            f"/v1/data-products/{product}:suspend",
            json={"reason": "nguồn đang sửa"},
            headers=ds.headers("steward"),
        )
        assert suspended.status_code == 200, suspended.text
        assert suspended.json()["status"] == "suspended"
        assert suspended.json()["suspended_reason"] == "manual"
        audits = [p for e, p in ds.events(product) if e == "audit.requested"]
        assert [a["action"] for a in audits] == ["data_product.suspend"]
        assert audits[0]["after"]["reason"] == "nguồn đang sửa"
        retired = await api.post(
            f"/v1/data-products/{product}:retire",
            json={"reason": "thay bằng sản phẩm khác"},
            headers=ds.headers("steward"),
        )
        assert retired.json()["status"] == "retired"
        frozen = await api.post(
            f"/v1/data-products/{product}/members",
            json={"member_kind": "metric", "member_ref": str(ds.metric), "role": "reference"},
            headers=ds.headers("steward"),
        )
        assert frozen.status_code == 422
        still_there = await api.get(f"/v1/data-products/{product}", headers=ds.headers("analyst"))
        assert still_there.json()["status"] == "retired"


async def test_scope_index_matches_metrics_entities_and_periods(ds: Space) -> None:
    async with client(ds) as api:
        product = (await _create(api, ds))["id"]
        draft = (await _create(api, ds, code="nhap_chua_dung"))["id"]
        await _members(api, ds, product)
        _activate(ds, product)
        index = await api.get("/v1/data-products/index", headers=ds.headers("analyst"))
        assert index.status_code == 200, index.text
        assert [p["id"] for p in index.json()] == [product]
        assert index.json()[0]["metric_codes"] == ["gia_tran_ban_le"]
        assert draft not in {p["id"] for p in index.json()}

        inside = await api.get(
            "/v1/data-products/index",
            params={
                "metric_codes": "gia_tran_ban_le",
                "dimension_values": "mat_hang:E5RON92",
                "period": "2026-07-02",
            },
            headers=ds.headers("analyst"),
        )
        assert inside.json()[0]["match"] == {
            "data_product_id": product,
            "in_scope": True,
            "reasons": [],
        }
        outside = await api.get(
            "/v1/data-products/index",
            params=[
                ("metric_codes", "gia_tran_ban_le"),
                ("metric_codes", "gia_co_so"),
                ("dimension_values", "mat_hang:mazut"),
                ("period", "2023-12-31"),
            ],
            headers=ds.headers("analyst"),
        )
        match = outside.json()[0]["match"]
        assert match["in_scope"] is False and len(match["reasons"]) == 3

        listed = await api.get("/v1/data-products?status=active", headers=ds.headers("analyst"))
        assert [p["id"] for p in listed.json()["items"]] == [product]
        assert listed.json()["items"][0]["freshness_state"] == "missing"


# AC-07-13-08
async def test_freshness_follows_the_commitment(ds: Space) -> None:
    async with client(ds) as api:
        product = (await _create(api, ds))["id"]
        added = await api.post(
            f"/v1/data-products/{product}/members",
            json={
                "member_kind": "dataset",
                "member_ref": str(ds.dataset),
                "role": "primary",
                "sla_kind": "schedule",
                "sla_cron": "30 16 * * 4",
                "sla_grace": "PT30M",
                "sla_timezone": "Asia/Ho_Chi_Minh",
            },
            headers=ds.headers("steward"),
        )
        member = added.json()["id"]
        # Loaded up to the 25/06 period only.
        ds.seed.conn.cursor().execute(
            "UPDATE context.data_product_member SET last_as_of = '2026-06-25T00:00:00+07' WHERE id = %s",
            (member,),
        )
        url = f"/v1/data-products/{product}/freshness"
        # 16:45 on Thursday 02/07, inside the 30-minute grace: the 02/07 deadline is not due yet.
        in_grace = await api.get(
            url, params={"at": "2026-07-02T16:45:00+07:00"}, headers=ds.headers("analyst")
        )
        assert in_grace.status_code == 200, in_grace.text
        assert in_grace.json()["datasets"][0]["state"] == "fresh"
        late = await api.get(
            url,
            params={"at": "2026-07-02T17:05:00+07:00", "periods": 3},
            headers=ds.headers("analyst"),
        )
        state = late.json()["datasets"][0]
        assert state["state"] == "overdue"
        assert state["expected_as_of"].startswith("2026-07-02")
        periods = [(p["period"], p["state"]) for p in state["periods"]]
        assert periods == [
            ("2026-07-09", "not_available"),
            ("2026-07-02", "masked_stale"),
            ("2026-06-25", "shown"),
        ]
        naive = await api.get(
            url, params={"at": "2026-07-02T17:05:00"}, headers=ds.headers("analyst")
        )
        assert naive.status_code == 422
        # The time of `at` aside, nothing was written by reading.
        assert (
            ds.one("SELECT freshness_state FROM context.data_product_member WHERE id = %s", member)
            == "missing"
        )


async def test_data_product_reads_stay_in_the_data_space(ds: Space) -> None:
    async with client(ds) as api:
        product = (await _create(api, ds))["id"]
        for path in ("", "/freshness"):
            other = await api.get(
                f"/v1/data-products/{product}{path}", headers=ds.headers("analyst", ds.other)
            )
            assert other.status_code == 404, other.text
        missing = await api.get(f"/v1/data-products/{uuid.uuid4()}", headers=ds.headers("analyst"))
        assert missing.status_code == 404
        wrong_version = await api.get(
            f"/v1/data-products/{product}?version=2", headers=ds.headers("analyst")
        )
        assert wrong_version.status_code == 404


async def test_the_creator_cannot_be_the_owner(ds: Space) -> None:
    async with client(ds) as api:
        mine = await api.post(
            "/v1/data-products",
            json=_body(ds, owner_id=str(ds.users["steward"]), steward_id=str(ds.users["owner"])),
            headers=ds.headers("steward"),
        )
        assert mine.status_code == 422
        assert mine.json()["errors"][0]["field"] == "owner_id"
        product = (await _create(api, ds))["id"]
        swap = await api.patch(
            f"/v1/data-products/{product}",
            json={"owner_id": str(ds.users["steward"]), "steward_id": str(ds.users["owner"])},
            headers=ds.headers("steward"),
        )
        assert swap.status_code == 422


async def test_personal_data_raises_the_product_classification(ds: Space) -> None:
    ds.seed.row(
        "catalog.column_meta",
        org_id=ds.org,
        dataset_id=ds.dataset,
        dataset_version=1,
        name="so_cccd",
        ordinal=1,
        data_type="text",
        normalized_type="string",
        pii_label="national_id",
    )
    async with client(ds) as api:
        product = (await _create(api, ds))["id"]
        added = await api.post(
            f"/v1/data-products/{product}/members",
            json={
                "member_kind": "dataset",
                "member_ref": str(ds.dataset),
                "role": "primary",
                "sla_kind": "max_age",
                "sla_max_age": "P1D",
            },
            headers=ds.headers("steward"),
        )
        assert added.status_code == 201, added.text
        assert added.json()["classification"] == "confidential"
        fetched = await api.get(f"/v1/data-products/{product}", headers=ds.headers("analyst"))
        assert fetched.json()["classification"] == "confidential"
