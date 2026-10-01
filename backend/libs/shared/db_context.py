"""Tenant-scoped ORM session helper.

Pairs with `backend/alembic/versions/a1b2c3d4e5f6_enable_tenant_rls.py`. After that
migration, every tenant-scoped table has Row-Level Security enabled with a
policy that filters by `current_setting('app.tenant_id')`. Connections that
don't set that setting see zero rows.

This module gives services a single, foolproof way to:

    1. open a SQLAlchemy session as `app_user` (the role RLS is scoped to)
    2. set `app.tenant_id` to the tenant from the JWT
    3. yield the session, run ORM queries, commit/rollback, close

The pattern is deliberately context-manager-only — there is no "give me
a connection and trust me to set it" escape hatch. Trying to provide one
defeats the point of RLS.

Usage (FastAPI route):

    from libs.shared.db_context import get_tenant_session

    @router.get("/things")
    def list_things(db = Depends(get_tenant_session)):
        return db.scalars(select(ThingORM)).all()  # no tenant WHERE — RLS scopes it
"""

from __future__ import annotations

import os
import re
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import Header, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

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


_session_factory: sessionmaker[Session] | None = None
_session_lock = threading.Lock()


def _sync_app_url() -> str:
    dsn = _app_dsn()
    if dsn.startswith("postgres://"):
        return dsn.replace("postgres://", "postgresql://", 1)
    return dsn


def _get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        with _session_lock:
            if _session_factory is None:
                engine = create_engine(_sync_app_url(), pool_pre_ping=True, future=True)
                _session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return _session_factory


@contextmanager
def tenant_session(tenant_id: str) -> Iterator[Session]:
    session = _get_session_factory()()
    try:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": _validated_uuid(tenant_id)},
        )
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_tenant_session(
    x_tenant_id: str = Header(..., alias="X-Tenant-Id"),
) -> Iterator[Session]:
    try:
        tenant_id = _validated_uuid(x_tenant_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    with tenant_session(tenant_id) as session:
        yield session
