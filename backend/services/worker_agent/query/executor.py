"""Running one query in place on a source (query in place by default, invariant 15) and recording it.

Every run is a query.query_run row, written as svc_query: `running` before the source is touched,
then `succeeded`, `failed` or `timeout` with the row count, the duration and a hash of the result,
plus up to 1 000 rows in query.query_result (30 days) for the answer's lineage and table preview.

The source is reached with the credentials at its secret_ref, in a READ ONLY transaction with the
source's own statement timeout (query_timeout_s, AC-01-01-03), after the guard admitted the SQL;
at most BR-03's 10 000 rows come back, flagged `truncated` when there were more. The number of
runs per source in this process is capped by source.max_concurrency.

PostgreSQL, MySQL and SQL Server sources are wired. The latter two use synchronous DB-API drivers
inside a bounded worker thread: neither driver has to run on the worker-agent event loop.
"""

from __future__ import annotations

import asyncio
import datetime
import decimal
import hashlib
import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import asyncpg
from sqlalchemy import update

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import query as q
from services.worker_agent.query.guard import SqlRejected, capped, check_read_only, dialect_of
from services.worker_agent.query.secrets import SecretReader, SecretUnavailable

ROLE = "svc_query"
MAX_ROWS = 10_000  # BR-03
STORED_ROWS = 1_000  # query.query_result keeps at most this many (D4)
RESULT_DAYS = 30
SSL_MODES = ("disable", "require", "verify-ca", "verify-full")

CallerKind = Literal[
    "question",
    "tile",
    "skill_run",
    "agent_run",
    "pipeline",
    "period",
    "eval",
    "receipt_replay",
    "ad_hoc",
]

_slots: dict[uuid.UUID, asyncio.Semaphore] = {}


def _slot(source: catalog.Source) -> asyncio.Semaphore:
    if source.id not in _slots:
        _slots[source.id] = asyncio.Semaphore(max(source.max_concurrency or 1, 1))
    return _slots[source.id]


@dataclass
class QueryOutcome:
    query_run_id: uuid.UUID
    status: Literal["succeeded", "failed", "timeout"]
    columns: list[dict[str, str]] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    result_hash: str | None = None
    sql: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "succeeded"


class SourceDriverError(RuntimeError):
    """A source driver failed without exposing credentials or source SQL."""


def _plain(value: Any) -> Any:
    """A JSON value that keeps the source's precision: amounts as decimal strings (A1.1)."""
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return None  # binary columns are not shown
    return value


def _hash(columns: Sequence[dict[str, str]], rows: Sequence[Sequence[Any]]) -> str:
    body = json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


async def _run_postgres(
    source: catalog.Source, user: str, password: str, sql: str, timeout_s: int
) -> tuple[list[dict[str, str]], list[list[Any]]]:
    options = source.conn_options or {}
    # Encrypted and verified unless the source says otherwise: the credentials and the rows cross
    # the network. A source on a private network without TLS states sslmode=disable explicitly.
    sslmode = str(options.get("sslmode") or "verify-full")
    if sslmode not in SSL_MODES:
        raise SecretUnavailable(f"sslmode {sslmode} is not one of {', '.join(SSL_MODES)}")
    conn = await asyncpg.connect(
        host=source.host,
        port=source.port or 5432,
        database=source.database_name,
        user=user,
        password=password,
        ssl=sslmode,
        timeout=min(timeout_s, 30),
        statement_cache_size=0,
        server_settings={"application_name": "dataagent-worker-agent"},
    )
    try:
        async with conn.transaction(readonly=True):
            await conn.execute(f"SET LOCAL statement_timeout = {int(timeout_s) * 1000}")
            if options.get("schema"):
                await conn.execute(
                    "SELECT set_config('search_path', $1, true)", str(options["schema"])
                )
            stmt = await conn.prepare(sql)
            columns = [{"name": a.name, "type": a.type.name} for a in stmt.get_attributes()]
            records = await stmt.fetch()
    finally:
        await conn.close()
    return columns, [[_plain(v) for v in r] for r in records]


def _rows(cursor: Any) -> tuple[list[dict[str, str]], list[list[Any]]]:
    description = cursor.description or ()
    columns = [{"name": str(item[0]), "type": str(item[1] or "unknown")} for item in description]
    return columns, [[_plain(value) for value in row] for row in cursor.fetchall()]


def _mysql_sync(
    source: catalog.Source, user: str, password: str, sql: str, timeout_s: int
) -> tuple[list[dict[str, str]], list[list[Any]]]:
    try:
        import pymysql

        options = source.conn_options or {}
        connect_options: dict[str, Any] = {
            "host": source.host,
            "port": source.port or 3306,
            "user": user,
            "password": password,
            "database": source.database_name,
            "connect_timeout": min(timeout_s, 30),
            "read_timeout": timeout_s,
            "write_timeout": timeout_s,
            "charset": str(options.get("charset") or "utf8mb4"),
        }
        if options.get("sslmode") not in (None, "disable"):
            ca = options.get("ssl_ca")
            if not ca:
                raise SourceDriverError("mysql TLS requires conn_options.ssl_ca")
            connect_options["ssl"] = {
                "ca": str(ca),
                "check_hostname": options["sslmode"] == "verify-full",
            }
        conn = pymysql.connect(**connect_options)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute("START TRANSACTION READ ONLY")
                if options.get("schema"):
                    cursor.execute(f"USE `{str(options['schema']).replace('`', '``')}`")
                cursor.execute(sql)
                result = _rows(cursor)
            conn.rollback()
            return result
        finally:
            conn.close()
    except SourceDriverError:
        raise
    except Exception as exc:
        raise SourceDriverError(f"mysql query failed ({type(exc).__name__})") from exc


async def _run_mysql(
    source: catalog.Source, user: str, password: str, sql: str, timeout_s: int
) -> tuple[list[dict[str, str]], list[list[Any]]]:
    return await asyncio.to_thread(_mysql_sync, source, user, password, sql, timeout_s)


def _sqlserver_sync(
    source: catalog.Source, user: str, password: str, sql: str, timeout_s: int
) -> tuple[list[dict[str, str]], list[list[Any]]]:
    try:
        import pymssql

        options = source.conn_options or {}
        conn = pymssql.connect(
            server=str(source.host),
            port=int(source.port or 1433),
            user=user,
            password=password,
            database=source.database_name or "master",
            login_timeout=min(timeout_s, 30),
            timeout=timeout_s,
            appname="dataagent-worker-agent",
            tds_version=str(options.get("tds_version") or "7.4"),
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute("BEGIN TRANSACTION")
                cursor.execute(sql)
                result = _rows(cursor)
            conn.rollback()
            return result
        finally:
            conn.close()
    except SourceDriverError:
        raise
    except Exception as exc:
        raise SourceDriverError(f"sqlserver query failed ({type(exc).__name__})") from exc


async def _run_sqlserver(
    source: catalog.Source, user: str, password: str, sql: str, timeout_s: int
) -> tuple[list[dict[str, str]], list[list[Any]]]:
    return await asyncio.to_thread(_sqlserver_sync, source, user, password, sql, timeout_s)


def driver_for(kind: str) -> Any:
    drivers = {
        "postgresql": _run_postgres,
        "mysql": _run_mysql,
        "sqlserver": _run_sqlserver,
    }
    try:
        return drivers[kind]
    except KeyError as exc:
        raise SecretUnavailable(f"queries on a {kind} source are not wired yet") from exc


async def run_query(
    *,
    org_id: uuid.UUID,
    data_space_id: uuid.UUID,
    principal_id: uuid.UUID,
    caller_kind: CallerKind,
    caller_id: uuid.UUID,
    source: catalog.Source,
    sql: str,
    secrets: SecretReader,
    metric_version_ids: Sequence[uuid.UUID] = (),
    as_of: datetime.date | None = None,
    keep_rows: bool = True,
) -> QueryOutcome:
    """`keep_rows=False` records the run (SQL, hashes, counts) but stores none of its rows: the
    private channel of UC-16-02 keeps no answer data in a table (BR-193)."""
    run_id = uuid7()
    started = time.monotonic()
    outcome = QueryOutcome(query_run_id=run_id, status="failed", sql=sql)
    executed = sql
    try:
        dialect = dialect_of(source.kind)
        executed = capped(check_read_only(sql, dialect), dialect, MAX_ROWS + 1)
    except SqlRejected as exc:
        outcome.error = str(exc)
    async with scoped_session(ROLE, org_id, principal_id) as session:
        session.add(
            q.QueryRun(
                id=run_id,
                org_id=org_id,
                data_space_id=data_space_id,
                caller_kind=caller_kind,
                caller_id=caller_id,
                principal_id=principal_id,
                source_id=source.id,
                route="live",
                sql_generated=sql,
                sql_executed=executed,
                metric_version_ids=list(metric_version_ids),
                as_of=as_of,
                status="failed" if outcome.error else "running",
                error=outcome.error,
            )
        )
    if outcome.error:
        return outcome

    timeout_s = int(source.query_timeout_s or 120)
    try:
        credentials = await secrets.read(org_id, source.secret_ref)
        async with _slot(source):
            columns, rows = await asyncio.wait_for(
                driver_for(source.kind)(
                    source, credentials.user, credentials.password, executed, timeout_s
                ),
                timeout=timeout_s + 5,
            )
        outcome.truncated = len(rows) > MAX_ROWS
        outcome.rows = rows[:MAX_ROWS]
        outcome.columns = columns
        outcome.result_hash = _hash(columns, outcome.rows)
        outcome.status = "succeeded"
    except (asyncpg.QueryCanceledError, TimeoutError):
        outcome.status = "timeout"
        outcome.error = f"the query ran longer than the source allows ({timeout_s} s)"
    except SecretUnavailable as exc:
        outcome.error = str(exc)
    except SourceDriverError as exc:
        outcome.error = str(exc)
    except (asyncpg.PostgresError, OSError) as exc:
        # The source's own message, without the SQL text or values it may quote.
        outcome.error = f"{type(exc).__name__}: {getattr(exc, 'sqlstate', '') or 'connection'}"

    async with scoped_session(ROLE, org_id, principal_id) as session:
        await session.execute(
            update(q.QueryRun)
            .where(q.QueryRun.id == run_id)
            .values(
                status=outcome.status,
                error=outcome.error,
                row_count=len(outcome.rows) if outcome.ok else None,
                result_hash=outcome.result_hash,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )
        if outcome.ok and keep_rows:
            session.add(
                q.QueryResult(
                    id=uuid7(),
                    org_id=org_id,
                    query_run_id=run_id,
                    columns=outcome.columns,
                    rows=outcome.rows[:STORED_ROWS],
                    truncated=outcome.truncated or len(outcome.rows) > STORED_ROWS,
                    expires_at=datetime.datetime.now(datetime.UTC)
                    + datetime.timedelta(days=RESULT_DAYS),
                )
            )
    return outcome
