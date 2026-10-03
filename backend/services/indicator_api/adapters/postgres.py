"""Postgres (registry, governance, knowledge schemas)."""

from __future__ import annotations

import os

import psycopg2

PING_TIMEOUT_S = 2

# The tenant-scoped (RLS) DSN libs/shared/db_context.py already reads. The V2
# service roles (governance_svc, knowledge_svc, …) have no password until
# NOP-214; the adapter moves to them then.
DSN_ENV = "APP_DATABASE_URL"


class PostgresAdapter:
    name = "postgres"

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    @classmethod
    def from_env(cls) -> PostgresAdapter | None:
        dsn = os.environ.get(DSN_ENV, "").strip()
        return cls(dsn) if dsn else None

    def ping(self) -> None:
        conn = psycopg2.connect(self._dsn, connect_timeout=PING_TIMEOUT_S)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            conn.close()
