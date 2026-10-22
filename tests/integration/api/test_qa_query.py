"""Hỏi đáp and Truy vấn against PostgreSQL 16 and the V3 grants: questions, plans,
conversations, the metric explorer and query runs, with the outbox rows each change writes.
Answers, feedback, exports and receipts are in test_qa_answers.py (same helpers)."""

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
    "asker": ["ask"],
    "reviewer": ["ask", "approve", "view_detail"],
    "voter1": ["ask"],
    "voter2": ["ask"],
    "auditor": ["ask", "view_detail"],
}


@dataclass
class World:
    org: uuid.UUID
    space: uuid.UUID
    other_space: uuid.UUID
    users: dict[str, uuid.UUID]
    tag: str


class Tokens:
    def __init__(self, org: uuid.UUID) -> None:
        self.org = org

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org)}


def build(seed: Seed) -> World:
    """One org, two Data Spaces; every person is a member of both."""
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
            "org.role", org_id=org, code=f"QA-{who}-{tag}", name=who, capabilities=caps
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
            name=f"Space {code}",
            steward_id=users["reviewer"],
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
    return World(org=org, space=spaces[0], other_space=spaces[1], users=users, tag=tag)


@pytest.fixture
def world(seed: Seed) -> World:
    return build(seed)


@asynccontextmanager
async def api_for(world: World, **state: Any) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(Settings(), token_verifier=Tokens(world.org))
    # Ports a test stands in for (e.g. export_links), set on app.state as the deploy would.
    for name, value in state.items():
        setattr(app.state, name, value)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api"
    ) as api:
        yield api


def as_(world: World, who: str, space: uuid.UUID | None = None, **extra: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer kc-{who}-{world.tag}",
        "X-Data-Space": str(space or world.space),
        **extra,
    }


def events(seed: Seed, schema: str, aggregate_id: object) -> list[tuple[str, str, dict[str, Any]]]:
    with seed.conn.cursor() as cur:
        cur.execute(
            f"SELECT event_type, aggregate_type, payload FROM {schema}.outbox"
            " WHERE aggregate_id = %s ORDER BY created_at, id",
            (str(aggregate_id),),
        )
        return [(t, a, p if isinstance(p, dict) else json.loads(p)) for t, a, p in cur.fetchall()]


def sql(seed: Seed, statement: str, *params: object) -> list[tuple[Any, ...]]:
    with seed.conn.cursor() as cur:
        cur.execute(statement, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
        return cur.fetchall() if cur.description else []


def jsonb(value: object) -> str:
    return json.dumps(value, default=str)


async def ask(
    api: httpx.AsyncClient, world: World, who: str = "asker", **body: Any
) -> dict[str, Any]:
    body.setdefault("text", "Doanh thu tháng 9?")
    created = await api.post("/v1/questions", headers=as_(world, who), json=body)
    assert created.status_code == 202, created.text
    return created.json()


def plan_for(seed: Seed, world: World, question_id: object, status: str = "answered") -> uuid.UUID:
    """What the worker would have written: a first plan, and the question's new status."""
    plan = seed.row(
        "qa.plan",
        org_id=world.org,
        question_id=uuid.UUID(str(question_id)),
        version=1,
        chips=jsonb([{"kind": "metric", "value": "NPL_RATIO", "origin": "default"}]),
    )
    sql(seed, "UPDATE qa.question SET status = %s WHERE id = %s", status, question_id)
    return plan


# --- questions ----------------------------------------------------------------------------------


async def test_a_question_is_recorded_for_the_worker(world: World, seed: Seed) -> None:
    async with api_for(world) as api:
        created = await api.post(
            "/v1/questions",
            headers=as_(world, "asker"),
            json={"text": "Doanh thu tháng 9?", "as_of": "2026-09-30", "stream": True},
        )
        assert created.status_code == 202, created.text
        body = created.json()
        assert created.headers["location"] == f"/v1/questions/{body['id']}"
        assert body["status"] == "received" and body["question_id"] == body["id"]
        assert body["stream_url"] == f"/v1/questions/{body['id']}/stream"
        [(event, aggregate, payload)] = events(seed, "qa", body["id"])
        assert (event, aggregate) == ("question.received", "question")
        assert payload["status"] == "received"
        assert payload["data_space_id"] == str(world.space)
        assert "text" not in payload

        question = await api.get(f"/v1/questions/{body['id']}", headers=as_(world, "asker"))
        assert question.status_code == 200, question.text
        view = question.json()
        assert view["status"] == "received" and view["seq"] == 1
        assert view["as_of"] == "2026-09-30" and view["plan"] is None and view["answer_id"] is None
        assert "org_id" not in view

        # Another Data Space, or another organisation, does not see it.
        elsewhere = await api.get(
            f"/v1/questions/{body['id']}", headers=as_(world, "asker", world.other_space)
        )
        assert elsewhere.status_code == 404
    other = build(seed)
    async with api_for(other) as other_api:
        foreign = await other_api.get(f"/v1/questions/{body['id']}", headers=as_(other, "asker"))
        assert foreign.status_code == 404


async def test_knowledge_at_is_required_in_at_mode(world: World) -> None:
    async with api_for(world) as api:
        refused = await api.post(
            "/v1/questions",
            headers=as_(world, "asker"),
            json={"text": "Dư nợ?", "knowledge_mode": "at"},
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["code"] == "business_rule_violated"
        accepted = await api.post(
            "/v1/questions",
            headers=as_(world, "asker"),
            json={"text": "Dư nợ?", "knowledge_mode": "at", "knowledge_at": "2026-07-01T00:00:00Z"},
        )
        assert accepted.status_code == 202, accepted.text


@pytest.mark.req("AC-01-04-01")
async def test_a_follow_up_joins_the_parent_conversation(world: World) -> None:
    async with api_for(world) as api:
        first = await ask(api, world)
        follow = await ask(
            api, world, text="còn theo vùng thì sao?", parent_question_id=first["id"]
        )
        assert follow["conversation_id"] == first["conversation_id"]
        view = (await api.get(f"/v1/questions/{follow['id']}", headers=as_(world, "asker"))).json()
        assert view["seq"] == 2 and view["parent_question_id"] == first["id"]

        # Someone else's question, or a conversation of another Data Space, is not a parent.
        stolen = await api.post(
            "/v1/questions",
            headers=as_(world, "voter1"),
            json={"text": "x", "parent_question_id": first["id"]},
        )
        assert stolen.status_code == 422 and stolen.json()["code"] == "validation_failed"
        wrong_space = await api.post(
            "/v1/questions",
            headers=as_(world, "asker", world.other_space),
            json={"text": "x", "conversation_id": first["conversation_id"]},
        )
        assert wrong_space.status_code == 422


async def test_the_stream_sends_recorded_state_and_resumes(world: World, seed: Seed) -> None:
    async with api_for(world) as api:
        created = await ask(api, world)
        url = f"/v1/questions/{created['id']}/stream"
        first = await api.get(url, headers=as_(world, "asker"))
        assert first.status_code == 200, first.text
        assert first.headers["content-type"].startswith("text/event-stream")
        assert "event: progress" in first.text and "event: done" not in first.text

        plan = plan_for(seed, world, created["id"], status="answered")
        answer = seed.row(
            "qa.answer",
            org_id=world.org,
            question_id=uuid.UUID(created["id"]),
            plan_id=plan,
            text="Doanh thu 10 tỷ.",
        )
        resumed = await api.get(url, headers=as_(world, "asker", **{"Last-Event-ID": "2"}))
        assert "event: progress" not in resumed.text
        assert "event: understood" in resumed.text
        assert f'"answer_id":"{answer}"' in resumed.text
        assert "event: done" in resumed.text

        missing = await api.get(f"/v1/questions/{uuid.uuid4()}/stream", headers=as_(world, "asker"))
        assert missing.status_code == 404


# --- plans --------------------------------------------------------------------------------------


@pytest.mark.req("AC-01-03-01")
async def test_editing_chips_versions_the_plan_and_requeues(world: World, seed: Seed) -> None:
    async with api_for(world) as api:
        created = await ask(api, world)
        url = f"/v1/questions/{created['id']}/plan"
        chips = [
            {"kind": "metric", "value": "NPL_RATIO", "origin": "default"},
            {"kind": "dimension", "value": "region"},
        ]
        early = await api.patch(url, headers=as_(world, "asker"), json={"chips": chips})
        assert early.status_code == 409 and early.json()["code"] == "invalid_state"

        first_plan = plan_for(seed, world, created["id"], status="answered")
        edited = await api.patch(
            url, headers=as_(world, "asker"), json={"chips": chips, "edit_note": "theo vùng"}
        )
        assert edited.status_code == 202, edited.text
        assert edited.headers["location"] == f"/v1/questions/{created['id']}"
        body = edited.json()
        assert body["version"] == 2 and body["status"] == "planning"

        view = (await api.get(f"/v1/questions/{created['id']}", headers=as_(world, "asker"))).json()
        assert view["status"] == "planning"
        assert view["plan"]["version"] == 2 and view["plan"]["edited_by_user"] is True
        assert view["plan"]["chips"][0]["origin"] == "default"
        assert view["plan"]["chips"][1]["origin"] == "user"
        [(event, aggregate, payload)] = events(seed, "qa", body["plan_id"])
        assert (event, aggregate) == ("plan.edited", "plan")
        assert payload["previous_plan_id"] == str(first_plan)
        assert payload["question_status"] == "planning"

        # Running again: no second edit until the worker has answered.
        again = await api.patch(url, headers=as_(world, "asker"), json={"chips": chips})
        assert again.status_code == 409
        # Only the asker edits the plan of their question.
        sql(seed, "UPDATE qa.question SET status = 'answered' WHERE id = %s", created["id"])
        other = await api.patch(url, headers=as_(world, "reviewer"), json={"chips": chips})
        assert other.status_code == 404


# --- conversations ------------------------------------------------------------------------------


async def test_conversations_are_mine_filtered_and_paged(world: World, seed: Seed) -> None:
    async with api_for(world) as api:
        mine = [await ask(api, world, text=f"Câu {i}") for i in range(3)]
        await ask(api, world, who="voter1")
        sql(
            seed,
            "UPDATE qa.conversation SET title = 'Nợ xấu theo vùng' WHERE id = %s",
            mine[0]["conversation_id"],
        )
        first = await api.get(
            "/v1/conversations", headers=as_(world, "asker"), params={"page_size": 2}
        )
        assert first.status_code == 200, first.text
        page = first.json()
        assert len(page["items"]) == 2 and page["next_page_token"]
        second = await api.get(
            "/v1/conversations",
            headers=as_(world, "asker"),
            params={"page_size": 2, "page_token": page["next_page_token"]},
        )
        ids = [c["id"] for c in page["items"] + second.json()["items"]]
        assert sorted(ids) == sorted(q["conversation_id"] for q in mine)
        assert second.json()["next_page_token"] == ""

        found = await api.get(
            "/v1/conversations", headers=as_(world, "asker"), params={"q": "nợ xấu"}
        )
        assert [c["id"] for c in found.json()["items"]] == [mine[0]["conversation_id"]]
        future = await api.get(
            "/v1/conversations",
            headers=as_(world, "asker"),
            params={"from": "2100-01-01T00:00:00Z"},
        )
        assert future.json()["items"] == []
        other_space = await api.get(
            "/v1/conversations", headers=as_(world, "asker", world.other_space)
        )
        assert other_space.json()["items"] == []


async def test_conversation_detail_lists_turns_in_seq(world: World, seed: Seed) -> None:
    async with api_for(world) as api:
        first = await ask(api, world, text="Câu đầu")
        second = await ask(
            api,
            world,
            text="Câu tiếp",
            conversation_id=first["conversation_id"],
        )
        assert first["conversation_id"] == second["conversation_id"]
        detail = await api.get(
            f"/v1/conversations/{first['conversation_id']}",
            headers=as_(world, "asker"),
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["id"] == first["conversation_id"]
        assert [t["seq"] for t in body["turns"]] == [1, 2]
        assert [t["question_id"] for t in body["turns"]] == [
            first["question_id"],
            second["question_id"],
        ]
        assert body["turns"][0]["text"] == "Câu đầu"
        assert body["turns"][1]["text"] == "Câu tiếp"
        # ask() leaves questions received unless the worker answers; answer_id stays null.
        assert all(t["answer_id"] is None for t in body["turns"])

        missing = await api.get(
            f"/v1/conversations/{uuid.uuid4()}",
            headers=as_(world, "asker"),
        )
        assert missing.status_code == 404
        other = await api.get(
            f"/v1/conversations/{first['conversation_id']}",
            headers=as_(world, "voter1"),
        )
        assert other.status_code == 404
        other_space = await api.get(
            f"/v1/conversations/{first['conversation_id']}",
            headers=as_(world, "asker", world.other_space),
        )
        assert other_space.status_code == 404


# --- explorer and query runs --------------------------------------------------------------------


def metric(seed: Seed, world: World, dimensions: list[uuid.UUID]) -> tuple[uuid.UUID, uuid.UUID]:
    metric_id = seed.row(
        "context.metric",
        org_id=world.org,
        data_space_id=world.space,
        code=f"NPL_{uuid.uuid4().hex[:6]}",
        owner_id=world.users["reviewer"],
        current_version=1,
        status="active",
    )
    version_id = seed.row(
        "context.metric_version",
        org_id=world.org,
        metric_id=metric_id,
        version=1,
        display_name="Nợ xấu (%)",
        expression="sum(npl) / sum(loans)",
        period_semantics="end_of_period",
        allowed_dimensions="{" + ",".join(str(d) for d in dimensions) + "}",
    )
    return metric_id, version_id


async def test_the_explorer_checks_dimensions_and_queues_a_run(world: World, seed: Seed) -> None:
    region, product = uuid.uuid4(), uuid.uuid4()
    metric_id, version_id = metric(seed, world, [region])
    async with api_for(world) as api:
        queued = await api.post(
            "/v1/explore/queries",
            headers=as_(world, "asker"),
            json={
                "metric_ids": [str(metric_id)],
                "dimensions": [str(region)],
                "filters": [{"dimension_id": str(region), "op": "in", "values": ["Bắc", "Nam"]}],
                "as_of": "2026-09-30",
            },
        )
        assert queued.status_code == 202, queued.text
        run = queued.json()
        assert queued.headers["location"] == f"/v1/query-runs/{run['id']}"
        assert run["status"] == "queued" and run["metric_version_ids"] == [str(version_id)]
        assert "sql_generated" not in run
        [(event, _, payload)] = events(seed, "query", run["id"])
        assert event == "query_run.requested"
        assert payload["dimensions"] == [str(region)] and payload["metric_ids"] == [str(metric_id)]

        bad_dimension = await api.post(
            "/v1/explore/queries",
            headers=as_(world, "asker"),
            json={"metric_ids": [str(metric_id)], "dimensions": [str(product)]},
        )
        assert bad_dimension.status_code == 422
        assert bad_dimension.json()["code"] == "business_rule_violated"
        unknown = await api.post(
            "/v1/explore/queries",
            headers=as_(world, "asker"),
            json={"metric_ids": [str(uuid.uuid4())]},
        )
        assert unknown.status_code == 422 and unknown.json()["code"] == "validation_failed"
        elsewhere = await api.post(
            "/v1/explore/queries",
            headers=as_(world, "asker", world.other_space),
            json={"metric_ids": [str(metric_id)]},
        )
        assert elsewhere.status_code == 422


def query_run(
    seed: Seed, world: World, *, status: str = "succeeded", row_count: int = 3, by: str = "reviewer"
) -> uuid.UUID:
    return seed.row(
        "query.query_run",
        org_id=world.org,
        data_space_id=world.space,
        caller_kind="question",
        caller_id=uuid.uuid4(),
        principal_id=world.users[by],
        route="live",
        sql_generated="SELECT region, npl FROM t",
        sql_executed="SELECT region, npl FROM t WHERE /* policy: branch scope */ true",
        row_count=row_count,
        status=status,
    )


@pytest.mark.req("AC-01-02-06", "AC-01-02-07")
async def test_query_run_sql_and_rows(world: World, seed: Seed) -> None:
    run = query_run(seed, world)
    seed.row(
        "query.query_result",
        org_id=world.org,
        query_run_id=run,
        columns=jsonb([{"name": "region"}, {"name": "npl"}]),
        rows=jsonb([["Bắc", "1.2"], ["Trung", "2.1"], ["Nam", "0.9"]]),
    )
    async with api_for(world) as api:
        got = await api.get(f"/v1/query-runs/{run}", headers=as_(world, "asker"))
        assert got.status_code == 200, got.text
        assert got.json()["status"] == "succeeded" and "sql_executed" not in got.json()
        hidden = await api.get(
            f"/v1/query-runs/{run}", headers=as_(world, "asker", world.other_space)
        )
        assert hidden.status_code == 404

        denied = await api.get(f"/v1/query-runs/{run}/sql", headers=as_(world, "asker"))
        assert denied.status_code == 403
        shown = await api.get(f"/v1/query-runs/{run}/sql", headers=as_(world, "reviewer"))
        assert shown.status_code == 200, shown.text
        assert shown.json()["sql_executed"].endswith("true") and shown.json()["sql_generated"]

        page = await api.get(
            f"/v1/query-runs/{run}/rows", headers=as_(world, "reviewer"), params={"page_size": 2}
        )
        assert page.status_code == 200, page.text
        assert page.json()["items"] == [["Bắc", "1.2"], ["Trung", "2.1"]]
        rest = await api.get(
            f"/v1/query-runs/{run}/rows",
            headers=as_(world, "reviewer"),
            params={"page_size": 2, "cursor": page.json()["next_page_token"]},
        )
        assert rest.json()["items"] == [["Nam", "0.9"]] and rest.json()["next_page_token"] == ""
    audits = [p["action"] for e, _, p in events(seed, "query", run) if e == "audit.requested"]
    assert audits == ["view_sql", "view_rows", "view_rows"]


async def test_rows_are_served_only_to_whoever_ran_the_query(world: World, seed: Seed) -> None:
    # The stored rows are masked under the runner's policy; another member of the Data Space,
    # even with view_detail, would see what that policy let through, so they are refused.
    run = query_run(seed, world, by="reviewer")
    seed.row(
        "query.query_result",
        org_id=world.org,
        query_run_id=run,
        columns=jsonb([{"name": "customer"}]),
        rows=jsonb([["Nguyễn Văn A"]]),
    )
    async with api_for(world) as api:
        own = await api.get(f"/v1/query-runs/{run}/rows", headers=as_(world, "reviewer"))
        assert own.status_code == 200, own.text
        other = await api.get(f"/v1/query-runs/{run}/rows", headers=as_(world, "auditor"))
        assert other.status_code == 404 and other.json()["code"] == "not_found"
        assert "Nguyễn" not in other.text
    audits = [p["action"] for e, _, p in events(seed, "query", run) if e == "audit.requested"]
    assert audits == ["view_rows"]


async def test_rows_of_an_unfinished_or_expired_run(world: World, seed: Seed) -> None:
    running = query_run(seed, world, status="running")
    expired = query_run(seed, world)
    seed.row(
        "query.query_result",
        org_id=world.org,
        query_run_id=expired,
        columns=jsonb([]),
        rows=jsonb([]),
        expires_at="2026-01-01T00:00:00Z",
    )
    async with api_for(world) as api:
        busy = await api.get(f"/v1/query-runs/{running}/rows", headers=as_(world, "reviewer"))
        assert busy.status_code == 409 and busy.json()["code"] == "invalid_state"
        gone = await api.get(f"/v1/query-runs/{expired}/rows", headers=as_(world, "reviewer"))
        assert gone.status_code == 410 and gone.json()["code"] == "gone"
