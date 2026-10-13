"""Kỹ năng, endpoints added 12/10/2026: POST /v1/skill-versions/{id}:try (a trial of a draft)
and GET /v1/tools (the agent tool register agent.tool_def, tab 5 G2/G2.1), against PostgreSQL
and the V3 grants.

The skill worker is not running: a trial is asserted as its skill_run row and outbox event.
The api suite builds its database from dataagent.sql, not by running alembic, so the tool rows
are loaded here with the upsert of the migration that loads them (v3_0011) and its seed file.
"""

from __future__ import annotations

import ast
import json
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import psycopg2
import psycopg2.errors
import pytest

from libs.dataagent_db import uuid7
from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]

SCHEMA = {
    "type": "object",
    "properties": {
        "period": {"type": "string", "format": "date"},
        "threshold": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["period"],
}
STEPS = [{"kind": "query", "metric": "npl_ratio", "op": "by_region"}]
PEOPLE = {
    # who: (capabilities, member of Data Space A)
    "author": (["ask", "create_skill"], True),
    "author2": (["ask", "create_skill"], True),
    "analyst": (["ask"], True),
    "far": (["ask", "create_skill"], False),
}


@dataclass
class World:
    org: uuid.UUID
    space: uuid.UUID
    other: uuid.UUID
    users: dict[str, uuid.UUID]
    tag: str
    seed: Seed


class Tokens:
    def __init__(self, org: uuid.UUID) -> None:
        self.org = org

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org)}


@pytest.fixture
def world(seed: Seed) -> World:
    org = seed.org()
    tag = org.hex[:8]
    keeper = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-keeper-{tag}",
        username="keeper",
        display_name="Keeper",
    )
    space = seed.row("org.data_space", org_id=org, code="DS-A", name="A", steward_id=keeper)
    other = seed.row("org.data_space", org_id=org, code="DS-B", name="B", steward_id=keeper)
    users = {}
    for who, (caps, in_a) in PEOPLE.items():
        role = seed.row("org.role", org_id=org, code=who.upper(), name=who, capabilities=caps)
        users[who] = seed.row(
            "org.app_user",
            org_id=org,
            external_id=f"kc-{who}-{tag}",
            username=who,
            display_name=who,
            status="active",
        )
        seed.row("org.user_role", org_id=org, user_id=users[who], role_id=role, source="manual")
        seed.row(
            "org.data_space_member",
            org_id=org,
            data_space_id=space if in_a else other,
            principal_type="user",
            principal_id=users[who],
            role_id=role,
        )
    return World(org=org, space=space, other=other, users=users, tag=tag, seed=seed)


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


def one(world: World, statement: str, *params: object) -> Any:
    with world.seed.conn.cursor() as cur:
        cur.execute(statement, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
        return cur.fetchone()


def events(world: World, aggregate_id: object) -> list[tuple[str, dict[str, Any]]]:
    with world.seed.conn.cursor() as cur:
        cur.execute(
            "SELECT event_type, payload FROM skill.outbox WHERE aggregate_id = %s"
            " ORDER BY created_at, id",
            (str(aggregate_id),),
        )
        return [(t, p if isinstance(p, dict) else json.loads(p)) for t, p in cur.fetchall()]


async def draft(api: httpx.AsyncClient, world: World, code: str = "npl-split") -> dict[str, Any]:
    """A personal skill of `author` with one draft version."""
    created = await api.post(
        "/v1/skills",
        headers=as_(world, "author"),
        json={"code": code, "name": "Tách nợ xấu", "category": "decomposition"},
    )
    assert created.status_code == 201, created.text
    version = await api.post(
        f"/v1/skills/{created.json()['id']}/versions",
        headers=as_(world, "author"),
        json={"params_schema": SCHEMA, "steps": STEPS},
    )
    assert version.status_code == 201, version.text
    return version.json()


# ---------------------------------------------------------------- POST /v1/skill-versions/{id}:try


async def test_try_records_a_queued_run_and_its_event(world: World) -> None:
    async with api_for(world) as api:
        version = await draft(api, world)
        tried = await api.post(
            f"/v1/skill-versions/{version['id']}:try",
            headers=as_(world, "author"),
            json={"params": {"period": "2026-09-30", "threshold": 0.05}},
        )
        assert tried.status_code == 202, tried.text
        body = tried.json()
        assert body["status"] == "queued"
        assert tried.headers["Location"] == f"/v1/skill-runs/{body['id']}"

        row = one(
            world,
            "SELECT skill_version_id, data_space_id, caller_kind, principal_id, params, status"
            " FROM skill.skill_run WHERE id = %s",
            body["id"],
        )
        assert row is not None
        assert (str(row[0]), str(row[1]), row[2], str(row[3]), row[5]) == (
            version["id"],
            str(world.space),
            "user",
            str(world.users["author"]),
            "queued",
        )
        assert row[4] == {"period": "2026-09-30", "threshold": 0.05}
        [(event_type, payload)] = events(world, body["id"])
        assert event_type == "skill_run.requested"
        assert payload["mode"] == "try"
        assert payload["skill_version_id"] == version["id"]

        # The library is untouched: the version stays a draft, the skill has no current version.
        state = one(
            world,
            "SELECT v.status, s.current_version, s.run_count_30d FROM skill.skill_version v"
            " JOIN skill.skill s ON s.id = v.skill_id WHERE v.id = %s",
            version["id"],
        )
        assert state == ("draft", None, 0)

        # The runner reads the trial back where Location points.
        run = await api.get(tried.headers["Location"], headers=as_(world, "author"))
        assert run.status_code == 200, run.text
        assert run.json()["status"] == "queued"


async def test_try_refuses_params_outside_the_schema(world: World) -> None:
    async with api_for(world) as api:
        version = await draft(api, world)
        for params in ({}, {"period": "30/09/2026"}, {"period": "2026-09-30", "threshold": 2}):
            tried = await api.post(
                f"/v1/skill-versions/{version['id']}:try",
                headers=as_(world, "author"),
                json={"params": params},
            )
            assert tried.status_code == 422, (params, tried.text)
            assert tried.json()["code"] == "validation_failed"
        missing_body = await api.post(
            f"/v1/skill-versions/{version['id']}:try", headers=as_(world, "author"), json={}
        )
        assert missing_body.status_code == 422
    assert one(world, "SELECT count(*) FROM skill.skill_run WHERE org_id = %s", world.org) == (0,)


async def test_try_needs_a_draft(world: World) -> None:
    async with api_for(world) as api:
        version = await draft(api, world)
        tested = await api.post(
            f"/v1/skill-versions/{version['id']}:test", headers=as_(world, "author")
        )
        assert tested.status_code == 202, tested.text
        tried = await api.post(
            f"/v1/skill-versions/{version['id']}:try",
            headers=as_(world, "author"),
            json={"params": {"period": "2026-09-30"}},
        )
        assert tried.status_code == 409, tried.text
        assert tried.json()["code"] == "invalid_state"


async def test_try_is_the_authors_and_needs_create_skill(world: World) -> None:
    async with api_for(world) as api:
        version = await draft(api, world)
        path = f"/v1/skill-versions/{version['id']}:try"
        params = {"params": {"period": "2026-09-30"}}
        # Without create_skill: the A2 capability gate.
        denied = await api.post(path, headers=as_(world, "analyst"), json=params)
        assert denied.status_code == 403, denied.text
        # Another author of the Data Space does not see a personal skill: 404.
        other = await api.post(path, headers=as_(world, "author2"), json=params)
        assert other.status_code == 404, other.text
        # From another Data Space the version does not exist.
        far = await api.post(path, headers=as_(world, "far", world.other), json=params)
        assert far.status_code == 404, far.text
        unknown = await api.post(
            f"/v1/skill-versions/{uuid.uuid4()}:try", headers=as_(world, "author"), json=params
        )
        assert unknown.status_code == 404


async def test_a_try_is_told_apart_from_a_run_in_the_table(world: World) -> None:
    async with api_for(world) as api:
        version = await draft(api, world)
        tried = await api.post(
            f"/v1/skill-versions/{version['id']}:try",
            headers=as_(world, "author"),
            json={"params": {"period": "2026-09-30"}},
        )
        assert tried.status_code == 202, tried.text
    assert one(world, "SELECT mode FROM skill.skill_run WHERE id = %s", tried.json()["id"]) == (
        "try",
    )
    # The column defaults to a run, so every other writer of skill_run records runs.
    assert one(
        world,
        "SELECT column_default FROM information_schema.columns WHERE table_schema = 'skill'"
        " AND table_name = 'skill_run' AND column_name = 'mode'",
    ) == ("'run'::text",)


# ---------------------------------------------------------------- GET /v1/tools

ALEMBIC = Path(__file__).resolve().parents[3] / "backend" / "alembic"
SEED_FILE = ALEMBIC / "seed" / "tool_def.json"
MIGRATION = ALEMBIC / "versions" / "v3_0011_endpoint_batches_1210.py"
SEED: list[dict[str, Any]] = json.loads(SEED_FILE.read_text(encoding="utf-8"))
TOOLSETS = {"metric_lookup", "explain_change", "document", "forecast", "provenance", "explore_sql"}


def _migration_upsert() -> str:
    """UPSERT_TOOL of v3_0011, read from its source: the api image has no alembic to import it."""
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "UPSERT_TOOL" for target in node.targets
        ):
            return str(ast.literal_eval(node.value))
    raise AssertionError("v3_0011 has no UPSERT_TOOL")


@pytest.fixture(scope="module")
def tools(api_database: str) -> list[dict[str, Any]]:
    """agent.tool_def loaded as v3_0011 loads it, as the admin (only the migration role writes)."""
    upsert = _migration_upsert()
    conn = psycopg2.connect(api_database)
    try:
        with conn, conn.cursor() as cur:
            for tool in SEED:
                cur.execute(upsert, {**tool, "id": str(uuid7())})
    finally:
        conn.close()
    return SEED


def test_the_seed_is_the_59_tools_of_g2_and_meets_tool_defs_checks(
    tools: list[dict[str, Any]], seed: Seed
) -> None:
    # Tab 5 G2: 48 tools plus the 11 of ADR-54; tab 3b: "Dữ liệu khởi tạo: 59 dòng".
    assert len(tools) == 59
    assert len({t["code"] for t in tools}) == 59
    for t in tools:
        code = t["code"]
        assert re.fullmatch(r"[a-z]+(\.[a-z_]+)+", code), code
        assert t["permission_level"] in ("read", "write_derived", "send_external"), code
        assert t["default_checkpoint"] in ("none", "on_condition", "before", "always"), code
        assert t["status"] == "active", code
        assert t["execution_kinds"], code
        assert set(t["execution_kinds"]) <= {"interactive", "plan", "workflow"}, code
        assert set(t["toolsets"]) <= TOOLSETS, code
        assert 0 < len(t["model_description"]) <= 400, code
        if t["permission_level"] == "send_external":
            assert t["default_checkpoint"] == "always" and not t["mcp_exposed"], code
        assert t["use_cases"], code
        assert all(re.fullmatch(r"UC-\d{2}-\d{2}", uc) for uc in t["use_cases"]), code
        if t["mcp_exposed"]:
            assert not code.startswith("exec."), code

    # The rows are in the table, active, as the seed has them: PostgreSQL ran tool_def's CHECKs.
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT code, permission_level, mcp_exposed, default_checkpoint, use_cases"
            " FROM agent.tool_def WHERE status = 'active' ORDER BY code"
        )
        stored = cur.fetchall()
    assert [
        (
            t["code"],
            t["permission_level"],
            t["mcp_exposed"],
            t["default_checkpoint"],
            t["use_cases"],
        )
        for t in sorted(tools, key=lambda t: t["code"])
    ] == [tuple(row) for row in stored]

    # And the CHECKs are there to refuse what G2.1 forbids.
    refused = [
        {"code": "notify.external_mcp", "permission_level": "send_external", "mcp_exposed": True},
        {"code": "exec.exposed", "mcp_exposed": True},
        {"code": "no.use_case", "use_cases": []},
    ]
    for bad in refused:
        row = {
            "permission_level": "read",
            "mcp_exposed": False,
            "use_cases": ["UC-01-01"],
            "default_checkpoint": "always",
            **bad,
        }
        with pytest.raises(psycopg2.errors.CheckViolation), seed.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent.tool_def (id, code, permission_level, owner_service, endpoints,"
                " model_description, execution_kinds, mcp_exposed, default_checkpoint, use_cases)"
                " VALUES (%s, %s, %s, 'Thử', '{}', 'Thử', '{interactive}', %s, %s, %s)",
                (
                    str(uuid.uuid4()),
                    row["code"],
                    row["permission_level"],
                    row["mcp_exposed"],
                    row["default_checkpoint"],
                    row["use_cases"],
                ),
            )


async def test_tools_lists_the_active_rows_of_tool_def(
    world: World, tools: list[dict[str, Any]]
) -> None:
    draft_id = world.seed.row(
        "agent.tool_def",
        code="draft.probe",
        permission_level="read",
        owner_service="Thử",
        endpoints=[],
        model_description="Công cụ nháp của bài kiểm",
        execution_kinds=["interactive"],
        default_checkpoint="none",
        use_cases=[],
        status="draft",
    )
    try:
        async with api_for(world) as api:
            listed = await api.get("/v1/tools", headers=as_(world, "analyst"))
    finally:
        one(world, "DELETE FROM agent.tool_def WHERE id = %s RETURNING id", draft_id)
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    # Every active row, by code; the draft is not a tool an agent may be given.
    codes = [t["code"] for t in items]
    assert codes == sorted(t["code"] for t in tools)
    assert "draft.probe" not in codes
    assert set(items[0]) == {"code", "level", "description", "executed_by", "default_checkpoint"}
    by_code = {t["code"]: t for t in items}
    for tool in tools:
        item = by_code[tool["code"]]
        assert item["description"] == tool["model_description"]
        assert item["executed_by"] == tool["owner_service"]
    # A3's names: send_external is served as external_send; a checkpoint of none is null.
    assert {t["level"] for t in items} == {"read", "write_derived", "external_send"}
    assert by_code["notify.external"]["level"] == "external_send"
    assert by_code["notify.external"]["default_checkpoint"] == "always"
    assert by_code["query.semantic"]["default_checkpoint"] is None
    assert by_code["query.sql"]["default_checkpoint"] == "on_condition"


async def test_tools_filter_by_level(world: World, tools: list[dict[str, Any]]) -> None:
    async with api_for(world) as api:
        external = await api.get("/v1/tools?level=external_send", headers=as_(world, "analyst"))
        assert external.status_code == 200, external.text
        assert sorted(t["code"] for t in external.json()["items"]) == [
            "export.create",
            "notify.external",
            "writeback.request",
        ]
        assert {t["level"] for t in external.json()["items"]} == {"external_send"}
        derived = await api.get("/v1/tools?level=write_derived", headers=as_(world, "analyst"))
        assert sorted(t["code"] for t in derived.json()["items"]) == sorted(
            t["code"] for t in tools if t["permission_level"] == "write_derived"
        )
        system = await api.get("/v1/tools?level=system_action", headers=as_(world, "analyst"))
        assert system.json()["items"] == []
        # The level of tab 3b is not A3's: send_external is not a value of the filter.
        for bad_level in ("admin", "send_external"):
            bad = await api.get(f"/v1/tools?level={bad_level}", headers=as_(world, "analyst"))
            assert bad.status_code == 422


async def test_tools_need_ask(world: World, seed: Seed) -> None:
    role = seed.row(
        "org.role", org_id=world.org, code="UPLOAD", name="upload", capabilities=["upload"]
    )
    user = seed.row(
        "org.app_user",
        org_id=world.org,
        external_id=f"kc-uploader-{world.tag}",
        username="uploader",
        display_name="uploader",
        status="active",
    )
    seed.row("org.user_role", org_id=world.org, user_id=user, role_id=role, source="manual")
    seed.row(
        "org.data_space_member",
        org_id=world.org,
        data_space_id=world.space,
        principal_type="user",
        principal_id=user,
        role_id=role,
    )
    async with api_for(world) as api:
        denied = await api.get("/v1/tools", headers=as_(world, "uploader"))
        assert denied.status_code == 403, denied.text
