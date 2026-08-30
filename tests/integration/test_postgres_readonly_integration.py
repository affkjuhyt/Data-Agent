"""Integration test for postgres_readonly.py against a real Postgres
(SANDBOX_SQL_BACKEND=postgres — local/dev/CI stand-in for Snowflake, see
docs/sandbox_report.md).

Unlike tests/unit/test_postgres_readonly.py (mocks asyncpg.connect
entirely), this drives execute_readonly_query() against a real ephemeral
Postgres container (testcontainers, same postgres_container fixture as
tests/conftest.py) — proving the wrapped SELECT/LIMIT SQL, row shape, and
connection reuse actually work against a real server, not just against a
mock that always agrees with whatever the code expects. Skipped if
testcontainers isn't installed (postgres_container fixture handles that) or
Docker isn't reachable.
"""
from __future__ import annotations

import importlib
import pathlib
import shutil
import sys

import psycopg2
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox-executor")


def _docker_and_image_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        import docker

        client = docker.from_env()
        client.images.get("sandbox-runner:latest")
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    sys.modules.pop("postgres_readonly", None)
    yield
    sys.modules.pop("postgres_readonly", None)
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.fixture
def seeded_db(postgres_container) -> dict[str, str]:
    """Fresh `sample_orders` table per test (mirrors the seed data
    deploy/docker/sql/create-extra-dbs.sql plants for the docker-compose `postgres`
    service), against the ephemeral testcontainer instead of that shared
    service — no docker-compose stack needs to be up for this test."""
    conn = psycopg2.connect(postgres_container["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS sample_orders")
            cur.execute(
                """CREATE TABLE sample_orders (
                    id SERIAL PRIMARY KEY,
                    customer_name TEXT NOT NULL,
                    amount NUMERIC(10, 2) NOT NULL
                )"""
            )
            cur.execute(
                """INSERT INTO sample_orders (customer_name, amount) VALUES
                    ('Acme Corp', 120.50), ('Globex', 75.00), ('Initech', 300.25)"""
            )
    finally:
        conn.close()
    return postgres_container


def _connection_params(container: dict[str, str]) -> dict:
    return {
        "host": container["host"],
        "port": int(container["port"]),
        "user": container["user"],
        "password": container["password"],
        "dbname": container["database"],
    }


async def test_execute_readonly_query_returns_real_rows(seeded_db):
    import postgres_readonly as pr

    result = await pr.execute_readonly_query(
        "SELECT customer_name, amount FROM sample_orders ORDER BY id",
        _connection_params(seeded_db),
    )

    assert result["row_count"] == 3
    assert result["is_truncated"] is False
    assert [r["customer_name"] for r in result["rows"]] == ["Acme Corp", "Globex", "Initech"]
    # amount is NUMERIC — asyncpg maps it to decimal.Decimal by default,
    # matching what main.py's JSONResponse would need to serialize downstream.
    assert float(result["rows"][0]["amount"]) == 120.50


async def test_write_statement_is_rejected_before_touching_the_database(seeded_db):
    import postgres_readonly as pr

    with pytest.raises(pr.PostgresQueryError):
        await pr.execute_readonly_query(
            "INSERT INTO sample_orders (customer_name, amount) VALUES ('x', 1)",
            _connection_params(seeded_db),
        )

    # Confirm nothing was actually inserted — sql_guard rejected it before
    # any query reached the real database.
    conn = psycopg2.connect(seeded_db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM sample_orders")
            assert cur.fetchone()[0] == 3
    finally:
        conn.close()


async def test_a_write_the_guard_cannot_see_is_still_refused(seeded_db):
    """The gap between the other two layers, made executable against a real server.

    `SELECT nextval(...)` is a SELECT by every measure sql_guard applies — one
    statement, no DDL token, no INTO — and it mutates state. The container role
    owns the sequence and is free to advance it, so no missing grant stands in
    the way either. What refuses it is the READ ONLY transaction that
    execute_readonly_query opens, which is the layer that has to hold for DB
    Connector connections: those carry the tenant's own credentials, not the
    `sandbox_reader` role.
    """
    import postgres_readonly as pr

    conn = psycopg2.connect(seeded_db["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("DROP SEQUENCE IF EXISTS sandbox_probe_seq")
            cur.execute("CREATE SEQUENCE sandbox_probe_seq")

        with pytest.raises(pr.PostgresQueryError) as excinfo:
            await pr.execute_readonly_query(
                "SELECT nextval('sandbox_probe_seq')", _connection_params(seeded_db),
            )
        assert "read-only transaction" in str(excinfo.value).lower()

        # The sequence never moved — its first value is still there to be had.
        with conn.cursor() as cur:
            cur.execute("SELECT nextval('sandbox_probe_seq')")
            assert cur.fetchone()[0] == 1
    finally:
        with conn.cursor() as cur:
            cur.execute("DROP SEQUENCE IF EXISTS sandbox_probe_seq")
        conn.close()


async def test_the_connection_is_read_only_outside_a_transaction_too(seeded_db):
    """The connection is pooled and reused, so read-only has to be a property of
    the connection and not only of the one call that opens a transaction."""
    import postgres_readonly as pr

    result = await pr.execute_readonly_query(
        "SELECT current_setting('default_transaction_read_only') AS ro",
        _connection_params(seeded_db),
    )

    assert result["rows"] == [{"ro": "on"}]


async def test_connection_is_reused_across_real_queries(seeded_db):
    import postgres_readonly as pr

    params = _connection_params(seeded_db)
    await pr.execute_readonly_query("SELECT 1 AS one", params)
    conn_after_first = pr._connections[pr._connection_key(params)]
    await pr.execute_readonly_query("SELECT 1 AS one", params)
    conn_after_second = pr._connections[pr._connection_key(params)]

    assert conn_after_first is conn_after_second


@pytest.mark.skipif(
    not _docker_and_image_available(),
    reason="Docker daemon or sandbox-runner:latest image not available",
)
def test_execute_sql_http_endpoint_serializes_decimal_rows(seeded_db, monkeypatch):
    """Drives /execute/sql through the real FastAPI app (not just
    execute_readonly_query() directly, unlike the test above) — this is the
    layer that actually broke: asyncpg/snowflake-connector hand back
    decimal.Decimal for NUMERIC/NUMBER columns, and stdlib json.dumps (what
    FastAPI's default JSONResponse uses) can't encode that on its own,
    turning a valid query into a 500 on both the poll and the SSE stream."""
    monkeypatch.setenv("SANDBOX_SQL_BACKEND", "postgres")
    for mod in ("main", "postgres_readonly", "snowflake_readonly"):
        sys.modules.pop(mod, None)
    try:
        from fastapi.testclient import TestClient
        import main as sandbox_main

        with TestClient(sandbox_main.app) as client:
            resp = client.post(
                "/execute/sql",
                json={
                    "sql": "SELECT customer_name, amount FROM sample_orders ORDER BY id",
                    "connection": _connection_params(seeded_db),
                },
            )
            assert resp.status_code == 202, resp.text
            job_id = resp.json()["job_id"]

            import time as _time

            for _ in range(50):
                poll = client.get(f"/execute/sql/{job_id}")
                data = poll.json()
                if data.get("status") not in ("queued", "running"):
                    break
                _time.sleep(0.3)
            else:
                pytest.fail(f"job {job_id} did not reach a terminal status")

            assert poll.status_code == 200, poll.text
            assert data["success"] is True
            assert data["rows"][0]["amount"] == 120.50
    finally:
        monkeypatch.delenv("SANDBOX_SQL_BACKEND", raising=False)
        for mod in ("main", "postgres_readonly", "snowflake_readonly"):
            sys.modules.pop(mod, None)
