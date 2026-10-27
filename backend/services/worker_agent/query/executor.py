"""Running one query in place on a source (query in place by default, invariant 15) and recording it.

Every run is a query.query_run row, written as svc_query: `running` before the source is touched,
then `succeeded`, `failed` or `timeout` with the row count, the duration and a hash of the result,
plus up to 1 000 rows in query.query_result (30 days) for the answer's lineage and table preview.

The source is reached with the credentials at its secret_ref, in a transaction that is rolled back,
with the source's own statement timeout (query_timeout_s, AC-01-01-03), after the guard admitted
the SQL. PostgreSQL and MySQL also mark that transaction READ ONLY; SQL Server has no read-only
transaction, so there the guard and the rollback are the only barriers and the source login must
be read-only (db_datareader). At most BR-03's 10 000 rows come back, flagged `truncated` when
there were more. The number of runs per source in this process is capped by
source.max_concurrency.

PostgreSQL, MySQL and SQL Server sources are wired. The latter two use synchronous DB-API drivers
inside a bounded worker thread: neither driver has to run on the worker-agent event loop.

A result is reused for a while (the Holistics review: cache by the generated SQL). The key is
what decides the rows: the Data Space and the source, the SQL as executed (after the row policies
and scopes bound the asker's attributes into it, and with its row cap), the column and mask
policies applied to the result (a run from before one of them last changed is not reused), and
the as-of date. A succeeded run with that key, younger than QUERY_CACHE_TTL_S (600 s;
0 turns it off) and whose stored result is whole (at most STORED_ROWS, not truncated), answers
without touching the source: the new run is recorded as usual, with that run named in result_ref
("query_run:<id>"), and its own copy of the rows. Never reused: a private run (nothing of it is
stored), and the callers that must read the source now: a tile refresh (UC-02-02), a receipt
replay (it is how a changed source is detected), a period close, a pipeline, and the internal
workspace (read through the asker's own session).
"""

from __future__ import annotations

import asyncio
import datetime
import decimal
import hashlib
import json
import os
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

import asyncpg
from sqlalchemy import select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, governance, org
from libs.dataagent_db.models import query as q
from libs.sql_guard import (
    SqlRejected,
    capped,
    check_read_only,
    dialect_of,
    tables_of,
)
from services.worker_agent.query.scope import (
    active_policies,
    active_row_scopes,
    agent_owner,
    apply_output_policies,
    covered_columns,
    datasets_read,
    default_schema,
    rewrite_access_policies,
    rewrite_column_policies,
    rewrite_row_scopes,
)
from services.worker_agent.query.secrets import SecretReader, SecretUnavailable

ROLE = "svc_query"
INTERNAL_WORKSPACE = "internal://workspace"
MAX_ROWS = 10_000  # BR-03
# PostgreSQL type OIDs as asyncpg reports them, named as asyncpg's own attributes name them, so
# the workspace path and the direct path describe a column the same way.
_PG_OIDS = {
    16: "bool",
    20: "int8",
    21: "int2",
    23: "int4",
    25: "text",
    700: "float4",
    701: "float8",
    790: "money",
    1043: "varchar",
    1082: "date",
    1114: "timestamp",
    1184: "timestamptz",
    1700: "numeric",
}


def _pg_type(code: Any) -> str:
    if isinstance(code, int):
        return _PG_OIDS.get(code, "unknown")
    return str(code or "unknown")


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

CACHEABLE_CALLERS = frozenset({"question", "agent_run", "skill_run", "eval", "ad_hoc"})
CACHE_PREFIX = "query_run:"


def cache_ttl() -> datetime.timedelta:
    return datetime.timedelta(seconds=max(0, int(os.environ.get("QUERY_CACHE_TTL_S", "600"))))


async def _cached(
    session: Any,
    *,
    data_space_id: uuid.UUID,
    source_id: uuid.UUID,
    executed: str,
    policies: Sequence[governance.AccessPolicy],
    as_of: datetime.date | None,
) -> tuple[q.QueryRun, q.QueryResult] | None:
    """The newest succeeded run with the same key inside the TTL, with its whole stored result.

    The policies are compared by id and by when they last changed: a run from before a policy was
    edited is not reused, whatever its id (a mask method or a column list changed in place)."""
    ttl = cache_ttl()
    if not ttl:
        return None
    since = datetime.datetime.now(datetime.UTC) - ttl
    changed = max((p.updated_at for p in policies if p.updated_at is not None), default=None)
    if changed is not None and changed > since:
        since = changed
    found = (
        await session.execute(
            select(q.QueryRun, q.QueryResult)
            .join(q.QueryResult, q.QueryResult.query_run_id == q.QueryRun.id)
            .where(
                q.QueryRun.started_at > since,
                q.QueryRun.data_space_id == data_space_id,
                q.QueryRun.source_id == source_id,
                q.QueryRun.status == "succeeded",
                q.QueryRun.sql_executed == executed,
                q.QueryRun.as_of.is_(None) if as_of is None else q.QueryRun.as_of == as_of,
                q.QueryResult.truncated.is_(False),
            )
            .order_by(q.QueryRun.started_at.desc())
            .limit(5)
        )
    ).all()
    wanted = sorted(str(p.id) for p in policies)
    return next(
        (
            (run, result)
            for run, result in found
            if sorted(map(str, run.policies_applied or [])) == wanted
        ),
        None,
    )


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
            # Exactly the one schema the table check resolved unqualified names to (scope.py),
            # quoted as one identifier: not the login's default path, not a list.
            await conn.execute(
                "SELECT set_config('search_path', quote_ident($1), true)",
                default_schema(source) or "public",
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
                # No READ ONLY transaction exists in T-SQL: the rollback below undoes a write the
                # guard missed, and the source login is expected to be db_datareader.
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


def _stored_error(outcome: QueryOutcome, private: bool) -> str | None:
    if outcome.error is None or not private:
        return outcome.error
    return f"private run {outcome.status}"


@dataclass
class Bound:
    """The SQL as `principal_id` may run it, and the policies that shaped it."""

    executed: str
    policies: list[governance.AccessPolicy]


async def bound_sql(
    session: Any,
    *,
    sql: str,
    source: catalog.Source,
    data_space_id: uuid.UUID,
    principal_id: uuid.UUID,
    caller_kind: str | None,
    limit: int = MAX_ROWS,
) -> Bound:
    """`sql` with the asker's limits written into it: the row cap, the column and mask policies,
    the row policies with their attributes bound, and the row scopes. Two people for whom it gives
    the same text read the same rows (session_reports' rights check relies on that). Raises
    SqlRejected for what they may not run."""
    dialect = dialect_of(source.kind)
    tree = check_read_only(sql, dialect)
    tables = tables_of(tree, dialect)
    executed = capped(tree, dialect, min(limit, MAX_ROWS) + 1)
    readable_datasets = await datasets_read(
        session,
        data_space_id=data_space_id,
        principal_id=principal_id,
        source=source,
        tables=tables,
    )
    policies = await active_policies(
        session,
        principal_id=principal_id,
        datasets=readable_datasets,
        caller_kind=caller_kind,
    )
    user = await session.scalar(select(org.AppUser).where(org.AppUser.id == principal_id))
    if user is None:
        raise SqlRejected("query principal is not an organisation user")
    output_policies = [policy for policy in policies if policy.kind in {"column", "mask"}]
    # Columns first: the row policies then filter the inner reads of the table,
    # where a hidden column is still there to filter on.
    executed = rewrite_column_policies(
        executed,
        dialect=dialect,
        policies=output_policies,
        datasets=readable_datasets,
        source=source,
        columns=await covered_columns(session, output_policies),
    )
    executed = rewrite_access_policies(
        executed,
        dialect=dialect,
        policies=[policy for policy in policies if policy.kind == "row"],
        user=user,
        datasets=readable_datasets,
        source=source,
    )
    # An agent account reads within its own scope and its owner's (agent_owner): both rewrites
    # apply, so the rows are their intersection, and an owner without ask refuses the query.
    owner = await agent_owner(session, principal_id)
    for reader in (principal_id, *([owner] if owner else [])):
        executed = rewrite_row_scopes(
            executed,
            dialect=dialect,
            scopes=await active_row_scopes(
                session,
                data_space_id=data_space_id,
                principal_id=reader,
            ),
        )
    return Bound(executed, list(policies))


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
    private: bool = False,
    run_id: uuid.UUID | None = None,
    limit: int = MAX_ROWS,
) -> QueryOutcome:
    """`private=True` is the channel of UC-16-02: the run is recorded (who, which source, status,
    counts, result hash) but neither its SQL, whose literals come from the question, nor its rows,
    nor an error message that may quote either. BR-193 / AC-16-02-10: none of the question's text
    is left in a table.

    `run_id` names a run the api already recorded (POST /v1/query-runs, the explorer): that row is
    completed instead of a new one written. `limit` (at most BR-03's 10 000) caps the rows."""
    limit = min(limit, MAX_ROWS)
    existing = run_id is not None
    run_id = run_id or uuid7()
    started = time.monotonic()
    outcome = QueryOutcome(query_run_id=run_id, status="failed", sql=sql)
    executed = sql
    output_policies: list[governance.AccessPolicy] = []
    applied_policy_ids: list[uuid.UUID] = []
    applied_policies: list[governance.AccessPolicy] = []
    async with scoped_session(ROLE, org_id, principal_id) as session:
        try:
            bound = await bound_sql(
                session,
                sql=sql,
                source=source,
                data_space_id=data_space_id,
                principal_id=principal_id,
                caller_kind=caller_kind,
                limit=limit,
            )
            executed = bound.executed
            applied_policies = bound.policies
            applied_policy_ids = [policy.id for policy in bound.policies]
            output_policies = [p for p in bound.policies if p.kind in {"column", "mask"}]
        except SqlRejected as exc:
            outcome.error = str(exc)
        hit: tuple[q.QueryRun, q.QueryResult] | None = None
        # The internal workspace is read through the asker's own session, and one source id stands
        # for it in every Data Space: never reused.
        if (
            outcome.error is None
            and not private
            and caller_kind in CACHEABLE_CALLERS
            and source.secret_ref != INTERNAL_WORKSPACE
        ):
            hit = await _cached(
                session,
                data_space_id=data_space_id,
                source_id=source.id,
                executed=executed,
                policies=applied_policies,
                as_of=as_of,
            )
        recorded = {
            "source_id": source.id,
            "sql_generated": "" if private else sql,
            "sql_executed": "" if private else executed,
            "status": "failed" if outcome.error else "running",
            "error": _stored_error(outcome, private),
            "policies_applied": applied_policy_ids,
        }
        if existing:
            if metric_version_ids:
                recorded["metric_version_ids"] = list(metric_version_ids)
            await session.execute(
                update(q.QueryRun).where(q.QueryRun.id == run_id).values(**recorded)
            )
        else:
            session.add(
                q.QueryRun(
                    id=run_id,
                    org_id=org_id,
                    data_space_id=data_space_id,
                    caller_kind=caller_kind,
                    caller_id=caller_id,
                    principal_id=principal_id,
                    route="live",
                    metric_version_ids=list(metric_version_ids),
                    as_of=as_of,
                    **recorded,
                )
            )
    if outcome.error:
        return outcome

    timeout_s = int(source.query_timeout_s or 120)
    cache_ref: str | None = None
    if hit is not None:
        # The rows were read with the same SQL, policies and as-of moments ago (module doc).
        earlier, stored = hit
        cache_ref = f"{CACHE_PREFIX}{earlier.id}"
        outcome.columns = list(stored.columns or [])
        outcome.rows = [list(row) for row in stored.rows or []]
        outcome.truncated = False
        outcome.result_hash = earlier.result_hash or _hash(outcome.columns, outcome.rows)
        outcome.status = "succeeded"
    else:
        try:
            async with _slot(source):
                if source.secret_ref == INTERNAL_WORKSPACE:
                    async with scoped_session(ROLE, org_id, principal_id) as local_session:
                        await local_session.execute(text("SET TRANSACTION READ ONLY"))
                        # Upload tables are materialized in the private workspace schema. The SQL
                        # editor deliberately uses unqualified table names, so make resolution agree
                        # with the scope checker without exposing any other schema to the query.
                        await local_session.execute(
                            text("SET LOCAL search_path TO workspace, pg_catalog")
                        )
                        result = await local_session.execute(text(executed))
                        columns = [
                            {"name": str(item[0]), "type": _pg_type(item[1])}
                            for item in cast(CursorResult[Any], result).cursor.description or ()
                        ]
                        rows = [[_plain(value) for value in row] for row in result.fetchall()]
                else:
                    credentials = await secrets.read(org_id, source.secret_ref)
                    columns, rows = await asyncio.wait_for(
                        driver_for(source.kind)(
                            source, credentials.user, credentials.password, executed, timeout_s
                        ),
                        timeout=timeout_s + 5,
                    )
            outcome.truncated = len(rows) > limit
            # Masks were applied in the SQL (rewrite_column_policies); a hidden column is not
            # read at all, and is dropped here once more should a source return it anyway.
            columns, rows = apply_output_policies(
                columns, rows, [p for p in output_policies if p.kind == "column"]
            )
            outcome.rows = rows[:limit]
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
        except SQLAlchemyError as exc:
            outcome.error = f"local query failed ({type(exc).__name__})"
        except (asyncpg.PostgresError, OSError) as exc:
            # The source's own message, without the SQL text or values it may quote.
            outcome.error = f"{type(exc).__name__}: {getattr(exc, 'sqlstate', '') or 'connection'}"

    async with scoped_session(ROLE, org_id, principal_id) as session:
        await session.execute(
            update(q.QueryRun)
            .where(q.QueryRun.id == run_id)
            .values(
                status=outcome.status,
                error=_stored_error(outcome, private),
                row_count=len(outcome.rows) if outcome.ok else None,
                result_hash=outcome.result_hash,
                result_ref=cache_ref,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        )
        if outcome.ok and not private:
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


async def record_period_snapshot(
    *,
    org_id: uuid.UUID,
    data_space_id: uuid.UUID,
    principal_id: uuid.UUID,
    caller_id: uuid.UUID,
    period_close_id: uuid.UUID,
    metric_version_ids: Sequence[uuid.UUID],
    columns: list[dict[str, str]],
    rows: list[list[Any]],
) -> QueryOutcome:
    """Record a read-only answer sourced from ``period.published_result``.

    A filed period is not a live query.  It still gets a normal query-run/result pair so the
    answer, receipt and API lineage have one stable reference, but no source connection is
    touched and the route is explicit in the query ledger.
    """
    run_id = uuid7()
    result_hash = _hash(columns, rows)
    async with scoped_session(ROLE, org_id, principal_id) as session:
        session.add(
            q.QueryRun(
                id=run_id,
                org_id=org_id,
                data_space_id=data_space_id,
                caller_kind="question",
                caller_id=caller_id,
                principal_id=principal_id,
                source_id=None,
                route="period_snapshot",
                period_close_id=period_close_id,
                sql_generated="",
                sql_executed="",
                metric_version_ids=list(metric_version_ids),
                status="succeeded",
                row_count=len(rows),
                result_hash=result_hash,
                duration_ms=0,
            )
        )
        session.add(
            q.QueryResult(
                id=uuid7(),
                org_id=org_id,
                query_run_id=run_id,
                columns=columns,
                rows=rows[:STORED_ROWS],
                truncated=len(rows) > STORED_ROWS,
            )
        )
    return QueryOutcome(
        query_run_id=run_id,
        status="succeeded",
        columns=columns,
        rows=rows,
        truncated=len(rows) > STORED_ROWS,
        result_hash=result_hash,
    )
