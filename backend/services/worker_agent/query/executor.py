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
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import asyncpg
from sqlalchemy import select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlglot import exp, parse_one

from libs import source_drivers
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
    OutOfScope,
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


# DuckDB's type names, as a snapshot read reports them, named as the workspace path names them.
_DUCK_TYPES = {
    "BOOLEAN": "bool",
    "BIGINT": "int8",
    "SMALLINT": "int2",
    "INTEGER": "int4",
    "VARCHAR": "text",
    "FLOAT": "float4",
    "DOUBLE": "float8",
    "DATE": "date",
    "TIMESTAMP": "timestamp",
    "TIMESTAMP WITH TIME ZONE": "timestamptz",
    "HUGEINT": "numeric",
}


def _duck_type(name: str) -> str:
    return "numeric" if name.startswith("DECIMAL") else _DUCK_TYPES.get(name, "unknown")


class SnapshotUnreadable(RuntimeError):
    """A version's snapshot could not be fetched, was not the recorded file, or failed in DuckDB."""


@dataclass(frozen=True)
class VersionRead:
    """A workspace dataset read as it was at an earlier version (DF-06, a receipt replay): `table`
    is its name as the SQL names it, `physical` the version's own table while it stands, and the
    version's Parquet snapshot (`snapshot_key`, `snapshot_sha256`) once it is gone."""

    table: str
    physical: str | None
    snapshot_key: str | None = None
    snapshot_sha256: str | None = None


def at_versions(executed: str, versions: Mapping[str, VersionRead], *, files: bool) -> str:
    """The bound SQL reading each versioned dataset where its version is: its version's table, or,
    with `files`, the snapshot loaded under the dataset's own name in DuckDB (and DuckDB's
    dialect). The column, mask and row policies are already written into `executed`, as a
    subquery over the name: they apply to the version's rows unchanged. A query that also reads a
    workspace table with no snapshot cannot be read from files, and is refused."""
    tree = parse_one(executed, dialect="postgres")
    ctes = {cte.alias for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        db = table.args.get("db")
        if table.args.get("catalog") or (db is not None and db.name != "workspace"):
            continue
        if db is None and table.name in ctes:
            continue
        found = versions.get(table.name)
        if found is None:
            if files:
                raise SqlRejected(
                    f"{table.name} has no snapshot of the version the answer read: the others "
                    "cannot be read from theirs alone"
                )
            continue
        if files:
            table.set("db", None)
            table.set("this", exp.to_identifier(found.table, quoted=True))
        else:
            table.set("this", exp.to_identifier(found.physical, quoted=True))
            table.set("db", exp.to_identifier("workspace"))
    return tree.sql(dialect="duckdb" if files else "postgres")


async def _read_snapshots(
    sql: str, versions: Sequence[VersionRead], limit: int, timeout_s: int
) -> tuple[list[dict[str, str]], list[list[Any]]]:
    """Run `sql` (DuckDB's, policies written in) on the versions' snapshots (libs/snapshot).

    One deadline, `timeout_s` from now, bounds the downloads and the read; DuckDB gets only what
    is left and interrupts itself at it, so the thread ends by the deadline and the source's slot
    is held until it has (security review of 9d7cdcfa: an outer wait_for let the slot go while
    the read went on)."""
    import duckdb

    from libs.snapshot import reader
    from libs.snapshot.store import StoreUnavailable
    from services.worker_agent import snapshots

    deadline = time.monotonic() + timeout_s
    try:
        store = snapshots.store()
        with tempfile.TemporaryDirectory(prefix="replay-") as tmp:
            files: dict[str, reader.SnapshotFile] = {}
            for index, version in enumerate(versions):
                assert version.snapshot_key and version.snapshot_sha256
                path = Path(tmp) / f"{index}.parquet"
                await asyncio.wait_for(
                    store.get(version.snapshot_key, path),
                    timeout=max(deadline - time.monotonic(), 0.001),
                )
                files[version.table] = reader.SnapshotFile(path, version.snapshot_sha256)
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("the snapshots took the whole time limit to fetch")
            read = await asyncio.to_thread(
                reader.query, files, sql, max_rows=limit + 1, timeout_s=left
            )
    except reader.SnapshotMismatch as exc:
        raise SnapshotUnreadable(f"a snapshot is not the file recorded: {exc}") from exc
    except StoreUnavailable as exc:
        raise SnapshotUnreadable(str(exc)) from exc
    except duckdb.Error as exc:
        raise SnapshotUnreadable(f"the snapshot could not be read ({type(exc).__name__})") from exc
    columns = [
        {"name": name, "type": _duck_type(kind)}
        for name, kind in zip(read.columns, read.types, strict=True)
    ]
    return columns, [[_plain(value) for value in row] for row in read.rows]


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


# The drivers live in libs/source_drivers (GEN-12), shared with worker-ingest.
SourceDriverError = source_drivers.DriverError


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


async def _run_mysql(
    source: catalog.Source, user: str, password: str, sql: str, timeout_s: int
) -> tuple[list[dict[str, str]], list[list[Any]]]:
    columns, rows = await asyncio.to_thread(
        source_drivers.mysql, source, user, password, sql, timeout_s
    )
    return columns, [[_plain(value) for value in row] for row in rows]


async def _run_sqlserver(
    source: catalog.Source, user: str, password: str, sql: str, timeout_s: int
) -> tuple[list[dict[str, str]], list[list[Any]]]:
    columns, rows = await asyncio.to_thread(
        source_drivers.sqlserver, source, user, password, sql, timeout_s, "dataagent-worker-agent"
    )
    return columns, [[_plain(value) for value in row] for row in rows]


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
    datasets: list[uuid.UUID] = field(default_factory=list)  # what the SQL reads (DF-08's gate)


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
    # An agent account is held to its owner too (agent_owner): the tables both may read, every
    # column, mask and row policy of either, and the intersection of their row scopes. An owner
    # who may no longer ask, or read a table, refuses the query.
    owner = await agent_owner(session, principal_id)
    readers = [principal_id, *([owner] if owner else [])]
    readable_datasets: list[catalog.Dataset] = []
    held: list[tuple[org.AppUser, list[governance.AccessPolicy]]] = []
    for reader in readers:
        resolved = await datasets_read(
            session,
            data_space_id=data_space_id,
            principal_id=reader,
            source=source,
            tables=tables,
        )
        # The policies are keyed by dataset: if the agent and its owner resolve a name to
        # different datasets (a personal one of either), one's policies would miss the tables
        # the SQL names, so such a query is refused rather than half-bound.
        if held and {d.id for d in resolved} != {d.id for d in readable_datasets}:
            raise OutOfScope("the agent and its owner read different datasets under these names")
        readable_datasets = resolved
        user = await session.scalar(select(org.AppUser).where(org.AppUser.id == reader))
        if user is None:
            raise SqlRejected("query principal is not an organisation user")
        found = await active_policies(
            session,
            principal_id=reader,
            datasets=readable_datasets,
            caller_kind=caller_kind,
        )
        held.append((user, list(found)))
    policies = list({p.id: p for _, found in held for p in found}.values())
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
    # Each reader's row policies bind that reader's attributes (:user.department, …).
    for user, found in held:
        executed = rewrite_access_policies(
            executed,
            dialect=dialect,
            policies=[policy for policy in found if policy.kind == "row"],
            user=user,
            datasets=readable_datasets,
            source=source,
        )
    for reader in readers:
        executed = rewrite_row_scopes(
            executed,
            dialect=dialect,
            scopes=await active_row_scopes(
                session,
                data_space_id=data_space_id,
                principal_id=reader,
            ),
        )
    return Bound(executed, list(policies), [d.id for d in readable_datasets])


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
    versions: Sequence[VersionRead] = (),
    quality_gate: bool = False,
) -> QueryOutcome:
    """`private=True` is the channel of UC-16-02: the run is recorded (who, which source, status,
    counts, result hash) but neither its SQL, whose literals come from the question, nor its rows,
    nor an error message that may quote either. BR-193 / AC-16-02-10: none of the question's text
    is left in a table.

    `run_id` names a run the api already recorded (POST /v1/query-runs, the explorer): that row is
    completed instead of a new one written. `limit` (at most BR-03's 10 000) caps the rows.

    `versions` (DF-06, the internal workspace only) reads those datasets as they were at an
    earlier version: their version's tables while every one stands, else their Parquet snapshots
    in DuckDB. The swap comes after every check and policy rewrite (at_versions), so the asker's
    limits are the live path's; the run is recorded with route `history`.

    `quality_gate` (DF-08: tiles, scheduled reports) refuses to read a dataset its quality rules
    or contract hold back (worker_agent/quality_gate), before the source is asked; the run is
    recorded failed with why. A quality check, a replay, an eval or a pipeline does not ask it."""
    limit = min(limit, MAX_ROWS)
    existing = run_id is not None
    run_id = run_id or uuid7()
    started = time.monotonic()
    outcome = QueryOutcome(query_run_id=run_id, status="failed", sql=sql)
    executed = sql
    historic = bool(versions) and source.secret_ref == INTERNAL_WORKSPACE
    by_table = {v.table: v for v in versions} if historic else {}
    # Every version's table stands: read them in place; else every one needs its snapshot.
    from_files = historic and any(v.physical is None for v in versions)
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
            if quality_gate:
                from services.worker_agent import quality_gate as gate

                held = await gate.check(org_id, bound.datasets, principal_id)
                if held.blocked:
                    raise SqlRejected(held.message)
            if from_files and any(v.snapshot_key is None for v in versions):
                raise SqlRejected(
                    "a version the answer read has neither its table nor a snapshot any more"
                )
            if historic:
                executed = at_versions(executed, by_table, files=from_files)
        except SqlRejected as exc:
            outcome.error = str(exc)
        hit: tuple[q.QueryRun, q.QueryResult] | None = None
        # The internal workspace is read through the asker's own session, and one source id stands
        # for it in every Data Space: never reused.
        if (
            outcome.error is None
            and not private
            and not historic
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
                    route="history" if historic else "live",
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
                if from_files:
                    # The source's limit, as a live read has it (_read_snapshots).
                    columns, rows = await _read_snapshots(executed, versions, limit, timeout_s)
                elif source.secret_ref == INTERNAL_WORKSPACE:
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
        except SnapshotUnreadable as exc:
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
