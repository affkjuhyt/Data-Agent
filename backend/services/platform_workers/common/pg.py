"""Postgres connection factory with startup wait.

``connect()`` blocks until Postgres accepts a connection (20 attempts, 5 s
apart — the same budget every worker had) and returns a connection with
``autocommit = False``, because each worker commits per unit of work and rolls
back on failure.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any

import psycopg2
import psycopg2.extras
from tenacity import (
    RetryError,
    Retrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from services.platform_workers.common.settings import PostgresSettings

log = logging.getLogger("platform-workers.pg")

DEFAULT_ATTEMPTS = 20
DEFAULT_WAIT_SECONDS = 5


def connect(
    settings: PostgresSettings | None = None,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> Any:
    settings = settings or PostgresSettings()
    kwargs = settings.connect_kwargs()

    def _build() -> Any:
        conn = psycopg2.connect(**kwargs)
        conn.autocommit = False
        return conn

    try:
        conn = Retrying(
            retry=retry_if_exception_type(psycopg2.OperationalError),
            wait=wait_fixed(wait_seconds),
            stop=stop_after_attempt(max(1, attempts)),
            before_sleep=before_sleep_log(log, logging.WARNING),
            reraise=False,
        )(_build)
    except RetryError as e:
        raise RuntimeError("PostgreSQL unavailable after retries") from e
    log.info("PostgreSQL connected")
    return conn


def reconnect(conn: Any, settings: PostgresSettings | None = None) -> Any:
    """Roll back, close (both best-effort) and open a fresh connection.

    The workers' recovery path after any DB error: the old connection may be
    in an aborted transaction or physically gone, and either way a fresh one
    is cheaper to reason about than an inspected one.
    """
    # Cleanup of an already-failed connection: nothing useful can be done
    # about a second failure here.
    for step in (conn.rollback, conn.close):
        with contextlib.suppress(Exception):
            step()
    return connect(settings)


def dict_rows(cur: Any) -> list[dict[str, Any]]:
    """Fetch the remaining rows of ``cur`` as dicts keyed by column name.

    Replaces the ``dict(zip(cols, row))`` idiom. Works on any cursor; a
    ``RealDictCursor`` already yields dicts and is passed through.
    """
    rows = cur.fetchall()
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return [dict(r) for r in rows]
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in rows]


def dict_cursor(conn: Any) -> Any:
    """A cursor whose rows are dicts (``psycopg2.extras.RealDictCursor``)."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
