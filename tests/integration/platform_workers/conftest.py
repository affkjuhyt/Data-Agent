"""The platform workers connect as app_user on the cluster, so their tests do too."""

from __future__ import annotations

import psycopg2
import pytest
from sqlalchemy.engine import make_url

_APP_USER_PASSWORD = "app-user-test-pw"


@pytest.fixture
def app_user_dsn(migrated_db) -> str:
    """libpq DSN of app_user (NOBYPASSRLS) with the grants deploy/docker/sql/grant-roles.sql
    gives it. Run as the superuser, a worker test passes whether or not the worker sets
    the tenant: the superuser skips every row-level policy."""
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
