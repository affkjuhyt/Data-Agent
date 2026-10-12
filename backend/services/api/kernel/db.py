"""Database access for api modules: every query inside the caller's org transaction.

ADR-28: each module owns one schema and connects with its own role
(`svc_<schema>`); the engines, the URLs (`DATAAGENT_DB_URL__SVC_<SCHEMA>`) and the
ADR-31 scoping (`set_config(..., true)` per transaction, no asyncpg statement
cache behind pgbouncer) live in libs.dataagent_db, shared with the workers.
This only binds that to the request's Principal, so a module cannot open a
transaction for another organisation or forget the user.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session
from services.api.kernel.auth import Principal


@asynccontextmanager
async def transaction(
    role: str, principal: Principal, **settings: str
) -> AsyncIterator[AsyncSession]:
    """Commits on normal exit, rolls back on an exception. `settings` are the
    other app.* values some policies read (libs.dataagent_db.session.EXTRA_SETTINGS)."""
    async with scoped_session(role, principal.org_id, principal.user_id, **settings) as session:
        yield session
