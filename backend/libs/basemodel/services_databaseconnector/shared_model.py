from __future__ import annotations

import os
from typing import TypeVar, Generic
from pydantic import BaseModel

T = TypeVar("T")


class ResponseModel(BaseModel, Generic[T]):
    code: int = 200
    data: T | None = None
    error: str | None = None

class RetryConfig(BaseModel):
    count: int = 5

class HealthCheckLoopConfig(BaseModel):
    count: int = 5
    interval: int = 5
    timeout_for_health_check: int = 5


class PoolConfig(BaseModel):
    """Connection-pool sizing. Pass one in, or let from_env() read <PREFIX>_* vars."""
    max_connections: int = 10
    max_keepalive: int = 10
    max_overflow: int = 10
    pool_timeout: int = 30
    pool_recycle: int = 1800
    statement_timeout: float | None = None
    # 0 disables asyncpg's prepared-statement cache — required behind PgBouncer
    # in transaction pooling mode.
    statement_cache_size: int | None = None

    @classmethod
    def from_env(cls, prefix: str) -> "PoolConfig":
        def _int(key: str, default: int) -> int:
            raw = os.getenv(f"{prefix}_{key}", "").strip()
            return int(raw) if raw else default

        def _opt_int(key: str) -> int | None:
            raw = os.getenv(f"{prefix}_{key}", "").strip()
            return int(raw) if raw else None

        stmt = os.getenv(f"{prefix}_STATEMENT_TIMEOUT", "").strip()
        return cls(
            max_connections=_int("MAX_CONNECTIONS", 10),
            max_keepalive=_int("MAX_KEEPALIVE", 10),
            max_overflow=_int("MAX_OVERFLOW", 10),
            pool_timeout=_int("POOL_TIMEOUT", 30),
            pool_recycle=_int("POOL_RECYCLE", 1800),
            statement_timeout=float(stmt) if stmt else None,
            statement_cache_size=_opt_int("STATEMENT_CACHE_SIZE"),
        )
