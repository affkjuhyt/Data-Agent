import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from collections.abc import Iterator
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from libs.basemodel.services_databaseconnector.postgres_orm.auth_api_orm import (
    AuthPlatformSettingORM,
)

try:
    from services.auth_api.infrastructure.config import OWNER_POOL_MAX, PG_DSN, log
except ModuleNotFoundError:
    from auth_api.infrastructure.config import OWNER_POOL_MAX, PG_DSN, log

SessionLocal: sessionmaker[Session] | None = None
session_lock = threading.Lock()


def _sync_url() -> str:
    if not PG_DSN:
        raise RuntimeError("DATABASE_URL is not set (see deploy/k8s/secrets.env.example)")
    if PG_DSN.startswith("postgres://"):
        return PG_DSN.replace("postgres://", "postgresql://", 1)
    return PG_DSN


def get_session_factory() -> sessionmaker[Session]:
    global SessionLocal
    if SessionLocal is None:
        with session_lock:
            if SessionLocal is None:
                engine = create_engine(
                    _sync_url(),
                    pool_size=OWNER_POOL_MAX,
                    max_overflow=0,
                    pool_pre_ping=True,
                    future=True,
                )
                SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return SessionLocal


@contextmanager
def get_session() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def orm_to_dict(instance: Any) -> dict[str, Any]:
    return {
        attr.key: getattr(instance, attr.key)
        for attr in sa_inspect(instance.__class__).column_attrs
    }


def ensure_settings_table() -> None:
    defaults = {
        "ip_allowlist_mode": "allow_all",
        "vault_auto_rotation": "false",
        "vault_pii_access_log": "false",
    }
    try:
        with get_session() as session:
            for key, value in defaults.items():
                stmt = (
                    pg_insert(AuthPlatformSettingORM)
                    .values(key=key, value=value, updated_at=datetime.now(timezone.utc))
                    .on_conflict_do_nothing(index_elements=[AuthPlatformSettingORM.key])
                )
                session.execute(stmt)
            session.execute(
                select(AuthPlatformSettingORM.key)
                .where(AuthPlatformSettingORM.key == "ip_allowlist_mode")
                .limit(1)
            )
    except Exception as exc:
        log.warning("schema_migration_failed", error=str(exc))
