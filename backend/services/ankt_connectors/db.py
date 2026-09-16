"""The one way this package talks to Postgres.

Written against the SQLAlchemy `AsyncSession` that `PostgresClient.get_client`
yields, because that is what every service here already holds — and because
`get_client(tenant_id)` sets `app.tenant_id` transaction-locally, which is what
makes the RLS policies on the ankt tables do anything.

An earlier version of this package spoke asyncpg with `$1` placeholders, which
worked in a throwaway container and returned HTTP 500 the moment the API used
it: `PostgresClient` has no `.execute`. One client, one parameter style, no
second path to drift.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text


async def fetch_all(session: Any, sql: str, **params: Any) -> list[dict[str, Any]]:
    result = await session.execute(text(sql), params)
    return [dict(row) for row in result.mappings()]


async def fetch_one(session: Any, sql: str, **params: Any) -> dict[str, Any] | None:
    rows = await fetch_all(session, sql, **params)
    return rows[0] if rows else None


async def fetch_value(session: Any, sql: str, **params: Any) -> Any:
    result = await session.execute(text(sql), params)
    return result.scalar()


async def execute(session: Any, sql: str, **params: Any) -> None:
    await session.execute(text(sql), params)


async def commit(session: Any, tenant_id: Any) -> None:
    """Commit, then put `app.tenant_id` back.

    `get_client(tenant_id)` sets it with `is_local => true`, which means the
    setting dies with the transaction. Anything that commits mid-way — and this
    package does, so a run row is visible while the run is still going — starts
    the next transaction with no tenant, and the RLS WITH CHECK on every ankt
    table then rejects the next insert.

    Invisible under `admin`, which bypasses RLS: this only surfaced once the
    service role (`kb_backend`, NOBYPASSRLS) reached the code, as
    "new row violates row-level security policy" three statements later.
    """
    await session.commit()
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )


async def rollback(session: Any, tenant_id: Any) -> None:
    """Recover a failed transaction so the run's outcome can still be written.

    Same reason as `commit`: a rollback also discards the tenant setting, and
    the UPDATE that records the failure would then fail too — leaving a run
    stuck on `running` forever with nothing saying why.
    """
    await session.rollback()
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
