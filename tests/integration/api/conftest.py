"""The api against the V3 database (backend/alembic/sql on PostgreSQL 16).

The DDL is applied by the migrate suite's `ddl_server` fixture. The api connects
the way it does in production: as the module's service role (svc_<schema>,
NOBYPASSRLS), with app.org_id set per transaction, so row-level security and
the role grants are what these tests exercise. The roles are NOLOGIN in the DDL;
here they get a password, standing in for the login roles the deploy creates.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import pytest

from libs.dataagent_db import engine_for
from tests.integration.migrate.conftest import ddl_server  # noqa: F401  (fixture)

SERVICE_ROLES = ("svc_org", "svc_qa", "svc_query")
PASSWORD = "api-integration"


def _login_url(dsn: str, role: str) -> str:
    parts = urlsplit(dsn)
    netloc = f"{role}:{PASSWORD}@{parts.hostname}:{parts.port}"
    return urlunsplit(("postgresql+asyncpg", netloc, parts.path, "", ""))


@pytest.fixture(scope="session")
def api_database(ddl_server: dict[str, str]) -> Iterator[str]:  # noqa: F811
    dsn = ddl_server["dataagent"]
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        for role in SERVICE_ROLES:
            cur.execute(f"ALTER ROLE {role} WITH LOGIN PASSWORD %s", (PASSWORD,))
    conn.close()
    mp = pytest.MonkeyPatch()
    for role in SERVICE_ROLES:
        mp.setenv(f"DATAAGENT_DB_URL__{role.upper()}", _login_url(dsn, role))
    engine_for.cache_clear()
    yield dsn
    engine_for.cache_clear()
    mp.undo()


class Seed:
    """Inserts committed rows as the admin; every test works in its own org."""

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

    def org(self) -> uuid.UUID:
        org_id = uuid.uuid4()
        return self.row(
            "org.organization", id=org_id, code=f"org-{org_id.hex[:8]}", name="Tổ chức thử"
        )

    def close(self) -> None:
        self.conn.close()


@pytest.fixture
def seed(api_database: str) -> Iterator[Seed]:
    s = Seed(api_database)
    yield s
    s.close()
