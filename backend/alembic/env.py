import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import MetaData
from sqlalchemy import create_engine
from alembic import context

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if _db_url := os.environ.get("DATABASE_URL"):
    # The generated baseline is a PostgreSQL script containing functions,
    # triggers and DO blocks.  asyncpg's prepared-statement protocol rejects
    # multiple commands in one call, while psycopg executes the script as one
    # transactional command.  Keep this choice local to the migration image;
    # application services continue to use their async drivers.
    _db_url = _db_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if _db_url.startswith("postgresql://"):
        _db_url = _db_url.replace("postgresql://", "postgresql+psycopg://", 1)
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


def run_migrations_online() -> None:
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    with engine.begin() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        context.run_migrations()
    engine.dispose()


def run() -> None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()


run()
