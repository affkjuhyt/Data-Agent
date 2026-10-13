"""worker-agent against the V3 database (backend/alembic/sql, applied by the migrate suite's
`ddl_server`), connecting as the roles it uses in production: svc_qa for the answer, the
relay role for claiming outbox rows across organisations."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest

from libs.dataagent_db import engine_for
from tests.integration.migrate.conftest import ddl_server  # noqa: F401  (fixture)

PASSWORD = "worker-agent-integration"


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
            "svc_catalog",
            "svc_context",
            "svc_workspace",
            "svc_skill",
            "svc_pack",
        ):
            cur.execute(f"ALTER ROLE {role} WITH LOGIN PASSWORD %s", (PASSWORD,))
    conn.close()
    mp = pytest.MonkeyPatch()
    for role in (
        "svc_qa",
        "svc_inbox",
        "svc_outbox_relay",
        "svc_query",
        "svc_catalog",
        "svc_context",
        "svc_workspace",
        "svc_skill",
        "svc_pack",
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
