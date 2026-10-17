from __future__ import annotations

import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest

from libs.dataagent_db import engine_for
from tests.integration.migrate.conftest import ddl_server  # noqa: F401

PASSWORD = "worker-ingest-integration"


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
        for role in ("svc_ingest", "svc_catalog", "svc_outbox_relay"):
            cur.execute(f"ALTER ROLE {role} WITH LOGIN PASSWORD %s", (PASSWORD,))
    conn.close()
    monkeypatch = pytest.MonkeyPatch()
    for role in ("svc_ingest", "svc_catalog", "svc_outbox_relay"):
        monkeypatch.setenv(f"DATAAGENT_DB_URL__{role.upper()}", _login_url(dsn, role))
    engine_for.cache_clear()
    yield dsn
    engine_for.cache_clear()
    monkeypatch.undo()


class Seed:
    def __init__(self, dsn: str) -> None:
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True

    def row(self, table: str, **values: object) -> uuid.UUID:
        values.setdefault("id", uuid.uuid4())
        columns = ", ".join(values)
        marks = ", ".join(["%s"] * len(values))
        params = [
            str(value) if isinstance(value, uuid.UUID) else value for value in values.values()
        ]
        with self.conn.cursor() as cur:
            cur.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", params)
        return values["id"]  # type: ignore[return-value]

    def one(self, sql: str, *params: object) -> object:
        with self.conn.cursor() as cur:
            cur.execute(
                sql, [str(value) if isinstance(value, uuid.UUID) else value for value in params]
            )
            row = cur.fetchone()
        return row[0] if row and len(row) == 1 else row

    def all(self, sql: str, *params: object) -> list[tuple[object, ...]]:
        with self.conn.cursor() as cur:
            cur.execute(
                sql, [str(value) if isinstance(value, uuid.UUID) else value for value in params]
            )
            return list(cur.fetchall())


@pytest.fixture
def seed(worker_database: str) -> Iterator[Seed]:
    value = Seed(worker_database)
    yield value
    value.conn.close()
