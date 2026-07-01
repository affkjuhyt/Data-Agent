"""Integration tests for Workflow Memory + Schema Memory (doc §3.7,
docs/STM_LTM_Memory_Architecture.docx — the two LTM layers a data task
platform needs on top of generic conversational memory).

Covers:
  1. Migration c3d4e5f6a7b8 — shape (columns/constraint), RLS policy, clean
     downgrade/re-upgrade. Same asyncpg-subprocess pattern as
     test_alembic_migrations.py, because that's the driver production
     actually runs migrations through.
  2. services/workflow-runtime/workflow_memory.py — record/retrieve against
     a real Postgres + Qdrant testcontainer.
  3. routers/schema_memory.py — the Admin CRUD endpoints, via a bare FastAPI
     TestClient (no app.py lifespan — that needs Mongo/Redis/Kafka/Qdrant
     all up, which is unrelated to what this router does).
  4. nodes/sandbox/tool_executor/handlers/data.py — _text_to_sql_handler's
     wiring (Schema Memory retrieval → prompt → real SQL execution → retry
     on error), with litellm.completion monkeypatched.

No real LLM/embedding provider is contacted anywhere in this file —
`workflow_memory._embed` is monkeypatched to a fixed vector. That isolates
the module's own logic (PG<->Qdrant join by point_id, task_type/outcome
filtering, recency*duration scoring) from embedding-provider availability,
per the project's "don't test against a real LLM" convention.
"""
from __future__ import annotations

import importlib
import json
import os
import pathlib
import subprocess
import sys
import uuid
from typing import Iterator
from unittest.mock import MagicMock

import psycopg2
import psycopg2.errors
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic" / "alembic.ini"


def _async_dsn(libpq_dsn: str) -> str:
    assert libpq_dsn.startswith("postgresql://")
    return "postgresql+asyncpg://" + libpq_dsn[len("postgresql://"):]


def _run_alembic(dsn: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": _async_dsn(dsn), "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=60,
    )


@pytest.fixture
def fresh_db(postgres_container) -> Iterator[dict[str, str]]:
    """Mirrors test_alembic_migrations.py's fixture of the same name —
    wipe public schema so each test gets a deterministic fresh DB without
    paying for a new container."""
    conn = psycopg2.connect(postgres_container["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cur.execute("CREATE SCHEMA public")
            cur.execute("GRANT ALL ON SCHEMA public TO public")
            cur.execute("DROP ROLE IF EXISTS app_user")
    finally:
        conn.close()
    yield postgres_container


@pytest.fixture
def migrated_db(fresh_db) -> dict[str, str]:
    result = _run_alembic(fresh_db["dsn"], "upgrade", "head")
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"
    return fresh_db


def _seed_tenant_and_agent(dsn: str) -> tuple[str, str]:
    tenant_id, agent_id = str(uuid.uuid4()), str(uuid.uuid4())
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO "Tenants" (id, name, slug, data_residency) VALUES (%s, %s, %s, %s)""",
                (tenant_id, "WM Test Tenant", f"wm-test-{tenant_id[:8]}", "Asia-SE1"),
            )
            cur.execute(
                """INSERT INTO "Agents" (id, tenant_id, name) VALUES (%s, %s, %s)""",
                (agent_id, tenant_id, "WM Test Agent"),
            )
    finally:
        conn.close()
    return tenant_id, agent_id


# ─── 1. Migration shape ─────────────────────────────────────────────────

def test_workflow_and_schema_memory_tables_created(migrated_db):
    conn = psycopg2.connect(migrated_db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name IN ('WorkflowMemory', 'SchemaMemory')
                ORDER BY table_name
            """)
            tables = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    assert tables == ["SchemaMemory", "WorkflowMemory"]


def test_schema_memory_unique_constraint_blocks_duplicate_table(migrated_db):
    tenant_id, _ = _seed_tenant_and_agent(migrated_db["dsn"])
    conn = psycopg2.connect(migrated_db["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO "SchemaMemory"
                       (id, tenant_id, data_source, table_name, column_metadata, business_description)
                   VALUES (%s, %s, 'postgresql', 'orders', '[]'::jsonb, 'orders table')""",
                (str(uuid.uuid4()), tenant_id),
            )
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cur.execute(
                    """INSERT INTO "SchemaMemory"
                           (id, tenant_id, data_source, table_name, column_metadata, business_description)
                       VALUES (%s, %s, 'postgresql', 'orders', '[]'::jsonb, 'dup')""",
                    (str(uuid.uuid4()), tenant_id),
                )
    finally:
        conn.close()


def test_rls_tenant_isolation_policy_exists_on_new_tables(migrated_db):
    conn = psycopg2.connect(migrated_db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.relname, p.polname
                FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
                WHERE c.relname IN ('WorkflowMemory', 'SchemaMemory') AND p.polname = 'tenant_isolation'
                ORDER BY c.relname
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    assert rows == [("SchemaMemory", "tenant_isolation"), ("WorkflowMemory", "tenant_isolation")]


def test_downgrade_drops_new_tables_then_reupgrade_is_clean(migrated_db):
    # Target the revision *before* c3d4e5f6a7b8 explicitly rather than "-1"
    # from head — head has since moved past it (d4e5f6a7b8c9 schema_catalog,
    # e5f6a7b8c9d0 microsoft_sso_approvals), same staleness this revision's
    # own docstring flags about a1b2c3d4e5f6 previously.
    down = _run_alembic(migrated_db["dsn"], "downgrade", "b2c3d4e5f6a7")
    assert down.returncode == 0, f"downgrade failed:\n{down.stdout}\n{down.stderr}"

    conn = psycopg2.connect(migrated_db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='public' AND table_name IN ('WorkflowMemory','SchemaMemory')
            """)
            assert cur.fetchall() == []
    finally:
        conn.close()

    up = _run_alembic(migrated_db["dsn"], "upgrade", "head")
    assert up.returncode == 0, f"re-upgrade failed:\n{up.stdout}\n{up.stderr}"


# ─── 2. workflow_memory.py module — real PG + Qdrant, fake embedding ──────

@pytest.fixture
async def wm(migrated_db, qdrant_container, monkeypatch):
    """Import workflow_memory.py the way the container does (top-level
    import, service dir on sys.path), point it at the migrated test DB +
    test Qdrant, and stub the embedding call to a fixed vector."""
    monkeypatch.setenv("EMBEDDING_DIM", "8")
    monkeypatch.setenv("QDRANT_URL", qdrant_container["url"])

    svc_dir = str(REPO_ROOT / "services" / "workflow-runtime")
    added = [p for p in (svc_dir, str(REPO_ROOT)) if p not in sys.path]
    sys.path[:0] = added
    try:
        import workflow_memory as wm_mod
        importlib.reload(wm_mod)  # pick up patched EMBEDDING_DIM/QDRANT_URL

        async def _fake_embed(text: str):
            # Deterministic-but-distinct-enough vector per text so cosine
            # similarity actually varies between different task_descriptions.
            h = abs(hash(text))
            return [((h >> (i * 4)) % 97) / 97.0 for i in range(8)]

        monkeypatch.setattr(wm_mod, "_embed", _fake_embed)

        from services.database_connector.postgres_connector import client
        client.set_url(migrated_db["dsn"])
        await client.open()
        await wm_mod.init_workflow_qdrant()

        tenant_id, agent_id = _seed_tenant_and_agent(migrated_db["dsn"])
        yield wm_mod, tenant_id, agent_id

        await wm_mod.close_workflow_qdrant()
        await client.close()
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)


async def test_record_then_retrieve_workflow_memory_round_trip(wm):
    wm_mod, tenant_id, agent_id = wm

    await wm_mod.record_workflow_outcome(
        tenant_id=tenant_id, agent_id=agent_id, task_type="sql_query",
        task_description="doanh thu theo tháng quý 1",
        param_snapshot={"filters": {"quarter": "Q1"}}, outcome="success", duration_ms=1200,
    )

    results = await wm_mod.retrieve_workflow_memory(
        tenant_id=tenant_id, agent_id=agent_id, task_type="sql_query",
        query="doanh thu theo tháng quý 1",
    )

    assert len(results) == 1
    assert results[0]["task_description"] == "doanh thu theo tháng quý 1"
    assert results[0]["outcome"] == "success"


async def test_retrieve_workflow_memory_excludes_failed_outcome(wm):
    wm_mod, tenant_id, agent_id = wm

    await wm_mod.record_workflow_outcome(
        tenant_id=tenant_id, agent_id=agent_id, task_type="sql_query",
        task_description="doanh thu theo tháng quý 1",
        param_snapshot={}, outcome="failed", duration_ms=500,
    )

    results = await wm_mod.retrieve_workflow_memory(
        tenant_id=tenant_id, agent_id=agent_id, task_type="sql_query",
        query="doanh thu theo tháng quý 1",
    )

    assert results == [], "doc §3.7/§IV: only outcome=success runs should be retrievable"


async def test_retrieve_workflow_memory_excludes_other_task_type(wm):
    wm_mod, tenant_id, agent_id = wm

    await wm_mod.record_workflow_outcome(
        tenant_id=tenant_id, agent_id=agent_id, task_type="report_gen",
        task_description="doanh thu theo tháng quý 1",
        param_snapshot={}, outcome="success", duration_ms=500,
    )

    results = await wm_mod.retrieve_workflow_memory(
        tenant_id=tenant_id, agent_id=agent_id, task_type="sql_query",
        query="doanh thu theo tháng quý 1",
    )

    assert results == [], "a report_gen run must not leak into sql_query retrieval"


async def test_retrieve_workflow_memory_isolates_tenants(wm):
    wm_mod, tenant_id, agent_id = wm
    other_tenant_id, other_agent_id = _seed_tenant_and_agent(_dsn_from_client())

    await wm_mod.record_workflow_outcome(
        tenant_id=other_tenant_id, agent_id=other_agent_id, task_type="sql_query",
        task_description="doanh thu theo tháng quý 1",
        param_snapshot={}, outcome="success", duration_ms=500,
    )

    results = await wm_mod.retrieve_workflow_memory(
        tenant_id=tenant_id, agent_id=agent_id, task_type="sql_query",
        query="doanh thu theo tháng quý 1",
    )

    assert results == [], "a different tenant's successful run must not leak across tenants"


def _dsn_from_client() -> str:
    from services.database_connector.postgres_connector import client
    return client._url.replace("postgresql+asyncpg://", "postgresql://")


async def test_retrieve_schema_memory_exact_match_and_format(wm):
    wm_mod, tenant_id, _ = wm
    from basemodel.services_databaseconnector.postgres_orm.workflow_orm import SchemaMemoryORM
    from services.database_connector.postgres_connector import client
    from uuid import UUID

    async with client.get_client(tenant_id=tenant_id) as session:
        session.add(SchemaMemoryORM(
            id=uuid.uuid4(), tenant_id=UUID(tenant_id), data_source="postgresql", table_name="orders",
            column_metadata=[
                {"name": "id", "type": "uuid", "is_nullable": False},
                {"name": "total", "type": "numeric", "description": "order total", "is_nullable": False},
            ],
            business_description="Customer orders", access_roles=["finance_analyst"],
        ))
        session.add(SchemaMemoryORM(
            id=uuid.uuid4(), tenant_id=UUID(tenant_id), data_source="snowflake", table_name="other_ds_table",
            column_metadata=[], business_description="should not be returned",
        ))
        await session.commit()

    tables = await wm_mod.retrieve_schema_memory(tenant_id, "postgresql")

    assert [t["table_name"] for t in tables] == ["orders"]

    ddl = wm_mod.format_schema_context(tables)
    assert "TABLE orders" in ddl
    assert "total numeric NOT NULL -- order total" in ddl
    assert "Customer orders" in ddl


# ─── 3. routers/schema_memory.py — Admin CRUD, bare FastAPI app ───────────

@pytest.fixture
def schema_memory_client(migrated_db):
    """Bare FastAPI app with only schema_memory.router mounted — no app.py
    lifespan (that needs Mongo/Redis/Kafka/Qdrant up, none of which this
    router touches)."""
    svc_dir = str(REPO_ROOT / "services" / "workflow-runtime")
    added = [p for p in (svc_dir, str(REPO_ROOT)) if p not in sys.path]
    sys.path[:0] = added
    try:
        import importlib as _il
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from services.database_connector.postgres_connector import client as pg_client

        schema_memory_mod = _il.import_module("routers.schema_memory")
        _il.reload(schema_memory_mod)

        # The pg_client's AsyncEngine must be created on the SAME event loop
        # that serves requests. TestClient runs requests through its own
        # anyio portal loop, so opening pg_client via a separate
        # run_until_complete() beforehand binds it to the wrong loop —
        # asyncpg then raises "attached to a different loop". Using
        # startup/shutdown events instead defers connection setup until
        # TestClient's own loop is the one driving it.
        app = FastAPI()
        app.include_router(schema_memory_mod.router)

        @app.on_event("startup")
        async def _startup():
            pg_client.set_url(migrated_db["dsn"])
            await pg_client.open()

        @app.on_event("shutdown")
        async def _shutdown():
            await pg_client.close()

        tenant_id, _ = _seed_tenant_and_agent(migrated_db["dsn"])

        with TestClient(app) as tc:
            yield tc, tenant_id
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)


def test_schema_memory_router_upsert_then_list(schema_memory_client):
    tc, tenant_id = schema_memory_client
    headers = {"X-Tenant-ID": tenant_id}

    resp = tc.put("/api/schema-memory", headers=headers, json={
        "data_source": "postgresql",
        "table_name": "orders",
        "column_metadata": [{"name": "id", "type": "uuid", "is_nullable": False}],
        "business_description": "Customer orders",
        "access_roles": ["finance_analyst"],
    })
    assert resp.status_code == 200, resp.text
    row_id = resp.json()["id"]

    resp = tc.get("/api/schema-memory", headers=headers, params={"data_source": "postgresql"})
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["id"] == row_id
    assert rows[0]["table_name"] == "orders"


def test_schema_memory_router_upsert_is_idempotent_on_same_table(schema_memory_client):
    """PUT twice for the same (data_source, table_name) must update in place,
    not create a second row — this is how Admin re-syncs a changed schema."""
    tc, tenant_id = schema_memory_client
    headers = {"X-Tenant-ID": tenant_id}
    body = {
        "data_source": "postgresql", "table_name": "orders",
        "column_metadata": [], "business_description": "v1", "access_roles": [],
    }

    first = tc.put("/api/schema-memory", headers=headers, json=body)
    second = tc.put("/api/schema-memory", headers=headers, json={**body, "business_description": "v2"})
    assert first.json()["id"] == second.json()["id"]

    rows = tc.get("/api/schema-memory", headers=headers).json()
    assert len(rows) == 1
    assert rows[0]["business_description"] == "v2"


def test_schema_memory_router_delete(schema_memory_client):
    tc, tenant_id = schema_memory_client
    headers = {"X-Tenant-ID": tenant_id}

    row_id = tc.put("/api/schema-memory", headers=headers, json={
        "data_source": "postgresql", "table_name": "orders",
        "column_metadata": [], "business_description": "x", "access_roles": [],
    }).json()["id"]

    resp = tc.delete(f"/api/schema-memory/{row_id}", headers=headers)
    assert resp.status_code == 200

    rows = tc.get("/api/schema-memory", headers=headers).json()
    assert rows == []


def test_schema_memory_router_delete_unknown_id_returns_404(schema_memory_client):
    tc, tenant_id = schema_memory_client
    resp = tc.delete(f"/api/schema-memory/{uuid.uuid4()}", headers={"X-Tenant-ID": tenant_id})
    assert resp.status_code == 404


# ─── 4. _text_to_sql_handler wiring — real Postgres, litellm.completion mocked ─
#
# The SQL-generation CALL is mocked (no real LLM) — what's under test is
# everything around it: does Schema Memory actually get retrieved and
# rendered into the prompt, does the generated SQL actually execute against
# a real database, does the retry-on-error path actually retry with the
# previous error appended to the prompt.

@pytest.fixture
async def text_to_sql_env(migrated_db, monkeypatch):
    svc_dir = str(REPO_ROOT / "services" / "workflow-runtime")
    added = [p for p in (svc_dir, str(REPO_ROOT)) if p not in sys.path]
    sys.path[:0] = added
    try:
        import importlib as _il
        from services.database_connector.postgres_connector import client as pg_client

        data_mod = _il.import_module("nodes.sandbox.tool_executor.handlers.data")
        _il.reload(data_mod)

        pg_client.set_url(migrated_db["dsn"])
        await pg_client.open()

        tenant_id, _ = _seed_tenant_and_agent(migrated_db["dsn"])

        # The "data source" table text_to_sql will query — a plain table in
        # the same throwaway Postgres, separate from the app's own schema.
        conn = psycopg2.connect(migrated_db["dsn"])
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE demo_orders (id serial PRIMARY KEY, total numeric NOT NULL)")
            cur.execute("INSERT INTO demo_orders (total) VALUES (100), (250)")
        conn.close()

        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import SchemaMemoryORM
        from uuid import UUID
        async with pg_client.get_client(tenant_id=tenant_id) as session:
            session.add(SchemaMemoryORM(
                id=uuid.uuid4(), tenant_id=UUID(tenant_id), data_source="postgresql", table_name="demo_orders",
                column_metadata=[
                    {"name": "id", "type": "int", "is_nullable": False},
                    {"name": "total", "type": "numeric", "description": "order total", "is_nullable": False},
                ],
                business_description="Demo orders table",
            ))
            await session.commit()

        yield data_mod, migrated_db["dsn"], tenant_id

        await pg_client.close()
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)


def _fake_sql_response(sql: str):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=sql))]
    return resp


async def test_text_to_sql_includes_schema_memory_context_in_prompt(text_to_sql_env, monkeypatch):
    data_mod, db_url, tenant_id = text_to_sql_env
    captured = {}

    def fake_completion(**kwargs):
        captured["prompt"] = kwargs["messages"][0]["content"]
        return _fake_sql_response("SELECT * FROM demo_orders")

    monkeypatch.setattr(data_mod.litellm, "completion", fake_completion)

    result, error = await data_mod._text_to_sql_handler(
        call={"query": "lấy hết đơn hàng"},
        cfg={"tool_config": {"text_to_sql": {"db_url": db_url, "data_source": "postgresql"}}, "tenant_id": tenant_id},
    )

    assert error is None, error
    assert "demo_orders" in captured["prompt"]
    assert "order total" in captured["prompt"]  # column description from Schema Memory
    assert "Demo orders table" in captured["prompt"]  # business_description


async def test_text_to_sql_executes_against_real_postgres(text_to_sql_env, monkeypatch):
    data_mod, db_url, tenant_id = text_to_sql_env

    monkeypatch.setattr(data_mod.litellm, "completion",
                         lambda **kw: _fake_sql_response("SELECT total FROM demo_orders ORDER BY total"))

    result, error = await data_mod._text_to_sql_handler(
        call={"query": "lấy hết đơn hàng"},
        cfg={"tool_config": {"text_to_sql": {"db_url": db_url, "data_source": "postgresql"}}, "tenant_id": tenant_id},
    )

    assert error is None, error
    rows = json.loads(result["content"])
    # numeric column -> Decimal via asyncpg -> not JSON-native -> serialized
    # as string by the handler's json.dumps(default=str). Still proves the
    # SQL actually ran against real Postgres and got the real row values.
    assert [r["total"] for r in rows] == ["100", "250"]


async def test_text_to_sql_retries_on_sql_error_with_previous_error_in_prompt(text_to_sql_env, monkeypatch):
    data_mod, db_url, tenant_id = text_to_sql_env
    attempts: list[str] = []

    def fake_completion(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        attempts.append(prompt)
        if len(attempts) == 1:
            return _fake_sql_response("SELECT * FROM table_that_does_not_exist")
        return _fake_sql_response("SELECT total FROM demo_orders")

    monkeypatch.setattr(data_mod.litellm, "completion", fake_completion)

    result, error = await data_mod._text_to_sql_handler(
        call={"query": "lấy hết đơn hàng"},
        cfg={"tool_config": {"text_to_sql": {"db_url": db_url, "data_source": "postgresql"}}, "tenant_id": tenant_id},
    )

    assert error is None, error
    assert len(attempts) == 2, "should succeed on the 2nd attempt, not exhaust all 3"
    assert "SQL trước lỗi" in attempts[1]
    assert "table_that_does_not_exist" in attempts[1]  # actual asyncpg error text fed back


async def test_text_to_sql_fails_after_3_attempts_all_invalid(text_to_sql_env, monkeypatch):
    data_mod, db_url, tenant_id = text_to_sql_env

    monkeypatch.setattr(data_mod.litellm, "completion",
                         lambda **kw: _fake_sql_response("SELECT * FROM table_that_does_not_exist"))

    result, error = await data_mod._text_to_sql_handler(
        call={"query": "lấy hết đơn hàng"},
        cfg={"tool_config": {"text_to_sql": {"db_url": db_url, "data_source": "postgresql"}}, "tenant_id": tenant_id},
    )

    assert result is None
    assert error["tool_name"] == "text_to_sql"
    assert "failed after 3 attempts" in error["error"]


async def test_text_to_sql_falls_back_to_static_schema_hint_when_no_schema_memory_rows(monkeypatch, migrated_db):
    """Different tenant, no Schema Memory rows registered — must fall back
    to the canvas's static schema_hint instead of sending an empty Schema: block."""
    svc_dir = str(REPO_ROOT / "services" / "workflow-runtime")
    added = [p for p in (svc_dir, str(REPO_ROOT)) if p not in sys.path]
    sys.path[:0] = added
    try:
        import importlib as _il
        from services.database_connector.postgres_connector import client as pg_client

        data_mod = _il.import_module("nodes.sandbox.tool_executor.handlers.data")
        _il.reload(data_mod)
        pg_client.set_url(migrated_db["dsn"])
        await pg_client.open()
        tenant_id, _ = _seed_tenant_and_agent(migrated_db["dsn"])

        captured = {}

        def fake_completion(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return _fake_sql_response("SELECT 1")

        monkeypatch.setattr(data_mod.litellm, "completion", fake_completion)

        result, error = await data_mod._text_to_sql_handler(
            call={"query": "test"},
            cfg={"tool_config": {"text_to_sql": {
                "db_url": migrated_db["dsn"], "data_source": "postgresql",
                "schema_hint": "TABLE fallback_table (id INT)",
            }}, "tenant_id": tenant_id},
        )

        assert error is None, error
        assert "fallback_table" in captured["prompt"]

        await pg_client.close()
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)
