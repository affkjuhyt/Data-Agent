import asyncio
import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine
from alembic import context

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if _db_url := os.environ.get("DATABASE_URL"):
    # DATABASE_URL is the plain "postgresql://" scheme project-wide (psycopg2
    # services connect with it directly) — create_async_engine below needs the
    # asyncpg dialect, so normalise it the same way
    # libs/database_connector/postgres_connector.py does for its own async engine.
    if _db_url.startswith("postgresql://"):
        _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    config.set_main_option("sqlalchemy.url", _db_url)

target_metadata = MetaData()


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


async def run_migrations_online() -> None:
    engine = create_async_engine(config.get_main_option("sqlalchemy.url"))
    async with engine.begin() as conn:
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
