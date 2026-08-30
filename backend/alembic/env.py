import asyncio
import logging
import os
import sys
from logging.config import fileConfig
from pathlib import Path

# `libs.*` lives next to this directory (backend/libs, or /app/libs in the migrate
# image) — make env.py importable regardless of the caller's cwd/PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from alembic import context

from libs.basemodel.services_databaseconnector.postgres_orm.base import Base
import libs.basemodel.services_databaseconnector.postgres_orm  # noqa: F401 — registers all ORM classes

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if _db_url := os.environ.get("DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", _db_url)

target_metadata = Base.metadata

# Initial schema revision. If the DB has app tables but no alembic version,
# stamp to this rev — see _stamp_if_init_scripts_ran below.
_INITIAL_REVISION = "ff9ae8dcd7f7"

log = logging.getLogger("alembic.env")


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def _stamp_if_init_scripts_ran(conn) -> None:
    """Reconcile alembic state with SQL init scripts that ran at first boot.

    `deploy/docker/sql/*.sql` files mounted into Postgres `/docker-entrypoint-initdb.d/`
    create overlapping schema (some tables, some indexes) with the alembic
    initial migration. When the DB boots fresh, those run BEFORE alembic, so
    when alembic tries `CREATE INDEX idx_drift_events_resolved` it fails with
    DuplicateTable.

    Two source-of-truth schemas is a real bug worth untangling separately.
    Until then: if we detect a non-empty schema with no alembic_version, we
    stamp the initial revision as applied and let only NEWER migrations run.

    Logic:
      - alembic_version table exists AND has a row → normal upgrade
      - alembic_version missing/empty + DriftEvents (init-scripts marker) exists
        → stamp initial, then upgrade head (which only runs later revisions)
      - Fresh DB → normal upgrade (will create everything from scratch)
    """
    # Does the alembic_version table exist?
    has_alembic_table = await conn.scalar(text("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'alembic_version'
        )
    """))
    if has_alembic_table:
        version_count = await conn.scalar(text(
            "SELECT count(*) FROM alembic_version"
        ))
        if version_count and version_count > 0:
            log.info("alembic_version present — normal upgrade path")
            return

    # No alembic version. Did the SQL init scripts populate the schema?
    # DriftEvents is in the initial alembic revision; if it exists, init ran.
    # Marker MUST be a table only the ORM initial migration creates (PascalCase).
    # The lowercase `drift_events` comes from deploy/docker/sql/*.sql and does NOT imply
    # the ORM tables (Agents, …) exist — keying on it wrongly stamps the initial
    # revision and skips creating the ORM schema.
    has_init_schema = await conn.scalar(text("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'DriftEvents'
        )
    """))

    if not has_init_schema:
        log.info("fresh DB — initial migration will create schema")
        return

    log.warning(
        "app tables exist but alembic_version is empty — assuming SQL init "
        "scripts populated schema. Stamping %s and letting only newer "
        "revisions run.", _INITIAL_REVISION,
    )
    # Create alembic_version table + stamp by hand. Same effect as `alembic
    # stamp <rev>` but inline so we don't need a second container invocation.
    await conn.execute(text(
        "CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) PRIMARY KEY)"
    ))
    await conn.execute(text(
        "INSERT INTO alembic_version (version_num) VALUES (:rev) ON CONFLICT DO NOTHING"
    ), {"rev": _INITIAL_REVISION})


async def run_migrations_online() -> None:
    engine = create_async_engine(config.get_main_option("sqlalchemy.url"))
    async with engine.begin() as conn:
        await _stamp_if_init_scripts_ran(conn)
        await conn.run_sync(
            lambda sync_conn: context.configure(
                connection=sync_conn,
                target_metadata=target_metadata,
            )
        )
        await conn.run_sync(lambda _: context.run_migrations())
    await engine.dispose()


def run() -> None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        asyncio.run(run_migrations_online())


run()
