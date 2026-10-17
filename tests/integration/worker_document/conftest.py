"""worker-document against the V3 databases (backend/alembic/sql, applied by the migrate suite's
`ddl_server`), as the roles it uses in production: svc_document for pages, chunks and quality
issues, svc_audit for security events in the audit database."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest

from libs.dataagent_db import engine_for
from tests.integration.migrate.conftest import ddl_server  # noqa: F401  (fixture)

PASSWORD = "worker-document-integration"


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
def worker_database(ddl_server: dict[str, str]) -> Iterator[dict[str, str]]:  # noqa: F811
    dsn = ddl_server["dataagent"]
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        # Roles are cluster-wide: one password serves both databases.
        for role in ("svc_document", "svc_audit"):
            cur.execute(f"ALTER ROLE {role} WITH LOGIN PASSWORD %s", (PASSWORD,))
    conn.close()
    mp = pytest.MonkeyPatch()
    mp.setenv("DATAAGENT_DB_URL__SVC_DOCUMENT", _login_url(dsn, "svc_document"))
    mp.setenv("DATAAGENT_DB_URL__SVC_AUDIT", _login_url(ddl_server["dataagent_audit"], "svc_audit"))
    mp.setenv("MINIO_URL", "http://minio.invalid")
    # No vector index here: indexing is unit-tested; these tests are about what Postgres holds.
    mp.delenv("QDRANT_URL", raising=False)
    engine_for.cache_clear()
    yield ddl_server
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
        rows = self.all(sql, *params)
        row = rows[0] if rows else None
        return row[0] if row and len(row) == 1 else row


@pytest.fixture
def seed(worker_database: dict[str, str]) -> Iterator[Seed]:
    s = Seed(worker_database["dataagent"])
    yield s
    s.conn.close()


@pytest.fixture
def audit(worker_database: dict[str, str]) -> Iterator[Seed]:
    s = Seed(worker_database["dataagent_audit"])
    yield s
    s.conn.close()
