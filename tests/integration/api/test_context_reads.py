"""The entity model, example questions and synonyms of the context layer (UC-07-02, BR-118, BR-22)
against PostgreSQL: GET /v1/entities, GET /v1/entities/{key}, GET /v1/examples,
PATCH /v1/examples/{id} and GET /v1/synonyms.

Each test builds its own organisation: a steward and an asker in one Data Space (the steward also
in a second one), and a member who holds neither `ask` nor `steward`. The first space declares two
entities (khach_hang on customers, hop_dong on contracts) joined many-to-one; the second space
declares one entity of its own.
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
class Ctx:
    org: uuid.UUID
    space: uuid.UUID
    other: uuid.UUID
    users: dict[str, uuid.UUID]
    rows: dict[str, uuid.UUID]
    seed: Seed

    def headers(self, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer kc-{who}", "X-Data-Space": str(space or self.space)}

    def events(self, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM context.outbox WHERE aggregate_id = %s"
                " ORDER BY created_at, id",
                (str(aggregate_id),),
            )
            return list(cur.fetchall())

    def one(self, sql: str, *params: object) -> Any:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
        value = row[0] if row else None
        # psycopg2 hands back UUID or str depending on the adapters registered: compare as text.
        return str(value) if isinstance(value, uuid.UUID) else value

    def run(self, sql: str, *params: object) -> None:
        with self.seed.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])


def _user(seed: Seed, org: uuid.UUID, name: str, role: uuid.UUID) -> uuid.UUID:
    made = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-{name}",
        username=name,
        display_name=name,
        status="active",
    )
    seed.row("org.user_role", org_id=org, user_id=made, role_id=role, source="manual")
    return made


def _member(seed: Seed, org: uuid.UUID, space: uuid.UUID, user: uuid.UUID, role: uuid.UUID) -> None:
    seed.row(
        "org.data_space_member",
        org_id=org,
        data_space_id=space,
        principal_type="user",
        principal_id=user,
        role_id=role,
    )


def _dataset(seed: Seed, org: uuid.UUID, space: uuid.UUID, table: str, **extra: Any) -> uuid.UUID:
    return seed.row(
        "catalog.dataset",
        org_id=org,
        data_space_id=space,
        origin="derived",
        schema_name="silver",
        table_name=table,
        enabled=True,
        **extra,
    )


@pytest.fixture
def ctx(seed: Seed) -> Ctx:
    org = seed.org()
    roles = {
        "steward": seed.row(
            "org.role", org_id=org, code="STEWARD", name="Steward", capabilities=["ask", "steward"]
        ),
        "ask": seed.row("org.role", org_id=org, code="ASK", name="Ask", capabilities=["ask"]),
        "none": seed.row(
            "org.role", org_id=org, code="VIEW", name="View", capabilities=["view_detail"]
        ),
    }
    users = {
        "steward": _user(seed, org, "steward", roles["steward"]),
        "asker": _user(seed, org, "asker", roles["ask"]),
        "nocap": _user(seed, org, "nocap", roles["none"]),
    }
    space = seed.row(
        "org.data_space", org_id=org, code="DS-CTX", name="Ctx", steward_id=users["steward"]
    )
    other = seed.row(
        "org.data_space", org_id=org, code="DS-CTX-B", name="Ctx B", steward_id=users["steward"]
    )
    for name, role in (("steward", "steward"), ("asker", "ask"), ("nocap", "none")):
        _member(seed, org, space, users[name], roles[role])
    _member(seed, org, other, users["steward"], roles["steward"])

    customers = _dataset(seed, org, space, "khach_hang", display_name="Khách hàng")
    contracts = _dataset(seed, org, space, "hop_dong")
    branches = _dataset(seed, org, space, "chi_nhanh")
    foreign = _dataset(seed, org, other, "khach_hang_b")
    rows: dict[str, uuid.UUID] = {
        "customers": customers,
        "contracts": contracts,
        "branches": branches,
    }
    rows["customer"] = seed.row(
        "context.semantic_entity",
        org_id=org,
        data_space_id=space,
        code="khach_hang",
        name="Khách hàng",
        description="Người có ít nhất một hợp đồng",
        primary_dataset_id=customers,
        key_columns=["ma_kh"],
    )
    rows["contract"] = seed.row(
        "context.semantic_entity",
        org_id=org,
        data_space_id=space,
        code="hop_dong",
        name="Hợp đồng",
        primary_dataset_id=contracts,
        key_columns=["so_hd"],
    )
    rows["foreign_entity"] = seed.row(
        "context.semantic_entity",
        org_id=org,
        data_space_id=other,
        code="khach_hang_b",
        name="Khách hàng B",
        primary_dataset_id=foreign,
        key_columns=["ma_kh"],
    )
    rows["joins"] = seed.row(
        "context.relationship",
        org_id=org,
        data_space_id=space,
        from_dataset_id=contracts,
        from_columns=["ma_kh"],
        to_dataset_id=customers,
        to_columns=["ma_kh"],
        cardinality="many_to_one",
        status="active",
    )
    # A rejected relationship is not part of the model.
    seed.row(
        "context.relationship",
        org_id=org,
        data_space_id=space,
        from_dataset_id=customers,
        from_columns=["ma_cn"],
        to_dataset_id=branches,
        to_columns=["ma_cn"],
        cardinality="many_to_one",
        status="rejected",
    )
    rows["region"] = seed.row(
        "context.semantic_dimension",
        org_id=org,
        data_space_id=space,
        entity_id=rows["customer"],
        code="vung",
        name="Vùng",
        expression="vung",
    )
    seed.row(
        "context.semantic_measure",
        org_id=org,
        data_space_id=space,
        entity_id=rows["customer"],
        code="so_kh",
        name="Số khách hàng",
        expression="ma_kh",
        aggregation="count_distinct",
    )

    def metric(code: str, dataset: uuid.UUID | None, name: str) -> uuid.UUID:
        made = seed.row(
            "context.metric",
            org_id=org,
            data_space_id=space,
            code=code,
            owner_id=users["steward"],
            canonical_dataset_id=dataset,
            current_version=1,
            status="active",
        )
        seed.row(
            "context.metric_version",
            org_id=org,
            metric_id=made,
            version=1,
            display_name=name,
            expression="count(*)",
            period_semantics="end_of_period",
        )
        return made

    rows["on_table"] = metric("so_khach_hang", customers, "Số khách hàng")
    rows["by_dimension"] = metric("du_no", contracts, "Dư nợ")
    rows["unrelated"] = metric("so_chi_nhanh", branches, "Số chi nhánh")
    ctx = Ctx(org, space, other, users, rows, seed)
    # Dư nợ is computed on contracts but broken down by the customer's region.
    ctx.run(
        "UPDATE context.metric_version SET allowed_dimensions = %s::uuid[] WHERE metric_id = %s",
        "{" + str(rows["region"]) + "}",
        rows["by_dimension"],
    )
    return ctx


class Tokens:
    def __init__(self, ctx: Ctx) -> None:
        self.ctx = ctx

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.ctx.org)}


def client(ctx: Ctx) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(ctx))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


def _example(ctx: Ctx, question: str, status: str, **extra: Any) -> uuid.UUID:
    return ctx.seed.row(
        "context.example_query",
        org_id=ctx.org,
        data_space_id=extra.pop("space", ctx.space),
        question=question,
        sql=extra.pop("sql", "SELECT count(*) FROM silver.khach_hang"),
        status=status,
        **extra,
    )


# ---------------------------------------------------------------- entity model


async def test_the_entity_model_lists_keys_tables_and_relationships(ctx: Ctx) -> None:
    async with client(ctx) as api:
        listed = await api.get("/v1/entities", headers=ctx.headers("asker"))
        assert listed.status_code == 200, listed.text
        page = listed.json()
        by_key = {e["key"]: e for e in page["items"]}
        # The second space's entity is not in this one's model.
        assert set(by_key) == {"khach_hang", "hop_dong"} and page["next_page_token"] == ""
        customer = by_key["khach_hang"]
        assert customer["primary_dataset"] == {
            "id": str(ctx.rows["customers"]),
            "name": "Khách hàng",
        }
        assert customer["key_columns"] == ["ma_kh"] and customer["status"] == "active"
        # Only the active relationship; the rejected one to chi_nhanh is not in the model.
        (join,) = customer["relationships"]
        assert join["id"] == str(ctx.rows["joins"]) and join["cardinality"] == "many_to_one"
        assert (join["from_entity"], join["to_entity"]) == ("hop_dong", "khach_hang")
        assert by_key["hop_dong"]["primary_dataset"]["name"] == "silver.hop_dong"
        assert [r["id"] for r in by_key["hop_dong"]["relationships"]] == [str(ctx.rows["joins"])]

        found = await api.get("/v1/entities", params={"q": "hợp"}, headers=ctx.headers("asker"))
        assert [e["key"] for e in found.json()["items"]] == ["hop_dong"]
        paged = await api.get("/v1/entities", params={"page_size": 1}, headers=ctx.headers("asker"))
        token = paged.json()["next_page_token"]
        rest = await api.get(
            "/v1/entities", params={"page_size": 1, "cursor": token}, headers=ctx.headers("asker")
        )
        assert len(paged.json()["items"]) == 1 and len(rest.json()["items"]) == 1
        assert paged.json()["items"][0]["key"] != rest.json()["items"][0]["key"]

        assert (await api.get("/v1/entities", headers=ctx.headers("nocap"))).status_code == 403
        other = await api.get("/v1/entities", headers=ctx.headers("steward", ctx.other))
        assert [e["key"] for e in other.json()["items"]] == ["khach_hang_b"]


async def test_one_entity_names_its_dimensions_measures_and_metrics(ctx: Ctx) -> None:
    async with client(ctx) as api:
        got = await api.get("/v1/entities/khach_hang", headers=ctx.headers("asker"))
        assert got.status_code == 200, got.text
        body = got.json()
        assert (
            body["key"] == "khach_hang" and body["description"] == "Người có ít nhất một hợp đồng"
        )
        assert [d["code"] for d in body["dimensions"]] == ["vung"]
        assert body["measures"][0]["aggregation"] == "count_distinct"
        # On its table, or broken down by its region; the branch count uses neither.
        assert {m["code"]: m["display_name"] for m in body["metrics"]} == {
            "du_no": "Dư nợ",
            "so_khach_hang": "Số khách hàng",
        }
        assert len(body["relationships"]) == 1

        missing = await api.get("/v1/entities/khong_co", headers=ctx.headers("asker"))
        assert missing.status_code == 404 and missing.json()["code"] == "not_found"
        # Another Data Space's entity is not reachable under this one's header.
        elsewhere = await api.get("/v1/entities/khach_hang_b", headers=ctx.headers("steward"))
        assert elsewhere.status_code == 404
        denied = await api.get("/v1/entities/khach_hang", headers=ctx.headers("nocap"))
        assert denied.status_code == 403


@pytest.mark.req("AC-07-01-06", "AC-07-01-07")
async def test_metric_code_is_immutable_and_filed_metric_can_only_be_retired(ctx: Ctx) -> None:
    metric = ctx.rows["by_dimension"]
    async with client(ctx) as api:
        version = await api.post(
            f"/v1/metrics/{metric}/versions",
            json={
                "display_name": "Dư nợ phiên bản mới",
                "expression": "sum(balance)",
                "period_semantics": "end_of_period",
                "additivity": "semi",
            },
            headers=ctx.headers("steward"),
        )
        assert version.status_code == 201, version.text
        assert version.json()["version"] == 2
        assert version.json()["valid_from"] is not None

        renamed = await api.patch(
            f"/v1/metrics/{metric}",
            json={"code": "DU_NO_RENAMED"},
            headers=ctx.headers("steward"),
        )
        assert renamed.status_code == 409 and renamed.json()["code"] == "immutable"

        ctx.run("UPDATE context.metric SET in_filed_report = true WHERE id = %s", metric)
        blocked = await api.delete(f"/v1/metrics/{metric}", headers=ctx.headers("steward"))
        assert blocked.status_code == 409 and blocked.json()["code"] == "in_use"
        assert ctx.one("SELECT status FROM context.metric WHERE id = %s", metric) == "active"

        ctx.run("UPDATE context.metric SET in_filed_report = false WHERE id = %s", metric)
        retired = await api.delete(f"/v1/metrics/{metric}", headers=ctx.headers("steward"))
        assert retired.status_code == 204
    assert ctx.one("SELECT status FROM context.metric WHERE id = %s", metric) == "retired"
    assert (
        ctx.one(
            "SELECT expression FROM context.metric_version WHERE metric_id = %s AND version = 1",
            metric,
        )
        == "count(*)"
    )


# ---------------------------------------------------------------- examples and synonyms


async def test_examples_are_listed_by_status_and_text(ctx: Ctx) -> None:
    active = _example(ctx, "Có bao nhiêu khách hàng?", "active")
    suggested = _example(ctx, "Dư nợ theo vùng tháng trước?", "pending_review")
    _example(ctx, "Khách hàng của không gian khác?", "active", space=ctx.other)
    async with client(ctx) as api:
        listed = await api.get("/v1/examples", headers=ctx.headers("asker"))
        assert listed.status_code == 200, listed.text
        items = listed.json()["items"]
        assert {i["id"] for i in items} == {str(active), str(suggested)}
        assert {"question", "sql", "metric_ids", "origin", "last_verified_at"} <= set(items[0])
        pending = await api.get(
            "/v1/examples", params={"status": "pending_review"}, headers=ctx.headers("asker")
        )
        assert [i["id"] for i in pending.json()["items"]] == [str(suggested)]
        text = await api.get("/v1/examples", params={"q": "dư nợ"}, headers=ctx.headers("asker"))
        assert [i["id"] for i in text.json()["items"]] == [str(suggested)]
        paged = await api.get("/v1/examples", params={"page_size": 1}, headers=ctx.headers("asker"))
        assert paged.json()["next_page_token"]
        assert (await api.get("/v1/examples", headers=ctx.headers("nocap"))).status_code == 403


async def test_synonyms_are_listed_and_matched_without_accents(ctx: Ctx) -> None:
    def synonym(phrase: str, norm: str, status: str, space: uuid.UUID | None = None) -> uuid.UUID:
        return ctx.seed.row(
            "context.synonym",
            org_id=ctx.org,
            data_space_id=space or ctx.space,
            phrase=phrase,
            phrase_norm=norm,
            target_kind="entity",
            target_id=ctx.rows["customer"],
            status=status,
        )

    kh = synonym("Khách", "khach", "active")
    nguoi_vay = synonym("Người vay", "nguoi vay", "pending_review")
    synonym("Khách B", "khach b", "active", ctx.other)
    async with client(ctx) as api:
        listed = await api.get("/v1/synonyms", headers=ctx.headers("asker"))
        assert listed.status_code == 200, listed.text
        assert {s["id"] for s in listed.json()["items"]} == {str(kh), str(nguoi_vay)}
        matched = await api.get("/v1/synonyms", params={"q": "Người"}, headers=ctx.headers("asker"))
        assert [s["id"] for s in matched.json()["items"]] == [str(nguoi_vay)]
        active = await api.get(
            "/v1/synonyms", params={"status": "active"}, headers=ctx.headers("asker")
        )
        assert [s["id"] for s in active.json()["items"]] == [str(kh)]
        assert (await api.get("/v1/synonyms", headers=ctx.headers("nocap"))).status_code == 403


@pytest.mark.req("AC-07-02-04")
async def test_a_second_meaning_becomes_a_pending_ambiguous_glossary_term(ctx: Ctx) -> None:
    ctx.seed.row(
        "context.synonym",
        org_id=ctx.org,
        data_space_id=ctx.space,
        phrase="nợ xấu",
        phrase_norm="no xau",
        target_kind="entity",
        target_id=ctx.rows["customer"],
        status="active",
    )
    async with client(ctx) as api:
        second = await api.post(
            "/v1/synonyms",
            json={
                "phrase": "Nợ Xấu",
                "target_kind": "entity",
                "target_id": str(ctx.rows["contract"]),
            },
            headers=ctx.headers("steward"),
        )
        assert second.status_code == 201, second.text
        assert second.json()["glossary_term_id"] is not None
        terms = await api.get("/v1/terms", params={"q": "nợ xấu"}, headers=ctx.headers("steward"))
        assert terms.status_code == 200
        term = terms.json()["items"][0]
        assert term["status"] == "pending_review" and len(term["meanings"]) == 2


@pytest.mark.req("AC-07-09-02")
async def test_steward_can_filter_unmapped_phrases_by_review_threshold(ctx: Ctx) -> None:
    ctx.seed.row(
        "context.unmapped_phrase",
        org_id=ctx.org,
        data_space_id=ctx.space,
        phrase_norm="no xau moi",
        sample_questions=["Nợ xấu mới là gì?"],
        ask_count=6,
        suggested_target=Json({"target_kind": "metric", "code": "NPL_RATIO"}),
    )
    async with client(ctx) as api:
        listed = await api.get(
            "/v1/unmapped-phrases",
            params={"min_count": 6},
            headers=ctx.headers("steward"),
        )
        assert listed.status_code == 200, listed.text
        (phrase,) = listed.json()["items"]
        assert phrase["ask_count"] == 6
        assert phrase["sample_questions"] == ["Nợ xấu mới là gì?"]
        assert phrase["suggested_target"]["code"] == "NPL_RATIO"


async def test_a_changed_sql_goes_back_to_the_checker(ctx: Ctx) -> None:
    example = _example(ctx, "Có bao nhiêu khách hàng?", "active", last_verified_at="2026-10-01")
    new_sql = "SELECT count(DISTINCT ma_kh) FROM silver.khach_hang"
    async with client(ctx) as api:
        changed = await api.patch(
            f"/v1/examples/{example}",
            json={"sql": new_sql + ";", "question": "Bao nhiêu khách hàng đang có?"},
            headers=ctx.headers("steward"),
        )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    # Out of the standard set until the checker has run the new SQL (BR-118).
    assert body["sql"] == new_sql and body["status"] == "draft"
    assert body["question"] == "Bao nhiêu khách hàng đang có?" and body["last_verified_at"] is None
    assert ctx.one("SELECT sql FROM context.example_query WHERE id = %s", example) == new_sql
    ((event, payload),) = ctx.events(example)
    assert event == "example_query.updated" and payload["rerun_sql"] is True
    assert payload["previous_status"] == "active" and payload["status"] == "draft"
    assert payload["changed"] == ["question", "sql"]


async def test_a_verified_query_may_name_its_period(ctx: Ctx) -> None:
    # R1-08: :period_start / :period_end are bound by the worker to the question's period.
    example = _example(ctx, "Khách hàng mới tháng 9/2026", "pending_review")
    templated = (
        "SELECT count(*) FROM silver.khach_hang"
        " WHERE ngay_mo >= :period_start AND ngay_mo < :period_end"
    )
    async with client(ctx) as api:
        changed = await api.patch(
            f"/v1/examples/{example}", json={"sql": templated}, headers=ctx.headers("steward")
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["sql"] == templated and changed.json()["status"] == "draft"
        # A placeholder does not let a write through.
        refused = await api.patch(
            f"/v1/examples/{example}",
            json={"sql": "DELETE FROM silver.khach_hang WHERE ngay_mo < :period_end"},
            headers=ctx.headers("steward"),
        )
        assert refused.status_code == 422


async def test_only_one_read_only_statement_is_stored(ctx: Ctx) -> None:
    example = _example(ctx, "Có bao nhiêu khách hàng?", "pending_review")
    async with client(ctx) as api:
        for sql in (
            "DELETE FROM silver.khach_hang",
            "SELECT 1; SELECT 2",
            "SELECT 1; DROP TABLE silver.khach_hang",
            "UPDATE silver.khach_hang SET ma_kh = NULL",
        ):
            refused = await api.patch(
                f"/v1/examples/{example}", json={"sql": sql}, headers=ctx.headers("steward")
            )
            assert refused.status_code == 422, (sql, refused.text)
            assert refused.json()["errors"][0]["field"] == "sql"
        # Confirming a new SQL in the same request would skip its run.
        both = await api.patch(
            f"/v1/examples/{example}",
            json={"sql": "SELECT 2", "status": "active"},
            headers=ctx.headers("steward"),
        )
        assert both.status_code == 422 and both.json()["errors"][0]["field"] == "status"
        null = await api.patch(
            f"/v1/examples/{example}", json={"question": None}, headers=ctx.headers("steward")
        )
        assert null.status_code == 422
    assert ctx.one("SELECT status FROM context.example_query WHERE id = %s", example) == (
        "pending_review"
    )
    assert ctx.events(example) == []


async def test_a_checked_suggestion_is_confirmed_and_a_draft_is_not(ctx: Ctx) -> None:
    suggested = _example(ctx, "Dư nợ theo vùng?", "pending_review", origin="agent_suggested")
    draft = _example(ctx, "Số hợp đồng?", "draft")
    async with client(ctx) as api:
        confirmed = await api.patch(
            f"/v1/examples/{suggested}", json={"status": "active"}, headers=ctx.headers("steward")
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "active"
        ((_, payload),) = ctx.events(suggested)
        assert payload["rerun_sql"] is False and payload["status"] == "active"
        # The checker has not run a draft's SQL yet.
        early = await api.patch(
            f"/v1/examples/{draft}", json={"status": "active"}, headers=ctx.headers("steward")
        )
        assert early.status_code == 409 and early.json()["code"] == "invalid_state"
        dropped = await api.patch(
            f"/v1/examples/{draft}", json={"status": "rejected"}, headers=ctx.headers("steward")
        )
        assert dropped.status_code == 200 and dropped.json()["status"] == "rejected"
        unknown = await api.patch(
            f"/v1/examples/{draft}", json={"status": "verified"}, headers=ctx.headers("steward")
        )
        assert unknown.status_code == 422


async def test_editing_an_example_needs_steward_in_its_data_space(ctx: Ctx) -> None:
    example = _example(ctx, "Có bao nhiêu khách hàng?", "pending_review")
    elsewhere = _example(ctx, "Khách B?", "pending_review", space=ctx.other)
    async with client(ctx) as api:
        asker = await api.patch(
            f"/v1/examples/{example}", json={"status": "active"}, headers=ctx.headers("asker")
        )
        assert asker.status_code == 403
        other_space = await api.patch(
            f"/v1/examples/{elsewhere}", json={"status": "active"}, headers=ctx.headers("steward")
        )
        assert other_space.status_code == 404
        missing = await api.patch(
            f"/v1/examples/{uuid.uuid4()}",
            json={"status": "active"},
            headers=ctx.headers("steward"),
        )
        assert missing.status_code == 404
    assert ctx.one("SELECT status FROM context.example_query WHERE id = %s", elsewhere) == (
        "pending_review"
    )
