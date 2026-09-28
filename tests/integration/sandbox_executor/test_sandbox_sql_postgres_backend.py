"""Integration test for SANDBOX_SQL_BACKEND=postgres (postgres_readonly.py)
— end-to-end against a real Postgres (testcontainers `postgres_container`
fixture, same one tests/integration/sandbox_executor/test_postgres_readonly_integration.py
uses), exercising the whole /execute/sql wire path (submit -> queue ->
postgres_readonly.py -> JSON response) the way a mocked connection cannot.

This file's specific job: test_postgres_readonly_integration.py's HTTP-level
test (`test_execute_sql_http_endpoint_serializes_decimal_rows`) only covers a
NUMERIC column (decimal.Decimal). Neither it nor tests/unit/
test_postgres_readonly.py's mocked rows ever exercised a TIMESTAMPTZ column.

Found via an actual end-to-end run while re-verifying this feature (not by
reading code): a TIMESTAMPTZ column crashed the whole response with an
unhandled 500 (`TypeError: Object of type datetime is not JSON
serializable`) — main.py's `_json_default` handled `Decimal` but not
`datetime`/`date`/`time`. Fixed in main.py; `created_at` below is
deliberately a TIMESTAMPTZ column so this doesn't regress silently again.
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import time

import psycopg2
import pytest

from tests.integration._sandbox import SKIP_REASON, docker_and_image_available

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox_executor")


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_and_image_available(), reason=SKIP_REASON),
]


@pytest.fixture
def seeded_db_with_timestamp(postgres_container) -> dict[str, str]:
    """Same seeding style as test_postgres_readonly_integration.py's
    `seeded_db`, plus a TIMESTAMPTZ column — the type that actually broke
    JSON encoding (see module docstring)."""
    conn = psycopg2.connect(postgres_container["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS sample_orders_ts")
            cur.execute(
                """CREATE TABLE sample_orders_ts (
                    id SERIAL PRIMARY KEY,
                    customer_name TEXT NOT NULL,
                    amount NUMERIC(10, 2) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )"""
            )
            cur.execute(
                """INSERT INTO sample_orders_ts (customer_name, amount) VALUES
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


@pytest.fixture
def client(monkeypatch):
    # Must be set before main.py is (re)imported — SANDBOX_SQL_BACKEND is
    # read once at module import time to pick which backend module to
    # import, not per-request (main.py's own module docstring/comment).
    monkeypatch.setenv("SANDBOX_SQL_BACKEND", "postgres")
    monkeypatch.setenv("SANDBOX_POOL_SIZE", "1")  # this file never touches /execute/python — keep pool spin-up cheap

    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    # postgres_readonly caches asyncpg connections per process, which is right
    # for the service (one event loop) and wrong across TestClients (one loop
    # each): a connection an earlier test opened fails here with "attached to
    # a different loop". Drop the module, and the functools.cache in
    # load_backend that still holds its execute function, so this app gets a
    # fresh cache.
    sys.modules.pop("postgres_readonly", None)
    try:
        import main as sandbox_main
        from fastapi.testclient import TestClient
        importlib.reload(sandbox_main)
        sandbox_main.load_backend.cache_clear()
        assert sandbox_main._SQL_BACKEND == "postgres"
        with TestClient(sandbox_main.app) as tc:
            yield tc
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)


def _post_and_poll(client, path: str, body: dict, max_wait_s: float = 30.0) -> tuple[int, dict]:
    resp = client.post(path, json=body)
    if resp.status_code == 503:
        return resp.status_code, resp.json()
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]

    deadline = time.monotonic() + max_wait_s
    delay = 0.2
    while True:
        poll = client.get(f"{path}/{job_id}")
        data = poll.json()
        if data.get("status") in ("queued", "running"):
            assert time.monotonic() < deadline, f"job {job_id} did not reach a terminal status within {max_wait_s}s"
            time.sleep(delay)
            delay = min(delay * 1.5, 1.0)
            continue
        return poll.status_code, data


def test_execute_sql_postgres_backend_runs_real_query_incl_datetime_and_decimal(client, seeded_db_with_timestamp):
    status, body = _post_and_poll(client, "/execute/sql", {
        "sql": "SELECT * FROM sample_orders_ts ORDER BY id",
        "connection": _connection_params(seeded_db_with_timestamp),
    })
    assert status == 200, body
    assert body["success"] is True
    assert body["row_count"] == 3
    assert not body["is_truncated"]

    first = body["rows"][0]
    assert first["customer_name"] == "Acme Corp"
    # NUMERIC(10,2) -> asyncpg gives back decimal.Decimal -> main.py's
    # _json_default must turn it into a JSON number, not crash the response.
    assert first["amount"] == 120.5
    # TIMESTAMPTZ -> asyncpg gives back datetime.datetime -> _json_default
    # must turn it into an ISO 8601 string (this is the exact regression
    # this test exists to catch — see module docstring).
    assert isinstance(first["created_at"], str)
    assert first["created_at"].startswith("2")  # a real year, not a crash


def test_execute_sql_postgres_backend_rejects_ddl(client, seeded_db_with_timestamp):
    status, body = _post_and_poll(client, "/execute/sql", {
        "sql": "DROP TABLE sample_orders_ts",
        "connection": _connection_params(seeded_db_with_timestamp),
    })
    assert status == 200
    assert body["success"] is False
    assert "SQL rejected" in body["error"]


def test_execute_sql_postgres_backend_rejects_dml(client, seeded_db_with_timestamp):
    status, body = _post_and_poll(client, "/execute/sql", {
        "sql": "INSERT INTO sample_orders_ts (customer_name, amount) VALUES ('x', 1)",
        "connection": _connection_params(seeded_db_with_timestamp),
    })
    assert status == 200
    assert body["success"] is False
    assert "SQL rejected" in body["error"]
