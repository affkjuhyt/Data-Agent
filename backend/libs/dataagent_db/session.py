"""Connections to the V3 database: one engine per service role, one scoped transaction per unit of work.

Each module of the application connects as its own role `svc_<schema>` (tab 3 D5.2), so the
database itself limits what it may read and write. Row-level security reads the organisation (and,
for some schemas, the user and a few switches) from transaction-local settings; `scoped_session`
sets them with bound parameters at the start of every transaction (ADR-31), so a pooled
connection never carries another request's organisation.

Engine URLs come from the environment, one per role: DATAAGENT_DB_URL__SVC_QA, … The plain
`postgresql://` form is accepted and switched to asyncpg. The prepared-statement cache is off
because connections go through PgBouncer in transaction mode (ADR-31).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from functools import cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

ENV_PREFIX = "DATAAGENT_DB_URL__"

# Settings the policies of tab 3b read besides app.org_id and app.user_id; anything else is refused
# so a typo cannot silently leave a policy without its input.
EXTRA_SETTINGS = frozenset(
    {"knowledge_reader", "data_space_ids", "clearance", "exec_workflow", "exec_admin", "caller_id"}
)

# context.classification_rank('internal'). No per-reader clearance is stored yet, so a reader
# sees public and internal and nothing higher (BR-163). The context API's reader_settings uses
# this same rank.
READER_CLEARANCE = "1"


def reader_settings_for(space_ids: Sequence[uuid.UUID]) -> dict[str, str]:
    """The settings `reader_settings` puts on a reader transaction for these Data Spaces.

    `knowledge_reader_allows` is false when `data_space_ids` or `clearance` is unset, so a
    reader sees no knowledge row. `knowledge_reader` is the switch that makes svc_context read
    as that reader; roles that already hold context_read (svc_qa) do not need it, and setting
    it does not widen them. An empty space list fails closed.
    """
    listed = "{" + ",".join(str(i) for i in sorted(space_ids, key=str)) + "}"
    return {
        "knowledge_reader": "on",
        "data_space_ids": listed,
        "clearance": READER_CLEARANCE,
    }


async def apply_app_settings(session: AsyncSession, settings: Mapping[str, str]) -> None:
    """Transaction-local `app.*` values the policies read. Unknown names are refused."""
    unknown = set(settings) - EXTRA_SETTINGS
    if unknown:
        raise ValueError(f"unknown app settings: {sorted(unknown)}")
    for name, value in settings.items():
        await session.execute(
            text("SELECT set_config(:name, :value, true)"),
            {"name": f"app.{name}", "value": value},
        )


def database_url(role: str) -> str:
    name = f"{ENV_PREFIX}{role.upper()}"
    url = os.environ.get(name)
    if not url:
        raise RuntimeError(f"{name} is not set; it holds the connection URL for role {role}")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


@cache
def engine_for(role: str) -> AsyncEngine:
    """One engine (and pool) per role, created on first use."""
    return create_async_engine(
        database_url(role),
        pool_pre_ping=True,
        connect_args={"statement_cache_size": 0},
    )


@asynccontextmanager
async def scoped_session(
    role: str,
    org_id: uuid.UUID | str,
    user_id: uuid.UUID | str | None = None,
    **settings: str,
) -> AsyncIterator[AsyncSession]:
    """A session inside one transaction as `role`, scoped to `org_id` (and `user_id`).

    Commits when the block ends, rolls back when it raises. Extra keyword arguments set the other
    `app.*` settings the policies read, e.g. `knowledge_reader="on"`.
    """
    unknown = set(settings) - EXTRA_SETTINGS
    if unknown:
        raise ValueError(f"unknown app settings: {sorted(unknown)}")
    async with (
        AsyncSession(engine_for(role), expire_on_commit=False) as session,
        session.begin(),
    ):
        await session.execute(
            text(
                "SELECT set_config('app.org_id', :org, true), set_config('app.user_id', :uid, true)"
            ),
            {"org": str(org_id), "uid": str(user_id) if user_id else ""},
        )
        await apply_app_settings(session, settings)
        yield session


# Roles that act across organisations by design and so run without app.org_id: the interim outbox
# relay (Debezium's job, ADR-06). Its grants stop at the outbox tables (backend/schema gen_ddl.py).
# svc_outbox_relay_audit is not another role but a second connection key for svc_outbox_relay, in
# the audit database (dataagent_audit), which has its own outbox and its own URL. svc_eval is here
# only for the nightly scheduler's call of eval.nightly_eval_sets() (SECURITY DEFINER): without
# app.org_id, row-level security lets it read no eval table itself.
UNSCOPED_ROLES = frozenset({"svc_outbox_relay", "svc_outbox_relay_audit", "svc_eval"})


@asynccontextmanager
async def unscoped_session(role: str) -> AsyncIterator[AsyncSession]:
    """A transaction as `role` with no organisation set; only for UNSCOPED_ROLES."""
    if role not in UNSCOPED_ROLES:
        raise ValueError(f"{role} works within one organisation; use scoped_session")
    async with (
        AsyncSession(engine_for(role), expire_on_commit=False) as session,
        session.begin(),
    ):
        yield session
