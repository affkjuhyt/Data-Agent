"""Row-Level Security enforcement tests.

These pin the contract of `backend/alembic/versions/a1b2c3d4e5f6_enable_tenant_rls.py`:

  * `app_user` cannot see another tenant's rows even via a WHERE-less SELECT.
  * `app_user` cannot insert/update a row with a tenant_id it isn't scoped to.
  * Without `app.tenant_id` set, `app_user` sees nothing (not "everything",
    not an error — quietly empty).
  * Owner/superuser bypass RLS so migrations + admin tasks still work.
  * The FastAPI dependency `get_tenant_db` rejects malformed tenant headers
    before opening a connection.

If a refactor accidentally drops FORCE or rewrites the policy, the offending
test below fails fast with a clear message.
"""
from __future__ import annotations

import psycopg2
import psycopg2.extras
import pytest

pytestmark = pytest.mark.integration


TENANT_1 = "11111111-1111-1111-1111-111111111111"
TENANT_2 = "22222222-2222-2222-2222-222222222222"


# ─── Helpers ───────────────────────────────────────────────────────────────
def _seed_two_tenants(owner_dsn: str) -> None:
    """As superuser (bypasses RLS), insert one api_keys row per tenant."""
    conn = psycopg2.connect(owner_dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO api_keys (tenant_id, created_by, name, key_hash, key_prefix, scope) "
                "VALUES (%s, 't1', 'aero-prod',   'h1', 'sk-prod-aero',  'full_access'), "
                "       (%s, 't2', 'helios-prod', 'h2', 'sk-prod-helio', 'full_access')",
                (TENANT_1, TENANT_2),
            )
    finally:
        conn.close()


def _app_conn(app_user_dsn: str):
    """Open a fresh app_user connection. Caller responsible for closing."""
    conn = psycopg2.connect(app_user_dsn)
    conn.autocommit = False
    return conn


def _set_tenant(conn, tenant_id: str | None) -> None:
    """SET LOCAL app.tenant_id — or clear it by passing None."""
    with conn.cursor() as cur:
        if tenant_id is None:
            cur.execute("SELECT set_config('app.tenant_id', '', true)")
        else:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))


# ─── Per-test isolation ────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _truncate(auth_api_db):
    """Wipe rows before each test so seeds are reproducible.

    TRUNCATE as superuser (the testcontainer owner) — bypasses RLS even
    under FORCE because owner role gets the superuser bit by default.
    """
    conn = psycopg2.connect(auth_api_db["dsn"])
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE api_keys, secrets_vault, ip_allowlists, key_rotations RESTART IDENTITY")
    finally:
        conn.close()


# ─── 1. Policy actually filters reads ──────────────────────────────────────
def test_app_user_sees_only_own_tenant_rows_via_wildcard_select(auth_api_db):
    """The core RLS guarantee: WHERE-less SELECT returns only the bound tenant."""
    _seed_two_tenants(auth_api_db["dsn"])

    conn = _app_conn(auth_api_db["app_user_dsn"])
    try:
        _set_tenant(conn, TENANT_1)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Deliberately no WHERE — if RLS is off, this returns BOTH rows.
            cur.execute("SELECT tenant_id::text, name FROM api_keys")
            rows = cur.fetchall()
    finally:
        conn.close()

    assert len(rows) == 1, f"Expected 1 row for T1, got {len(rows)}: {rows}"
    assert rows[0]["tenant_id"] == TENANT_1
    assert rows[0]["name"] == "aero-prod"


# ─── 2. Without context set, the connection sees nothing ───────────────────
def test_app_user_without_tenant_context_sees_no_rows(auth_api_db):
    """A connection that forgets to SET app.tenant_id sees zero rows, not all.

    This is the "fail-closed" property: leaking nothing > leaking everything.
    """
    _seed_two_tenants(auth_api_db["dsn"])

    conn = _app_conn(auth_api_db["app_user_dsn"])
    try:
        # Note: NOT calling _set_tenant — default state.
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM api_keys")
            (n,) = cur.fetchone()
    finally:
        conn.close()

    assert n == 0, f"Connection without tenant context saw {n} rows (expected 0)"


# ─── 3. WITH CHECK blocks cross-tenant smuggling on INSERT ─────────────────
def test_app_user_cannot_insert_row_for_different_tenant(auth_api_db):
    """Caller bound to T1 trying to INSERT a T2 row must be denied.

    Postgres raises InsufficientPrivilege (SQLSTATE 42501) for RLS WITH CHECK
    violations, NOT CheckViolation (23514) — those are reserved for column-
    level CHECK constraints. Assert the error message so a missing GRANT
    INSERT (which also raises InsufficientPrivilege) doesn't pass the test
    by accident.
    """
    conn = _app_conn(auth_api_db["app_user_dsn"])
    try:
        _set_tenant(conn, TENANT_1)
        with conn.cursor() as cur, pytest.raises(psycopg2.errors.InsufficientPrivilege) as exc_info:
            cur.execute(
                "INSERT INTO api_keys (tenant_id, created_by, name, key_hash, key_prefix, scope) "
                "VALUES (%s, 't1', 'smuggled', 'h', 'sk-x', 'full_access')",
                (TENANT_2,),
            )
        assert "row-level security" in str(exc_info.value).lower(), (
            f"InsufficientPrivilege raised but not from RLS: {exc_info.value}"
        )
        conn.rollback()
    finally:
        conn.close()


# ─── 4. Owner (superuser) bypasses RLS — required for migrations + admin ───
def test_owner_role_bypasses_rls(auth_api_db):
    """The superuser/owner can still SELECT cross-tenant — for migrations,
    backups, audit aggregation. If this regresses, alembic upgrades fail."""
    _seed_two_tenants(auth_api_db["dsn"])

    conn = psycopg2.connect(auth_api_db["dsn"])
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM api_keys")
            (n,) = cur.fetchone()
    finally:
        conn.close()

    assert n == 2, f"Owner should see all 2 rows, saw {n}"


# ─── 5. The FastAPI dependency rejects bad tenant headers early ────────────
def test_get_tenant_db_rejects_non_uuid_tenant_header(client, headers_factory):
    """`get_tenant_db` validates the X-Tenant-Id header is a UUID before it
    even opens a connection. Blocks SQL-injection-via-SET attempts at the
    HTTP boundary, not just at the SET LOCAL call."""
    h = headers_factory(tenant="not-a-uuid; DROP TABLE api_keys; --")
    r = client.get("/api/auth/api-keys/list-via-rls", headers=h)
    assert r.status_code == 422, r.text
    assert "tenant_id" in r.json()["detail"].lower()


# ─── 6. End-to-end via FastAPI route (PoC for the rollout) ─────────────────
def test_list_via_rls_route_returns_only_caller_tenant(client, headers_factory, auth_api_db):
    """The `/api/auth/api-keys/list-via-rls` route has NO WHERE in its SQL.
    It relies entirely on RLS + `get_tenant_db` to scope rows. This proves
    the wire-up: dependency sets context, RLS filters, route returns clean."""
    _seed_two_tenants(auth_api_db["dsn"])

    r = client.get(
        "/api/auth/api-keys/list-via-rls",
        headers=headers_factory(tenant=TENANT_1),
    )
    assert r.status_code == 200, r.text
    keys = r.json()
    assert len(keys) == 1, f"T1 should see exactly its key, got: {keys}"
    assert keys[0]["name"] == "aero-prod"

    # And T2 sees the other.
    r2 = client.get(
        "/api/auth/api-keys/list-via-rls",
        headers=headers_factory(tenant=TENANT_2),
    )
    assert r2.status_code == 200
    assert len(r2.json()) == 1
    assert r2.json()[0]["name"] == "helios-prod"
