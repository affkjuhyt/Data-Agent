"""Tenant-scoped DB connection helper.

Pairs with `alembic/versions/a1b2c3d4e5f6_enable_tenant_rls.py`. After that
migration, every tenant-scoped table has Row-Level Security enabled with a
policy that filters by `current_setting('app.tenant_id')`. Connections that
don't set that setting see zero rows.

This module gives services a single, foolproof way to:

    1. open a connection as `app_user` (the role RLS is scoped to)
    2. set `app.tenant_id` to the tenant from the JWT
    3. yield the connection, run queries, commit/rollback, close

The pattern is deliberately context-manager-only — there is no "give me
a connection and trust me to set it" escape hatch. Trying to provide one
defeats the point of RLS.

Usage (FastAPI route):

    from services.shared.db_context import get_tenant_db

    @router.get("/things")
    def list_things(db = Depends(get_tenant_db)):
        with db.cursor() as cur:
            cur.execute("SELECT * FROM things")  # no WHERE — RLS scopes it
            return cur.fetchall()

Usage (script / one-off):

    from services.shared.db_context import tenant_scoped_db

    with tenant_scoped_db(tenant_id="<uuid>") as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO things (name) VALUES (%s)", ("hi",))
        conn.commit()
"""
from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg2
from fastapi import Header, HTTPException

# Connection URL for the app_user role. MUST be distinct from the
# admin/owner DATABASE_URL. If services share a single DSN, RLS is bypassed
# because the owner role bypasses policies (unless FORCE RLS is enabled,
# which it is — but FORCE only applies to non-superuser roles).
#
# Set APP_DATABASE_URL via env at deploy time, e.g.:
#   APP_DATABASE_URL=postgresql://app_user:<pw>@postgres:5432/dataagent
APP_DATABASE_URL_ENV = "APP_DATABASE_URL"

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _validated_uuid(value: str) -> str:
    """Reject non-UUID input. Defense against SQL-injection-via-SET.

    `SET app.tenant_id = '<value>'` is parameterizable in PG's `set_config`
    but the simplest form interpolates the literal. We reject anything
    that isn't a UUID so an attacker can't smuggle a `'; DROP TABLE` payload.
    """
    if not isinstance(value, str) or not _UUID_RE.fullmatch(value):
        raise ValueError(f"Invalid tenant_id (not a UUID): {value!r}")
    # Also parse via uuid module — catches edge cases like leading zeros that
    # the regex accepts but PG might trip on.
    uuid.UUID(value)
    return value


def _app_dsn() -> str:
    dsn = os.environ.get(APP_DATABASE_URL_ENV)
    if not dsn:
        raise RuntimeError(
            f"{APP_DATABASE_URL_ENV} is not set. Tenant-scoped queries require "
            "a connection as `app_user`, the role RLS policies are written for. "
            "Owner/superuser connections bypass FORCE RLS and would return all "
            "tenants' rows. Set APP_DATABASE_URL in your service env."
        )
    return dsn


def set_tenant_context(conn: Any, tenant_id: str) -> None:
    """Bind `app.tenant_id` for the lifetime of the current transaction.

    Uses `SET LOCAL` so the setting reverts on COMMIT/ROLLBACK — important
    when connections come from a pool and may be reused for another tenant.

    `set_config(..., is_local=true)` rather than literal SQL so the tenant_id
    is bound as a parameter (defense in depth on top of _validated_uuid).
    """
    tenant_id = _validated_uuid(tenant_id)
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))


@contextmanager
def tenant_scoped_db(tenant_id: str) -> Iterator[Any]:
    """Open an app_user connection, scope it to a tenant, yield it.

    Commits on clean exit; rolls back on exception. Caller is responsible
    for `conn.commit()` if they want to commit mid-block — but the safer
    pattern is to let the context manager handle it on exit.
    """
    tenant_id = _validated_uuid(tenant_id)
    conn = psycopg2.connect(_app_dsn())
    conn.autocommit = False
    try:
        set_tenant_context(conn, tenant_id)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_tenant_db(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
) -> Iterator[Any]:
    """FastAPI dependency: yield a tenant-scoped connection per request.

    Reads the tenant from the X-Tenant-Id header that Kong injects after JWT
    verification (see infra/kong/plugins/aeroflow-jwks). A request that
    reaches the service with a missing/invalid tenant header gets 422 — the
    intent is that Kong always populates this, so it being absent is a
    config bug, not a user error.

    Use as:
        @router.get("/x")
        def handler(db = Depends(get_tenant_db)):
            ...
    """
    try:
        tenant_id = _validated_uuid(x_tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    conn = psycopg2.connect(_app_dsn())
    conn.autocommit = False
    try:
        set_tenant_context(conn, tenant_id)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
