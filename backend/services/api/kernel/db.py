"""Database access: one pool per module role, every query inside an org transaction.

ADR-28: each module owns one schema and connects with its own role
(`svc_<schema>`), so a module cannot read another module's tables except
through the `_read` grants the DDL gives it. Each role gets its own engine here,
configured as `DATAAGENT_DB_URL__SVC_<SCHEMA>` (the password comes from OpenBao through
the deploy, never from this repo).

ADR-31: Postgres sits behind pgbouncer in transaction pooling, so the
organisation is set with `set_config(..., true)` (the function form of SET LOCAL,
which takes bind parameters) at the start of every transaction and dies with
it. A session-level SET would leak to the next organisation served by the same
server connection. asyncpg's statement cache is off for the same reason: a
prepared statement does not follow its connection across the pool.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from services.api.kernel.auth import Principal

ENV_PREFIX = "DATAAGENT_DB_URL__"

_SET_SCOPE = text(
    "SELECT set_config('app.org_id', :org_id, true), set_config('app.user_id', :user_id, true)"
)


def role_urls_from_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """`DATAAGENT_DB_URL__SVC_QA=postgresql+asyncpg://…` → {"svc_qa": "postgresql+asyncpg://…"}."""
    env = os.environ if environ is None else environ
    return {
        key[len(ENV_PREFIX) :].lower(): url
        for key, url in env.items()
        if key.startswith(ENV_PREFIX) and url
    }


class Databases:
    def __init__(self, role_urls: Mapping[str, str]) -> None:
        self._urls = dict(role_urls)
        self._engines: dict[str, AsyncEngine] = {}

    def engine(self, role: str) -> AsyncEngine:
        if role not in self._engines:
            url = self._urls.get(role)
            if url is None:
                raise RuntimeError(
                    f"no database URL configured for role {role} ({ENV_PREFIX}{role.upper()})"
                )
            self._engines[role] = create_async_engine(
                url,
                pool_pre_ping=True,
                connect_args={"statement_cache_size": 0},
            )
        return self._engines[role]

    @asynccontextmanager
    async def transaction(self, role: str, principal: Principal) -> AsyncIterator[AsyncSession]:
        """A transaction as `role`, scoped to the caller's organisation and user.

        Commits on normal exit, rolls back on an exception. Nothing in the api
        opens a session any other way, so no query runs without app.org_id.
        """
        async with (
            AsyncSession(self.engine(role), expire_on_commit=False) as session,
            session.begin(),
        ):
            await session.execute(
                _SET_SCOPE, {"org_id": str(principal.org_id), "user_id": principal.user_id}
            )
            yield session

    async def dispose(self) -> None:
        for engine in self._engines.values():
            await engine.dispose()
        self._engines.clear()
