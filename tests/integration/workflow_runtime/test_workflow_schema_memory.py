"""Integration tests for Workflow Memory (doc §3.7,
docs/STM_LTM_Memory_Architecture.docx — the LTM layer a data task platform
needs on top of generic conversational memory).

Covers:
  1. Migration c3d4e5f6a7b8 — tables, RLS policy, clean downgrade/re-upgrade.
     Its other table, SchemaMemory, was never used and 9d4f2b7c1e3a drops it. Same asyncpg-subprocess pattern as
     test_alembic_migrations.py, because that's the driver production
     actually runs migrations through.
  2. backend/services/workflow_runtime/infrastructure/workflow_memory.py — record/retrieve against
     a real Postgres + Qdrant testcontainer.

No real LLM/embedding provider is contacted anywhere in this file —
`workflow_memory._embed` is monkeypatched to a fixed vector. That isolates
the module's own logic (PG<->Qdrant join by point_id, task_type/outcome
filtering, recency*duration scoring) from embedding-provider availability,
per the project's "don't test against a real LLM" convention.
"""
from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import sys
import uuid

import psycopg2
import pytest


pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "backend" / "alembic" / "alembic.ini"


def _async_dsn(libpq_dsn: str) -> str:
    assert libpq_dsn.startswith("postgresql://")
    return "postgresql+asyncpg://" + libpq_dsn[len("postgresql://"):]


def _run_alembic(dsn: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": _async_dsn(dsn), "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=60,
    )


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

def test_workflow_memory_created_and_schema_memory_dropped_at_head(migrated_db):
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
    assert tables == ["WorkflowMemory"]


def test_rls_tenant_isolation_policy_exists_on_workflow_memory(migrated_db):
    conn = psycopg2.connect(migrated_db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.relname, p.polname
                FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid
                WHERE c.relname = 'WorkflowMemory' AND p.polname = 'tenant_isolation'
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    assert rows == [("WorkflowMemory", "tenant_isolation")]


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

    # Bare `import memory_middleware` / `workflow_memory`: their layer dirs go on the path.
    svc_dirs = [
        str(REPO_ROOT / "backend" / "services" / "workflow_runtime" / layer)
        for layer in ("api", "application", "domain", "infrastructure")
    ]
    added = [p for p in (*svc_dirs, str(REPO_ROOT)) if p not in sys.path]
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

        from libs.database_connector.postgres_connector import client
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
    from libs.database_connector.postgres_connector import client
    return client._url.replace("postgresql+asyncpg://", "postgresql://")
