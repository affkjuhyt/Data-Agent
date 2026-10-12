"""Truy vấn, endpoint added 12/10/2026: POST /v1/query-runs, a read-only run on one dataset
(with filters) or on one SQL statement, as the caller, against PostgreSQL and the V3 grants.

The query worker is not running: a run is asserted as its queued query_run row and the
query_run.requested event; the rows it would write are seeded to read them back via /rows.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]

PEOPLE = {
    # who: capabilities
    "analyst": ["ask", "view_detail"],
    "peer": ["ask", "view_detail"],
    "asker": ["ask"],
}


@dataclass
class World:
    org: uuid.UUID
    space: uuid.UUID
    other_space: uuid.UUID
    users: dict[str, uuid.UUID]
    dataset: uuid.UUID
    source: uuid.UUID
    tag: str
    seed: Seed


class Tokens:
    def __init__(self, org: uuid.UUID) -> None:
        self.org = org

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org)}


def _dataset(
    seed: Seed, org: uuid.UUID, space: uuid.UUID, source: uuid.UUID | None, **kw: Any
) -> uuid.UUID:
    values: dict[str, Any] = {
        "org_id": org,
        "data_space_id": space,
        "schema_name": "dw",
        "table_name": f"t_{uuid.uuid4().hex[:8]}",
        "enabled": True,
    }
    if source is not None:
        values["source_id"] = source
    else:
        values["origin"] = "derived"
    values.update(kw)
    return seed.row("catalog.dataset", **values)


@pytest.fixture
def world(seed: Seed) -> World:
    org = seed.org()
    tag = org.hex[:8]
    users: dict[str, uuid.UUID] = {}
    roles: dict[str, uuid.UUID] = {}
    for who, caps in PEOPLE.items():
        users[who] = seed.row(
            "org.app_user",
            org_id=org,
            external_id=f"kc-{who}-{tag}",
            username=f"{who}-{tag}",
            display_name=who,
            status="active",
        )
        roles[who] = seed.row(
            "org.role", org_id=org, code=f"Q-{who}-{tag}", name=who, capabilities=caps
        )
        seed.row(
            "org.user_role", org_id=org, user_id=users[who], role_id=roles[who], source="manual"
        )
    spaces = []
    for code in ("A", "B"):
        space = seed.row(
            "org.data_space",
            org_id=org,
            code=f"DS-{code}-{tag}",
            name=code,
            steward_id=users["analyst"],
        )
        for who in PEOPLE:
            seed.row(
                "org.data_space_member",
                org_id=org,
                data_space_id=space,
                principal_type="user",
                principal_id=users[who],
                role_id=roles[who],
            )
        spaces.append(space)
    source = seed.row(
        "catalog.source",
        org_id=org,
        data_space_id=spaces[0],
        name="Core",
        kind="postgresql",
        secret_ref="kv/data-agent/sources/core",
    )
    dataset = _dataset(seed, org, spaces[0], source, table_name="loan_balance")
    for i, name in enumerate(("branch_code", "balance", "as_of_date")):
        seed.row(
            "catalog.column_meta",
            org_id=org,
            dataset_id=dataset,
            dataset_version=1,
            name=name,
            ordinal=i,
            data_type="text",
            normalized_type="string",
        )
    return World(
        org=org,
        space=spaces[0],
        other_space=spaces[1],
        users=users,
        dataset=dataset,
        source=source,
        tag=tag,
        seed=seed,
    )


@asynccontextmanager
async def api_for(world: World) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(), token_verifier=Tokens(world.org))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as api:
        yield api


def as_(world: World, who: str, space: uuid.UUID | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer kc-{who}-{world.tag}",
        "X-Data-Space": str(space or world.space),
    }


def rows(world: World, statement: str, *params: object) -> list[tuple[Any, ...]]:
    with world.seed.conn.cursor() as cur:
        cur.execute(statement, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
        return cur.fetchall() if cur.description else []


def events(world: World, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
    found = rows(
        world,
        "SELECT event_type, payload FROM query.outbox WHERE aggregate_id = %s"
        " ORDER BY created_at, id",
        aggregate_id,
    )
    return [(t, p if isinstance(p, dict) else json.loads(p)) for t, p in found]


def run_count(world: World) -> int:
    return int(
        rows(world, "SELECT count(*) FROM query.query_run WHERE org_id = %s", world.org)[0][0]
    )


async def test_dataset_run_is_queued_with_its_event(world: World) -> None:
    async with api_for(world) as api:
        created = await api.post(
            "/v1/query-runs",
            headers=as_(world, "analyst"),
            json={
                "dataset_id": str(world.dataset),
                "filters": [
                    {"column": "branch_code", "op": "eq", "value": "HN01"},
                    {"column": "balance", "op": "gte", "value": 1000},
                ],
                "limit": 50,
            },
        )
        assert created.status_code == 202, created.text
        body = created.json()
        assert created.headers["Location"] == f"/v1/query-runs/{body['id']}"
        assert body["status"] == "queued"
        assert body["principal_id"] == str(world.users["analyst"])
        assert body["source_id"] == str(world.source)
        assert body["route"] == "live"
        assert "sql_executed" not in body and "sql_generated" not in body

        [(caller_kind, caller_id, sql_generated, sql_executed)] = rows(
            world,
            "SELECT caller_kind, caller_id, sql_generated, sql_executed FROM query.query_run"
            " WHERE id = %s",
            body["id"],
        )
        assert (caller_kind, str(caller_id)) == ("ad_hoc", str(world.users["analyst"]))
        # The worker builds and rewrites the SQL (BR-45).
        assert (sql_generated, sql_executed) == ("", "")

        [(event_type, payload)] = events(world, body["id"])
        assert event_type == "query_run.requested"
        assert payload["mode"] == "dataset"
        assert payload["dataset_id"] == str(world.dataset)
        assert payload["limit"] == 50
        assert payload["filters"][1] == {"column": "balance", "op": "gte", "value": 1000}
        assert payload["principal_id"] == str(world.users["analyst"])

        # The run is read back as usual; rows come once the worker has written them.
        got = await api.get(f"/v1/query-runs/{body['id']}", headers=as_(world, "analyst"))
        assert got.status_code == 200, got.text
        pending = await api.get(f"/v1/query-runs/{body['id']}/rows", headers=as_(world, "analyst"))
        assert pending.status_code == 409, pending.text


async def test_sql_run_keeps_the_callers_sql_and_rows_come_from_rows(world: World) -> None:
    sql = "select branch_code, sum(balance) from dw.loan_balance group by 1;"
    async with api_for(world) as api:
        created = await api.post(
            "/v1/query-runs",
            headers=as_(world, "analyst"),
            json={"sql": sql, "as_of": "2026-09-30"},
        )
        assert created.status_code == 202, created.text
        body = created.json()
        assert body["route"] == "history" and body["as_of"] == "2026-09-30"
        assert body["source_id"] is None
        [(sql_generated, caller_kind)] = rows(
            world,
            "SELECT sql_generated, caller_kind FROM query.query_run WHERE id = %s",
            body["id"],
        )
        assert (sql_generated, caller_kind) == (sql, "ad_hoc")
        [(_, payload)] = events(world, body["id"])
        assert payload["mode"] == "sql" and payload["dataset_id"] is None

        # What the worker writes once it ran the rewritten SQL.
        rows(
            world,
            "UPDATE query.query_run SET status = 'succeeded', row_count = 1 WHERE id = %s",
            body["id"],
        )
        world.seed.row(
            "query.query_result",
            org_id=world.org,
            query_run_id=uuid.UUID(body["id"]),
            columns=json.dumps([{"name": "branch_code"}, {"name": "sum"}]),
            rows=json.dumps([["HN01", 1200]]),
        )
        read = await api.get(f"/v1/query-runs/{body['id']}/rows", headers=as_(world, "analyst"))
        assert read.status_code == 200, read.text
        assert read.json()["items"] == [["HN01", 1200]]


@pytest.mark.parametrize(
    "sql",
    [
        "delete from dw.loan_balance",
        "select 1; select 2",
        "select 1; drop table dw.loan_balance",
        "select * into dw.copy from dw.loan_balance",
        "with x as (delete from dw.t returning *) select * from x",
        "select * from dw.loan_balance for update",
        "select 'unterminated",
    ],
)
async def test_sql_must_be_one_read_only_statement(world: World, sql: str) -> None:
    async with api_for(world) as api:
        refused = await api.post("/v1/query-runs", headers=as_(world, "analyst"), json={"sql": sql})
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "validation_failed"
    assert run_count(world) == 0


async def test_sql_tricks_inside_literals_and_comments_are_not_statements(world: World) -> None:
    async with api_for(world) as api:
        for sql in (
            "select 'a; drop table t' as note",
            "/* header */ select 1 -- ; delete",
            'select "update" from dw.audit_words',
        ):
            created = await api.post(
                "/v1/query-runs", headers=as_(world, "analyst"), json={"sql": sql}
            )
            assert created.status_code == 202, (sql, created.text)


async def test_body_takes_a_dataset_or_sql(world: World) -> None:
    async with api_for(world) as api:
        for body in (
            {},
            {"dataset_id": str(world.dataset), "sql": "select 1"},
            {"sql": "select 1", "filters": [{"column": "a", "op": "eq", "value": 1}]},
            {"dataset_id": str(world.dataset), "limit": 10_001},
            {
                "dataset_id": str(world.dataset),
                "filters": [{"column": "a", "op": "in", "value": 1}],
            },
            {"dataset_id": str(world.dataset), "extra": True},
        ):
            refused = await api.post("/v1/query-runs", headers=as_(world, "analyst"), json=body)
            assert refused.status_code == 422, (body, refused.text)
        unknown_column = await api.post(
            "/v1/query-runs",
            headers=as_(world, "analyst"),
            json={
                "dataset_id": str(world.dataset),
                "filters": [{"column": "customer_name", "op": "eq", "value": "A"}],
            },
        )
        assert unknown_column.status_code == 422, unknown_column.text
        assert unknown_column.json()["errors"][0]["field"] == "filters"
    assert run_count(world) == 0


async def test_datasets_outside_the_callers_rights_are_not_found(world: World) -> None:
    seed = world.seed
    disabled = _dataset(seed, world.org, world.space, world.source, enabled=False)
    deleted = _dataset(seed, world.org, world.space, world.source, deleted_at="2026-10-01")
    others = _dataset(
        seed, world.org, world.space, None, scope="personal", created_by=world.users["peer"]
    )
    mine = _dataset(
        seed, world.org, world.space, None, scope="personal", created_by=world.users["analyst"]
    )
    elsewhere = _dataset(seed, world.org, world.other_space, None)
    async with api_for(world) as api:
        for dataset in (disabled, deleted, others, elsewhere, uuid.uuid4()):
            refused = await api.post(
                "/v1/query-runs",
                headers=as_(world, "analyst"),
                json={"dataset_id": str(dataset)},
            )
            assert refused.status_code == 404, (dataset, refused.text)
        own = await api.post(
            "/v1/query-runs", headers=as_(world, "analyst"), json={"dataset_id": str(mine)}
        )
        assert own.status_code == 202, own.text
        # The same dataset from its own Data Space runs.
        there = await api.post(
            "/v1/query-runs",
            headers=as_(world, "analyst", world.other_space),
            json={"dataset_id": str(elsewhere)},
        )
        assert there.status_code == 202, there.text
        # The run of Data Space B is not readable from A (A1.2).
        hidden = await api.get(
            f"/v1/query-runs/{there.json()['id']}", headers=as_(world, "analyst")
        )
        assert hidden.status_code == 404


async def test_another_organisation_does_not_see_the_dataset(world: World, seed: Seed) -> None:
    stranger = seed.org()
    tag = stranger.hex[:8]
    role = seed.row(
        "org.role", org_id=stranger, code="Q", name="q", capabilities=["ask", "view_detail"]
    )
    user = seed.row(
        "org.app_user",
        org_id=stranger,
        external_id=f"kc-x-{tag}",
        username="x",
        display_name="x",
        status="active",
    )
    space = seed.row("org.data_space", org_id=stranger, code="DS", name="DS", steward_id=user)
    seed.row(
        "org.data_space_member",
        org_id=stranger,
        data_space_id=space,
        principal_type="user",
        principal_id=user,
        role_id=role,
    )
    app = create_app(Settings(), token_verifier=Tokens(stranger))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as api:
        refused = await api.post(
            "/v1/query-runs",
            headers={"Authorization": f"Bearer kc-x-{tag}", "X-Data-Space": str(space)},
            json={"dataset_id": str(world.dataset)},
        )
        assert refused.status_code == 404, refused.text


async def test_query_runs_need_view_detail(world: World) -> None:
    async with api_for(world) as api:
        denied = await api.post(
            "/v1/query-runs", headers=as_(world, "asker"), json={"dataset_id": str(world.dataset)}
        )
        assert denied.status_code == 403, denied.text
    assert run_count(world) == 0
