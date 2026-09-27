import threading
from typing import Any

from psycopg2 import pool as pg_pool

try:
    from services.auth_api.infrastructure.config import OWNER_POOL_MAX, PG_DSN, POOL_MIN, log
except ModuleNotFoundError:
    from auth_api.infrastructure.config import OWNER_POOL_MAX, PG_DSN, POOL_MIN, log

owner_pool: pg_pool.ThreadedConnectionPool | None = None
owner_pool_lock = threading.Lock()


def get_owner_pool() -> pg_pool.ThreadedConnectionPool:
    global owner_pool
    if owner_pool is None:
        with owner_pool_lock:
            if owner_pool is None:
                if not PG_DSN:
                    raise RuntimeError("DATABASE_URL is not set (see deploy/k8s/secrets.env.example)")
                owner_pool = pg_pool.ThreadedConnectionPool(POOL_MIN, OWNER_POOL_MAX, PG_DSN)
    return owner_pool


class PooledConnection:
    def __init__(self, pool: pg_pool.ThreadedConnectionPool, conn: Any) -> None:
        object.__setattr__(self, "_pool", pool)
        object.__setattr__(self, "_conn", conn)

    def close(self) -> None:
        conn = object.__getattribute__(self, "_conn")
        pool = object.__getattribute__(self, "_pool")
        try:
            conn.rollback()
        except Exception:
            pass
        pool.putconn(conn)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_conn"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(object.__getattribute__(self, "_conn"), name, value)


def get_db() -> PooledConnection:
    pool = get_owner_pool()
    raw = pool.getconn()
    raw.autocommit = False
    return PooledConnection(pool, raw)


def ensure_settings_table() -> None:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS platform_settings (
                    key        varchar(100) PRIMARY KEY,
                    value      text NOT NULL,
                    updated_at timestamptz DEFAULT now()
                )
                """
            )
            cur.execute(
                "ALTER TABLE key_rotations DROP CONSTRAINT IF EXISTS key_rotations_triggered_by_check"
            )
            cur.execute(
                """
                ALTER TABLE key_rotations ADD CONSTRAINT key_rotations_triggered_by_check
                  CHECK (triggered_by IN ('SCHEDULED','MANUAL','PANIC','REVEAL'))
                """
            )
            cur.execute(
                """
                INSERT INTO platform_settings (key, value, updated_at)
                VALUES ('ip_allowlist_mode', 'allow_all', now())
                ON CONFLICT (key) DO NOTHING
                """
            )
            cur.execute("ALTER TABLE key_rotations ADD COLUMN IF NOT EXISTS access_reason text")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS compliance_settings (
                    tenant_id           varchar(64)  PRIMARY KEY,
                    pii_masking         boolean      NOT NULL DEFAULT false,
                    pii_types           text         NOT NULL DEFAULT 'EMAIL,PHONE,CCCD,CMND,CREDIT_CARD,IP',
                    residency_region    varchar(32)  NOT NULL DEFAULT 'Asia-SE1',
                    residency_standards text         NOT NULL DEFAULT 'GDPR',
                    retention_days      integer      NOT NULL DEFAULT 365,
                    retention_lock      boolean      NOT NULL DEFAULT false,
                    zero_trust          boolean      NOT NULL DEFAULT false,
                    audit_residency     boolean      NOT NULL DEFAULT false,
                    updated_at          timestamptz  DEFAULT now(),
                    updated_by          varchar(128) DEFAULT ''
                )
                """
            )
        conn.commit()
    except Exception as exc:
        log.warning("schema_migration_failed", error=str(exc))
    finally:
        conn.close()
