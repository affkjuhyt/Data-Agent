"""SQLAlchemy session factory for platform workers."""
from __future__ import annotations

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import Session, sessionmaker

from services.platform_workers.common.settings import PostgresSettings


def make_session_factory(settings: PostgresSettings | None = None) -> sessionmaker[Session]:
    settings = settings or PostgresSettings()
    url = URL.create(
        "postgresql+psycopg2",
        username=settings.user,
        password=settings.password,
        host=settings.host,
        port=settings.port,
        database=settings.name,
    )
    engine = create_engine(url, pool_pre_ping=True, future=True)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
