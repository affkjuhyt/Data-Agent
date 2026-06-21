"""Integration tests for the gap fixes listed in docs/STM_LTM_Memory_Implementation.md
mục 7 (comparison against docs/STM_LTM_Memory_Architecture.docx):

  1. Catch-all fact_key ("general_note") no longer overwrites unrelated
     facts — novelty-detected by similarity instead of a single shared slot.
  2. Schema Memory access_roles is actually enforced on read.
  3. Episodic Future reminders transition out of "pending" after being
     surfaced — recurring ones get a new row at the next cron occurrence.

Real Postgres + Qdrant via testcontainers. embed() is monkeypatched to
return vectors we fully control (precise, engineered cosine similarity),
not because we can't reach an LLM, but because we need EXACT similarity
values to hit the 0.7/0.85/0.95 decision boundaries deterministically —
a real embedding model can't promise that for arbitrary text. No real
LLM/embedding provider is contacted.
"""
from __future__ import annotations

import math
import pathlib
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator

import psycopg2
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic" / "alembic.ini"


def _async_dsn(libpq_dsn: str) -> str:
    assert libpq_dsn.startswith("postgresql://")
    return "postgresql+asyncpg://" + libpq_dsn[len("postgresql://"):]


def _run_alembic_upgrade(dsn: str) -> subprocess.CompletedProcess:
    import os
    env = {**os.environ, "DATABASE_URL": _async_dsn(dsn), "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), "upgrade", "head"],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=60,
    )


@pytest.fixture
def fresh_db(postgres_container) -> Iterator[dict[str, str]]:
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
    result = _run_alembic_upgrade(fresh_db["dsn"])
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
                (tenant_id, "MC Test Tenant", f"mc-test-{tenant_id[:8]}", "Asia-SE1"),
            )
            cur.execute(
                """INSERT INTO "Agents" (id, tenant_id, name) VALUES (%s, %s, %s)""",
                (agent_id, tenant_id, "MC Test Agent"),
            )
    finally:
        conn.close()
    return tenant_id, agent_id


def _vec_at_sim(sim: float, dim: int = 4) -> list[float]:
    """Unit vector whose cosine similarity to e0=[1,0,0,...] is exactly `sim`."""
    rest = math.sqrt(max(0.0, 1.0 - sim * sim))
    return [sim, rest] + [0.0] * (dim - 2)


_E0 = _vec_at_sim(1.0)   # reference vector — "Note A"
_E1 = [0.0, 1.0, 0.0, 0.0]  # orthogonal — sim(_E0, _E1) = 0, "completely different fact"


# ─── 1. Catch-all fact_key collision fix (memory-consumer) ────────────────

@pytest.fixture
def mc_env(migrated_db, qdrant_container, monkeypatch):
    svc_dir = str(REPO_ROOT / "services" / "memory-consumer")
    added = [p for p in (svc_dir,) if p not in sys.path]
    sys.path[:0] = added
    try:
        import importlib
        import main as mc_main
        importlib.reload(mc_main)

        monkeypatch.setattr(mc_main, "EMBEDDING_DIM", 4)
        monkeypatch.setattr(mc_main, "QDRANT_URL", qdrant_container["url"])

        from qdrant_client import QdrantClient
        qdrant = QdrantClient(url=qdrant_container["url"], check_compatibility=False)
        mc_main.ensure_qdrant_collection(qdrant)

        conn = psycopg2.connect(migrated_db["dsn"])
        tenant_id, agent_id = _seed_tenant_and_agent(migrated_db["dsn"])

        yield mc_main, conn, qdrant, tenant_id, agent_id

        conn.close()
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)


def _active_agent_memories(conn, tenant_id: str) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT content, fact_key FROM "AgentMemories"
               WHERE tenant_id=%s AND deleted_at IS NULL ORDER BY created_at""",
            (tenant_id,),
        )
        return cur.fetchall()


def test_catch_all_distinct_facts_do_not_overwrite_each_other(mc_env, monkeypatch):
    mc_main, conn, qdrant, tenant_id, agent_id = mc_env
    vectors = {"Note A": _E0, "Note B totally unrelated": _E1}
    monkeypatch.setattr(mc_main, "embed", lambda content: vectors[content])

    cur = conn.cursor()
    mc_main.process_insight(cur, qdrant, tenant_id, agent_id, "user1",
                             {"memory_type": "semantic", "action": "add", "importance_score": 0.8,
                              "domain": "general", "fact_key": None, "content": "Note A"},
                             "msg1", 0)
    mc_main.process_insight(cur, qdrant, tenant_id, agent_id, "user1",
                             {"memory_type": "semantic", "action": "add", "importance_score": 0.8,
                              "domain": "general", "fact_key": None, "content": "Note B totally unrelated"},
                             "msg2", 0)
    conn.commit()

    rows = _active_agent_memories(conn, tenant_id)
    contents = {r[0] for r in rows}
    assert contents == {"Note A", "Note B totally unrelated"}, (
        "doc §7 gap fix: 2 unrelated catch-all facts must NOT collapse into 1 row"
    )
    fact_keys = {r[1] for r in rows}
    assert len(fact_keys) == 2, "each distinct catch-all fact must get its own fact_key"


def test_catch_all_no_change_for_near_identical_resend(mc_env, monkeypatch):
    mc_main, conn, qdrant, tenant_id, agent_id = mc_env
    monkeypatch.setattr(mc_main, "embed", lambda content: _E0)

    cur = conn.cursor()
    insight = {"memory_type": "semantic", "action": "add", "importance_score": 0.8,
               "domain": "general", "fact_key": None, "content": "Note A"}
    mc_main.process_insight(cur, qdrant, tenant_id, agent_id, "user1", insight, "msg1", 0)
    mc_main.process_insight(cur, qdrant, tenant_id, agent_id, "user1", insight, "msg2", 0)
    conn.commit()

    rows = _active_agent_memories(conn, tenant_id)
    assert len(rows) == 1, "doc §V: sim>0.95 -> NO_CHANGE, must not create a second row"


def test_catch_all_updates_existing_entry_when_revised(mc_env, monkeypatch):
    mc_main, conn, qdrant, tenant_id, agent_id = mc_env
    vectors = {"Note A original": _E0, "Note A revised": _vec_at_sim(0.9)}
    monkeypatch.setattr(mc_main, "embed", lambda content: vectors[content])

    cur = conn.cursor()
    mc_main.process_insight(cur, qdrant, tenant_id, agent_id, "user1",
                             {"memory_type": "semantic", "action": "add", "importance_score": 0.8,
                              "domain": "general", "fact_key": None, "content": "Note A original"},
                             "msg1", 0)
    mc_main.process_insight(cur, qdrant, tenant_id, agent_id, "user1",
                             {"memory_type": "semantic", "action": "add", "importance_score": 0.8,
                              "domain": "general", "fact_key": None, "content": "Note A revised"},
                             "msg2", 0)
    conn.commit()

    rows = _active_agent_memories(conn, tenant_id)
    assert len(rows) == 1, "doc §V: sim in (0.85, 0.95] -> UPDATE same row, not a new one"
    assert rows[0][0] == "Note A revised"


def test_catch_all_delete_removes_best_similarity_match(mc_env, monkeypatch):
    mc_main, conn, qdrant, tenant_id, agent_id = mc_env
    monkeypatch.setattr(mc_main, "embed", lambda content: _E0)

    cur = conn.cursor()
    mc_main.process_insight(cur, qdrant, tenant_id, agent_id, "user1",
                             {"memory_type": "semantic", "action": "add", "importance_score": 0.8,
                              "domain": "general", "fact_key": None, "content": "Note A"},
                             "msg1", 0)
    mc_main.process_insight(cur, qdrant, tenant_id, agent_id, "user1",
                             {"memory_type": "semantic", "action": "delete", "importance_score": 0.5,
                              "domain": "general", "fact_key": None, "content": "Note A"},
                             "msg2", 0)
    conn.commit()

    assert _active_agent_memories(conn, tenant_id) == []


def test_catch_all_delete_skips_when_no_confident_match(mc_env, monkeypatch):
    """Doc §V/cancel_future_memory spirit: deleting the wrong fact is worse
    than leaving a stale one — no match above the UPDATE threshold means no-op."""
    mc_main, conn, qdrant, tenant_id, agent_id = mc_env
    vectors = {"Note A": _E0, "totally unrelated delete request": _E1}
    monkeypatch.setattr(mc_main, "embed", lambda content: vectors[content])

    cur = conn.cursor()
    mc_main.process_insight(cur, qdrant, tenant_id, agent_id, "user1",
                             {"memory_type": "semantic", "action": "add", "importance_score": 0.8,
                              "domain": "general", "fact_key": None, "content": "Note A"},
                             "msg1", 0)
    mc_main.process_insight(cur, qdrant, tenant_id, agent_id, "user1",
                             {"memory_type": "semantic", "action": "delete", "importance_score": 0.5,
                              "domain": "general", "fact_key": None, "content": "totally unrelated delete request"},
                             "msg2", 0)
    conn.commit()

    rows = _active_agent_memories(conn, tenant_id)
    assert len(rows) == 1 and rows[0][0] == "Note A"


def test_controlled_vocab_fact_key_keeps_single_slot_behavior(mc_env, monkeypatch):
    """Regression: non-catch-all fact_key (controlled vocabulary) is a
    canonical single value — must still overwrite in place, unlike the
    catch-all fix above."""
    mc_main, conn, qdrant, tenant_id, agent_id = mc_env
    vectors = {"PDF": _E0, "Excel": _E1}
    monkeypatch.setattr(mc_main, "embed", lambda content: vectors[content])

    cur = conn.cursor()
    mc_main.process_insight(cur, qdrant, tenant_id, agent_id, "user1",
                             {"memory_type": "semantic", "action": "add", "importance_score": 0.8,
                              "domain": "user_pref", "fact_key": "preferred_report_format", "content": "PDF"},
                             "msg1", 0)
    mc_main.process_insight(cur, qdrant, tenant_id, agent_id, "user1",
                             {"memory_type": "semantic", "action": "add", "importance_score": 0.8,
                              "domain": "user_pref", "fact_key": "preferred_report_format", "content": "Excel"},
                             "msg2", 0)
    conn.commit()

    rows = _active_agent_memories(conn, tenant_id)
    assert len(rows) == 1 and rows[0][0] == "Excel"


# ─── 2. Episodic Future recurrence reschedule (memory_middleware.py) ──────

@pytest.fixture
async def mm_env(migrated_db):
    svc_dir = str(REPO_ROOT / "services" / "workflow-runtime")
    added = [p for p in (svc_dir, str(REPO_ROOT)) if p not in sys.path]
    sys.path[:0] = added
    try:
        import importlib
        import memory_middleware as mm
        importlib.reload(mm)

        from services.database_connector.postgres_connector import client
        client.set_url(migrated_db["dsn"])
        await client.open()

        tenant_id, agent_id = _seed_tenant_and_agent(migrated_db["dsn"])
        yield mm, migrated_db["dsn"], tenant_id, agent_id

        await client.close()
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)


def _insert_future_memory(dsn, tenant_id, agent_id, content, execution_time, recurrence=None):
    row_id = str(uuid.uuid4())
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO "FutureMemory" (id, tenant_id, agent_id, user_ref, content, execution_time, recurrence)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (row_id, tenant_id, agent_id, "user1", content, execution_time, recurrence),
        )
    conn.close()
    return row_id


def _future_memory_rows(dsn, tenant_id):
    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, status, execution_time, recurrence FROM "FutureMemory"
               WHERE tenant_id=%s ORDER BY execution_time""",
            (tenant_id,),
        )
        rows = cur.fetchall()
    conn.close()
    return rows


async def test_non_recurring_reminder_marked_executed_after_surfacing(mm_env):
    mm, dsn, tenant_id, agent_id = mm_env
    due = datetime.now(timezone.utc) - timedelta(minutes=5)
    _insert_future_memory(dsn, tenant_id, agent_id, "nhắc tôi gửi báo cáo", due)

    surfaced = await mm.retrieve_pending_reminders(tenant_id, agent_id, "user1")
    assert len(surfaced) == 1

    rows = _future_memory_rows(dsn, tenant_id)
    assert len(rows) == 1, "doc §3.4: one-off reminder does not spawn a new row"
    assert rows[0][1] == "executed"


async def test_recurring_reminder_creates_next_occurrence(mm_env):
    mm, dsn, tenant_id, agent_id = mm_env
    due = datetime.now(timezone.utc) - timedelta(minutes=5)
    _insert_future_memory(dsn, tenant_id, agent_id, "báo cáo ngày 1 hàng tháng",
                           due, recurrence="0 8 1 * *")

    surfaced = await mm.retrieve_pending_reminders(tenant_id, agent_id, "user1")
    assert len(surfaced) == 1

    rows = _future_memory_rows(dsn, tenant_id)
    assert len(rows) == 2, "doc §3.4: recurring reminder must spawn the next occurrence"
    statuses = {r[1] for r in rows}
    assert statuses == {"executed", "pending"}
    next_row = next(r for r in rows if r[1] == "pending")
    assert next_row[3] == "0 8 1 * *"
    assert next_row[2] > due


async def test_reminder_not_resurfaced_after_being_marked_executed(mm_env):
    """The exact bug doc §7 flagged: same due reminder must not come back
    every session forever."""
    mm, dsn, tenant_id, agent_id = mm_env
    due = datetime.now(timezone.utc) - timedelta(minutes=5)
    _insert_future_memory(dsn, tenant_id, agent_id, "one-off", due)

    first = await mm.retrieve_pending_reminders(tenant_id, agent_id, "user1")
    second = await mm.retrieve_pending_reminders(tenant_id, agent_id, "user1")

    assert len(first) == 1
    assert second == []


async def test_bad_cron_expression_does_not_crash_and_still_marks_executed(mm_env):
    mm, dsn, tenant_id, agent_id = mm_env
    due = datetime.now(timezone.utc) - timedelta(minutes=5)
    _insert_future_memory(dsn, tenant_id, agent_id, "broken cron", due, recurrence="not a cron expression")

    surfaced = await mm.retrieve_pending_reminders(tenant_id, agent_id, "user1")
    assert len(surfaced) == 1

    rows = _future_memory_rows(dsn, tenant_id)
    assert len(rows) == 1, "no next-occurrence row should be created from a bad cron expr"
    assert rows[0][1] == "executed"


# ─── 3. Schema Memory access_roles enforcement (workflow_memory.py) ───────

@pytest.fixture
async def wm_rbac_env(migrated_db):
    svc_dir = str(REPO_ROOT / "services" / "workflow-runtime")
    added = [p for p in (svc_dir, str(REPO_ROOT)) if p not in sys.path]
    sys.path[:0] = added
    try:
        import importlib
        import workflow_memory as wm
        importlib.reload(wm)

        from services.database_connector.postgres_connector import client
        client.set_url(migrated_db["dsn"])
        await client.open()

        from basemodel.services_databaseconnector.postgres_orm.workflow_orm import SchemaMemoryORM
        tenant_id, _ = _seed_tenant_and_agent(migrated_db["dsn"])

        async with client.get_client(tenant_id=tenant_id) as session:
            session.add(SchemaMemoryORM(
                id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), data_source="postgresql", table_name="public_table",
                column_metadata=[], business_description="visible to everyone", access_roles=[],
            ))
            session.add(SchemaMemoryORM(
                id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), data_source="postgresql", table_name="finance_table",
                column_metadata=[], business_description="restricted", access_roles=["finance_analyst", "admin"],
            ))
            await session.commit()

        yield wm, tenant_id
        await client.close()
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)


async def test_unrestricted_table_visible_regardless_of_caller_roles(wm_rbac_env):
    wm, tenant_id = wm_rbac_env
    tables = await wm.retrieve_schema_memory(tenant_id, "postgresql", user_roles=["random_role"])
    names = {t["table_name"] for t in tables}
    assert "public_table" in names
    assert "finance_table" not in names


async def test_restricted_table_visible_when_caller_has_matching_role(wm_rbac_env):
    wm, tenant_id = wm_rbac_env
    tables = await wm.retrieve_schema_memory(tenant_id, "postgresql", user_roles=["finance_analyst"])
    names = {t["table_name"] for t in tables}
    assert names == {"public_table", "finance_table"}


async def test_no_user_roles_param_skips_filter_entirely(wm_rbac_env):
    """user_roles=None (default) — caller identity unknown/internal — must
    not silently hide anything; that would be a behavior change for every
    existing caller that doesn't pass this new param."""
    wm, tenant_id = wm_rbac_env
    tables = await wm.retrieve_schema_memory(tenant_id, "postgresql")
    names = {t["table_name"] for t in tables}
    assert names == {"public_table", "finance_table"}


async def test_empty_user_roles_hides_all_restricted_tables(wm_rbac_env):
    wm, tenant_id = wm_rbac_env
    tables = await wm.retrieve_schema_memory(tenant_id, "postgresql", user_roles=[])
    names = {t["table_name"] for t in tables}
    assert names == {"public_table"}
