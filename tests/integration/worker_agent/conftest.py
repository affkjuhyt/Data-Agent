"""worker-agent against the V3 database (backend/alembic/sql, applied by the migrate suite's
`ddl_server`), connecting as the roles it uses in production: svc_qa for the answer, the
relay role for claiming outbox rows across organisations. `adventureworks` is the dataset the
product is tested on, in a database of its own on the same server."""

from __future__ import annotations

import importlib.util
import io
import os
import pathlib
import uuid
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest

from libs.dataagent_db import engine_for
from tests.integration.migrate.conftest import ddl_server  # noqa: F401  (fixture)

PASSWORD = "worker-agent-integration"

AW_LOADER = pathlib.Path(__file__).parents[3] / "tests" / "e2e" / "adventureworks" / "load.py"
AW_CACHE = pathlib.Path(os.environ.get("AW_CACHE") or AW_LOADER.with_name(".cache"))
AW_DATABASE = "adventureworks_test"


def _login_url(dsn: str, role: str) -> str:
    parts = urlsplit(dsn)
    return urlunsplit(
        (
            "postgresql+asyncpg",
            f"{role}:{PASSWORD}@{parts.hostname}:{parts.port}",
            parts.path,
            "",
            "",
        )
    )


@pytest.fixture(scope="session")
def worker_database(ddl_server: dict[str, str]) -> Iterator[str]:  # noqa: F811
    dsn = ddl_server["dataagent"]
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        # Roles are cluster-wide: one password serves both databases.
        for role in (
            "svc_qa",
            "svc_inbox",
            "svc_outbox_relay",
            "svc_audit_worker",
            "svc_query",
            "svc_governance",
            "svc_catalog",
            "svc_context",
            "svc_workspace",
            "svc_skill",
            "svc_pipeline",
            "svc_ingest",
            "svc_agent",
            "svc_notify",
            "svc_monitor",
            "svc_eval",
            "svc_conflict",
            "svc_document",
            "svc_period",
            "svc_acquire",
            "svc_org",
        ):
            cur.execute(f"ALTER ROLE {role} WITH LOGIN PASSWORD %s", (PASSWORD,))
    conn.close()
    mp = pytest.MonkeyPatch()
    for role in (
        "svc_qa",
        "svc_inbox",
        "svc_outbox_relay",
        "svc_query",
        "svc_governance",
        "svc_catalog",
        "svc_context",
        "svc_workspace",
        "svc_skill",
        "svc_pipeline",
        "svc_ingest",
        "svc_agent",
        "svc_notify",
        "svc_monitor",
        "svc_eval",
        "svc_conflict",
        "svc_document",
        "svc_period",
        "svc_acquire",
        "svc_org",
    ):
        mp.setenv(f"DATAAGENT_DB_URL__{role.upper()}", _login_url(dsn, role))
    # The audit database's outbox, read by the same relay role through its own connection key.
    mp.setenv(
        "DATAAGENT_DB_URL__SVC_AUDIT_WORKER",
        _login_url(ddl_server["dataagent_audit"], "svc_audit_worker"),
    )
    mp.setenv(
        "DATAAGENT_DB_URL__SVC_OUTBOX_RELAY_AUDIT",
        _login_url(ddl_server["dataagent_audit"], "svc_outbox_relay"),
    )
    engine_for.cache_clear()
    yield dsn
    engine_for.cache_clear()
    mp.undo()


class Seed:
    def __init__(self, dsn: str) -> None:
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True

    def row(self, table: str, **values: object) -> uuid.UUID:
        values.setdefault("id", uuid.uuid4())
        cols = ", ".join(values)
        marks = ", ".join(["%s"] * len(values))
        params = [str(v) if isinstance(v, uuid.UUID) else v for v in values.values()]
        with self.conn.cursor() as cur:
            cur.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", params)
        return values["id"]  # type: ignore[return-value]

    def all(self, sql: str, *params: object) -> list[tuple]:
        with self.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            return cur.fetchall()

    def one(self, sql: str, *params: object) -> object:
        with self.conn.cursor() as cur:
            cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
            row = cur.fetchone()
        return row[0] if row and len(row) == 1 else row


@pytest.fixture
def seed(worker_database: str) -> Iterator[Seed]:
    s = Seed(worker_database)
    yield s
    s.conn.close()


def _load_adventureworks(dsn: str) -> None:
    """tests/e2e/adventureworks/load.py's conversion, fetched once into its .cache/ at the pinned
    commit, written into `dsn` the way `make adventureworks` writes the cluster's database."""
    spec = importlib.util.spec_from_file_location("aw_load", AW_LOADER)
    assert spec is not None and spec.loader is not None
    aw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(aw)
    aw.fetch(AW_CACHE)
    script = (AW_CACHE / "instawdbdw.sql").read_text(encoding="utf-8")
    found = aw.tables(script)
    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            for name, columns in found.items():
                body = ", ".join(f"{aw.snake(col)} {t}" for col, t, _ in columns)
                cur.execute(f"CREATE TABLE {aw.snake(name)} ({body})")
                data = (AW_CACHE / f"{name}.csv").read_bytes()
                text = "".join(line + "\n" for line in aw.rows(data, name, columns))
                cur.copy_expert(f"COPY {aw.snake(name)} FROM STDIN", io.StringIO(text))
            for statement in aw.constraints(script):
                cur.execute(statement)
    finally:
        conn.close()


@pytest.fixture(scope="session")
def adventureworks(request: pytest.FixtureRequest) -> Iterator[Any]:
    """AdventureWorksDW in its own database, loaded once for every module that reads it."""
    admin = os.environ.get("DATAAGENT_TEST_ADMIN_DSN")
    if not admin:
        admin = request.getfixturevalue("postgres_container")["dsn"]
    server = psycopg2.connect(admin)
    server.autocommit = True
    with server.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {AW_DATABASE}")
        cur.execute(f"CREATE DATABASE {AW_DATABASE}")
    dsn = urlunsplit(urlsplit(admin)._replace(path=f"/{AW_DATABASE}"))
    _load_adventureworks(dsn)
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    yield conn
    conn.close()
    with server.cursor() as cur:
        cur.execute(f"DROP DATABASE {AW_DATABASE}")
    server.close()
