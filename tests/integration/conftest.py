"""Fixtures shared by the integration suites (the Postgres container itself is in
tests/conftest.py)."""
from __future__ import annotations

import psycopg2
import pytest
from sqlalchemy.engine import make_url

from tests.integration._db import migrated_database


@pytest.fixture
def migrated_db(postgres_container) -> dict[str, str]:
    """The suite's Postgres at alembic head, holding only what the migrations insert.

    The chain runs once per container; later tests get a truncate-and-reload of
    that state (tests.integration._db.migrated_database).
    """
    migrated_database(postgres_container["dsn"])
    return postgres_container


_APP_USER_PASSWORD = "app-user-test-pw"


@pytest.fixture
def app_user_dsn(migrated_db) -> str:
    """libpq DSN of app_user (NOBYPASSRLS), the role the workers and jobs connect as on
    the cluster, with the grants deploy/docker/sql/grant-roles.sql gives it. Run as the
    superuser, a test passes whether or not the code sets the tenant: the superuser
    skips every row-level policy."""
    conn = psycopg2.connect(migrated_db["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f"ALTER ROLE app_user LOGIN PASSWORD '{_APP_USER_PASSWORD}'")
            cur.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user"
            )
            cur.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user")
    finally:
        conn.close()
    url = make_url(migrated_db["dsn"]).set(username="app_user", password=_APP_USER_PASSWORD)
    return url.render_as_string(hide_password=False)
