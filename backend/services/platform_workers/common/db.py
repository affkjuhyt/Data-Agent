"""SQLAlchemy session factory for platform workers.

``make_session_factory()`` blocks until Postgres accepts a connection (20
attempts, 5 s apart — the budget the psycopg2 factory had before the SQLAlchemy
rewrite). Without the wait a worker that starts before Postgres crashes on its
first query and only recovers through the pod's restart backoff.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from tenacity import (
    RetryError,
    Retrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from services.platform_workers.common.settings import PostgresSettings

log = logging.getLogger("platform-workers.db")

_SET_TENANT = text("SELECT set_config('app.tenant_id', :tenant_id, true)")

DEFAULT_ATTEMPTS = 20
DEFAULT_WAIT_SECONDS = 5


def wait_until_ready(
    engine: Engine, *, attempts: int = DEFAULT_ATTEMPTS, wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> None:
    def _probe() -> None:
        with engine.connect():
            pass

    try:
        Retrying(
            retry=retry_if_exception_type(OperationalError),
            wait=wait_fixed(wait_seconds),
            stop=stop_after_attempt(max(1, attempts)),
            before_sleep=before_sleep_log(log, logging.WARNING),
            reraise=False,
        )(_probe)
    except RetryError as e:
        raise RuntimeError("PostgreSQL unavailable after retries") from e
    log.info("PostgreSQL connected")


def make_session_factory(
    settings: PostgresSettings | None = None,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> sessionmaker[Session]:
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
    wait_until_ready(engine, attempts=attempts, wait_seconds=wait_seconds)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def set_tenant(session: Session, tenant_id: str | uuid.UUID) -> None:
    """Scope the session's current transaction to one tenant.

    The workers connect as app_user (NOBYPASSRLS), and every tenant table's
    row-level policy compares tenant_id with app.tenant_id: before this call a
    read sees no rows and an insert is rejected. The setting is transaction-
    local, so it ends at commit/rollback, and calling it again switches tenant.
    Pending rows are flushed first, while the tenant they were added under is
    still the one in force — otherwise the policy checks them against the next.
    Raises ValueError for a tenant that is not a UUID.
    """
    session.flush()
    session.execute(_SET_TENANT, {"tenant_id": str(uuid.UUID(str(tenant_id)))})
