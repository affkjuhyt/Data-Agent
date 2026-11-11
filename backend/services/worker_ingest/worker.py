"""Source-table ingestion: a table of a customer's database copied into the workspace.

PostgreSQL through asyncpg; MySQL, SQL Server and ClickHouse through libs/source_drivers, the
drivers worker-agent's executor uses (GEN-12). Another engine is refused and the run recorded as
failed; a job is never marked successful without materializing and cataloguing rows.
"""

from __future__ import annotations

import datetime
import decimal
import json
import os
import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import asyncpg
from sqlalchemy import func, select, text, update

from libs import source_drivers, tabular
from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog, ingest
from services.worker_ingest.events import Event, Handler
from services.worker_ingest.secrets import Reader, SecretUnavailable

CREATED = "ingestion_job.created"
BACKFILL = "job_run.backfill_requested"
SAMPLE = "ingestion_job.sample_requested"
REPLAY = "dlq_record.replay_requested"
ROLE = "svc_ingest"
CATALOG_ROLE = "svc_catalog"
SAMPLE_ROWS = 1_000
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Source types the workspace keeps (da_materialize_upload takes text, numeric, boolean, date);
# anything else lands as text. Timestamps too: the function has no timestamp column yet.
_WORKSPACE_TYPES = {
    **dict.fromkeys(("int2", "int4", "int8", "numeric", "float4", "float8", "money"), "numeric"),
    "bool": "boolean",
    "date": "date",
}
# How the catalog names a workspace column type (catalog.column_meta.normalized_type).
_NORMALIZED = {"text": "string", "numeric": "decimal", "boolean": "boolean", "date": "date"}


# The PostgreSQL types a cursor may have (RES-18): numbers, dates and timestamps.
_CURSOR_TYPES = frozenset(
    {"int2", "int4", "int8", "numeric", "float4", "float8", "date", "timestamp", "timestamptz"}
)


class SchemaDrift(ValueError):
    """The source retyped a column the workspace table already holds: appending would mix kinds."""


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("source_object must be a table identifier")
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return None
    if hasattr(value, "as_tuple"):  # decimal.Decimal without importing a driver-specific type
        return format(value, "f")
    return value


# The table's name in each engine's quoting; both parts are plain identifiers (_IDENTIFIER).
_QUOTED = {
    "mysql": "`{schema}`.`{table}`",
    "clickhouse": "`{schema}`.`{table}`",
    "sqlserver": "[{schema}].[{table}]",
}
# The schema a bare table name is in, by engine.
_DEFAULT_SCHEMA = {"postgresql": "public", "sqlserver": "dbo"}


def _workspace_type(values: list[Any]) -> str:
    """The workspace type of a column read through a DB-API driver, from its values: the drivers
    name types by their own codes, the values say the same in Python's terms."""
    present = [v for v in values if v is not None]
    if not present:
        return "text"
    if all(isinstance(v, bool) for v in present):
        return "boolean"
    if all(
        isinstance(v, int | float | decimal.Decimal) and not isinstance(v, bool) for v in present
    ):
        return "numeric"
    if all(isinstance(v, datetime.date) and not isinstance(v, datetime.datetime) for v in present):
        return "date"
    return "text"


# Rows read and written per batch, and at most per run (RES-16): a table larger than memory is
# copied a batch at a time, and one larger than the ceiling fails rather than fill the workspace.
BATCH_ROWS = int(os.environ.get("INGEST_BATCH_ROWS", "5000"))
MAX_ROWS = int(os.environ.get("INGEST_MAX_ROWS", "2000000"))

Columns = list[dict[str, str]]


def _fits(kind: str, value: Any) -> bool:
    """Whether a value read later can go in a column typed from the first batch."""
    if value is None or kind == "text":
        return True
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "numeric":
        return isinstance(value, int | float | decimal.Decimal) and not isinstance(value, bool)
    return isinstance(value, datetime.date) and not isinstance(value, datetime.datetime)


@dataclass(frozen=True)
class Window:
    """An incremental run's slice of the source (RES-17): the cursor column, above the value the
    last run reached (none on the first run), up to the maximum sampled when this run started.
    Backfill sets inclusive_lower so [from, to] keeps the from day."""

    column: str
    after: str | None
    upto: str
    inclusive_lower: bool = False


_NUMBER = re.compile(r"-?\d+(\.\d+)?")
_MOMENT = re.compile(
    r"\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?)?(Z|[+-]\d{2}(:?\d{2})?)?"
)


def _literal(value: str) -> str:
    """A cursor value as a SQL literal for the DB-API engines, which take the statement as text:
    only a number or an ISO date or timestamp, never any other text. The value is the source's
    own maximum, so a cursor over free text (whose quoting each engine escapes its own way, MySQL
    with backslashes too) is refused rather than quoted."""
    if _NUMBER.fullmatch(value):
        return value
    if _MOMENT.fullmatch(value):
        return "'" + value + "'"
    raise ValueError(
        "an incremental cursor on this engine must be a number, a date or a timestamp column"
    )


def _driver_where(kind: str, window: Window | None) -> str:
    if window is None:
        return ""
    column = _QUOTED[kind].split(".")[0].format(schema=_identifier(window.column))
    upto = f"{column} <= {_literal(window.upto)}"
    if window.after is None:
        return f" WHERE {upto}"
    lower = ">=" if window.inclusive_lower else ">"
    return f" WHERE {column} {lower} {_literal(window.after)} AND {upto}"


async def _driver_batches(
    source: catalog.Source,
    schema: str,
    table: str,
    org_id: uuid.UUID,
    window: Window | None = None,
) -> AsyncIterator[tuple[Columns, list[list[Any]]]]:
    if source.kind not in _QUOTED:
        raise ValueError(f"source connector {source.kind} is not wired for ingestion")
    credentials = await Reader().read(org_id, source.secret_ref)
    columns: Columns | None = None
    async for names, rows in source_drivers.stream(
        source,
        credentials.user,
        credentials.password,
        "SELECT * FROM "
        + _QUOTED[source.kind].format(schema=schema, table=table)
        + _driver_where(source.kind, window),
        min(int(source.query_timeout_s or 120), 300),
        BATCH_ROWS,
    ):
        if columns is None:
            # The drivers name types by their own codes; the first batch's values say the same
            # in Python's terms, and every later batch must agree.
            labels = [c["name"] for c in names]
            columns = [
                {"name": name, "label": label, "type": _workspace_type([row[i] for row in rows])}
                for i, (name, label) in enumerate(
                    zip(tabular.physical_names(labels), labels, strict=True)
                )
            ]
        else:
            for row in rows:
                for column, value in zip(columns, row, strict=True):
                    if not _fits(column["type"], value):
                        raise ValueError(
                            f"column {column['label']} changed type after the first"
                            f" {BATCH_ROWS} rows ({column['type']} expected)"
                        )
        yield columns, [[_plain(value) for value in row] for row in rows]


def _table_of(source: catalog.Source, object_name: str) -> tuple[str, str]:
    parts = object_name.split(".")
    options = source.conn_options or {}
    if len(parts) == 1:
        schema = str(
            options.get("schema")
            or _DEFAULT_SCHEMA.get(source.kind)
            or source.database_name
            or "public"
        )
        table = parts[0]
    elif len(parts) == 2:
        schema, table = parts
    else:
        raise ValueError("source_object must be table or schema.table")
    return _identifier(schema), _identifier(table)


async def _pg_connect(source: catalog.Source, org_id: uuid.UUID) -> asyncpg.Connection:
    options = source.conn_options or {}
    credentials = await Reader().read(org_id, source.secret_ref)
    return await asyncpg.connect(
        host=source.host,
        port=source.port or 5432,
        database=source.database_name,
        user=credentials.user,
        password=credentials.password,
        ssl=str(options.get("sslmode") or "verify-full"),
        timeout=min(source.query_timeout_s, 30),
        statement_cache_size=0,
    )


async def _max_cursor(
    source: catalog.Source, object_name: str, column: str, org_id: uuid.UUID
) -> tuple[str | None, str]:
    """The cursor column's maximum now, as text, and its type: the upper bound of this run, fixed
    before reading so rows written meanwhile wait for the next run instead of being half read."""
    schema, table = _table_of(source, object_name)
    column = _identifier(column)
    if source.kind == "postgresql":
        conn = await _pg_connect(source, org_id)
        try:
            # RES-18: the cursor must be a column of the table and of a type that orders the way
            # time or a sequence does, checked before anything is read.
            kind = await conn.fetchval(
                "SELECT t.typname FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid"
                " WHERE a.attrelid = to_regclass($1) AND a.attname = $2"
                " AND a.attnum > 0 AND NOT a.attisdropped",
                f'"{schema}"."{table}"',
                column,
            )
            if kind is None:
                raise ValueError(f"cursor column {column} is not a column of {schema}.{table}")
            if kind not in _CURSOR_TYPES:
                raise ValueError(
                    f"cursor column {column} is {kind}; a cursor must be a number, a date or a"
                    " timestamp"
                )
            row = await conn.fetchrow(
                f'SELECT max("{column}")::text AS upto, pg_typeof(max("{column}"))::text AS kind'
                f' FROM "{schema}"."{table}"'
            )
        finally:
            await conn.close()
        return (row["upto"], row["kind"]) if row else (None, "text")
    if source.kind not in _QUOTED:
        raise ValueError(f"source connector {source.kind} is not wired for ingestion")
    credentials = await Reader().read(org_id, source.secret_ref)
    quoted = _QUOTED[source.kind].split(".")[0].format(schema=column)
    _, rows = await source_drivers.read(
        source,
        credentials.user,
        credentials.password,
        f"SELECT MAX({quoted}) FROM " + _QUOTED[source.kind].format(schema=schema, table=table),
        min(int(source.query_timeout_s or 120), 300),
    )
    value = rows[0][0] if rows and rows[0] else None
    if value is None:
        return None, "text"
    upto = str(_plain(value))
    # The bound goes into the driver's SQL as a literal: a cursor of text or of anything else that
    # is not a number or a moment is refused here, as a configuration error, before a row is read.
    _literal(upto)
    return upto, "text"


async def _source_batches(
    source: catalog.Source,
    object_name: str,
    org_id: uuid.UUID,
    window: Window | None = None,
    cursor_type: str = "text",
) -> AsyncIterator[tuple[Columns, list[list[Any]]]]:
    """The source table a batch at a time, with its columns (the same in every batch); an empty
    table is one empty batch. With a window, only the rows of that slice of the cursor."""
    schema, table = _table_of(source, object_name)
    if source.kind != "postgresql":
        async for item in _driver_batches(source, schema, table, org_id, window):
            yield item
        return
    conn = await _pg_connect(source, org_id)
    where, params = "", []
    if window is not None:
        # The bounds go as text parameters cast to the column's own type (read with the maximum):
        # asyncpg wants a Python value of a typed parameter, and the cursor is kept as text.
        if not re.fullmatch(r"[a-z0-9 _\[\]]+", cursor_type):
            raise ValueError(f"cursor column type {cursor_type} is not supported")
        column = _identifier(window.column)
        if window.after is None:
            where, params = f' WHERE "{column}" <= $1::text::{cursor_type}', [window.upto]
        else:
            lower = ">=" if window.inclusive_lower else ">"
            where = (
                f' WHERE "{column}" {lower} $1::text::{cursor_type}'
                f' AND "{column}" <= $2::text::{cursor_type}'
            )
            params = [window.after, window.upto]
    try:
        # A server-side cursor in a read-only transaction: the worker holds one batch at a time.
        async with conn.transaction(readonly=True):
            statement = await conn.prepare(f'SELECT * FROM "{schema}"."{table}"{where}')
            attributes = statement.get_attributes()
            # The source's own names ("CustomerID", "Ngày hạch toán") are labels; the table takes
            # physical ones (GEN-13).
            columns = [
                {
                    "name": name,
                    "label": attr.name,
                    "type": _WORKSPACE_TYPES.get(attr.type.name, "text"),
                }
                for name, attr in zip(
                    tabular.physical_names([a.name for a in attributes]), attributes, strict=True
                )
            ]
            cursor = await statement.cursor(*params)
            sent = False
            while True:
                records = await cursor.fetch(BATCH_ROWS)
                if not records:
                    break
                sent = True
                yield columns, [[_plain(value) for value in record] for record in records]
            if not sent:
                yield columns, []
    finally:
        await conn.close()


async def _no_batches() -> AsyncIterator[tuple[Columns, list[list[Any]]]]:
    """An incremental run with nothing above the cursor reads nothing."""
    return
    yield


def _table_name(job: ingest.IngestionJob) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", job.name.lower()).strip("_")[:45] or "source"
    return f"ingest_{stem}_{job.id.hex[:8]}"


async def _workspace_columns(session: Any, table_name: str) -> dict[str, str]:
    """The workspace table's columns and their types; empty when the table is not there yet."""
    rows = await session.execute(
        text(
            # By name through the catalogue: svc_ingest has no USAGE on the workspace schema,
            # which to_regclass would need (the table is written by a SECURITY DEFINER function).
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a"
            " JOIN pg_class c ON c.oid = a.attrelid"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'workspace' AND c.relname = :t"
            " AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum"
        ),
        {"t": table_name},
    )
    return {str(name): str(kind) for name, kind in rows}


def check_drift(existing: dict[str, str], columns: Columns) -> None:
    """RES-18, after Airbyte's schema evolution: a column the source gained is added, one it dropped
    stays (NULL from now on), and one it retyped stops the job: rows of two kinds in one column
    would be wrong silently. The person rebuilds the table with a full run or fixes the source."""
    retyped = [
        f"{column['name']} {existing[column['name']]} → {column['type']}"
        for column in columns
        if column["name"] in existing and existing[column["name"]] != column["type"]
    ]
    if retyped:
        raise SchemaDrift("source columns changed type: " + ", ".join(retyped))


async def _materialize(
    session: Any,
    table_name: str,
    columns: list[dict[str, str]],
    rows: list[list[Any]],
    *,
    replace: bool,
    merge_keys: list[str] | None = None,
) -> None:
    typed = [{"name": column["name"], "type": column["type"]} for column in columns]
    if merge_keys and not replace:
        await session.execute(
            text(
                "SELECT public.da_materialize_upsert(:table_name, CAST(:columns AS jsonb),"
                " CAST(:rows AS jsonb), CAST(:keys AS text[]))"
            ),
            {
                "table_name": table_name,
                "columns": json.dumps(typed),
                "rows": json.dumps(rows),
                "keys": merge_keys,
            },
        )
        return
    await session.execute(
        text(
            "SELECT public.da_materialize_upload(:table_name, CAST(:columns AS jsonb),"
            " CAST(:rows AS jsonb), :replace)"
        ),
        {
            "table_name": table_name,
            "columns": json.dumps(typed),
            "rows": json.dumps(rows),
            "replace": replace,
        },
    )


def _cap_skip_today(upto: str, skip_today: bool) -> str:
    """RES-17: when skip_today, do not read the current UTC calendar day for date/timestamp cursors."""
    if not skip_today:
        return upto
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    if not (upto.startswith(today) or (len(upto) >= 10 and upto[:10] >= today)):
        return upto
    yesterday = (
        datetime.datetime.now(datetime.UTC).date() - datetime.timedelta(days=1)
    ).isoformat()
    return yesterday if len(upto) <= 10 else yesterday + upto[10:]


# What the person must fix (credentials, rights, a table or column that is not there) against what
# may pass on its own (a timeout, a dropped connection, a busy source), after Airbyte's failure
# types (RES-19); everything else is the platform's own fault.
_CONFIG_PG = (
    asyncpg.InvalidPasswordError,
    asyncpg.InvalidAuthorizationSpecificationError,
    asyncpg.InsufficientPrivilegeError,
    asyncpg.UndefinedTableError,
    asyncpg.UndefinedColumnError,
    asyncpg.InvalidCatalogNameError,
    asyncpg.InvalidSchemaNameError,
)
_TRANSIENT_PG = (
    asyncpg.QueryCanceledError,
    asyncpg.ConnectionDoesNotExistError,
    asyncpg.CannotConnectNowError,
    asyncpg.TooManyConnectionsError,
    asyncpg.AdminShutdownError,
)


def failure_kind(exc: BaseException) -> str:
    """config, transient or system: whether a person must act, a later run may pass, or it is ours."""
    if isinstance(exc, (SecretUnavailable, ValueError, *_CONFIG_PG)):
        return "config"
    if isinstance(exc, (*_TRANSIENT_PG, TimeoutError, ConnectionError, OSError)):
        return "transient"
    if isinstance(exc, source_drivers.DriverError):
        text = str(exc)
        return (
            "config" if "TLS" in text or "sslmode" in text or "certificate" in text else "transient"
        )
    return "system"


async def _fail(
    event: Event, run_id: uuid.UUID, message: str, kind: str, *, drift: bool = False
) -> None:
    async with scoped_session(ROLE, event.org_id, event.payload.get("principal_id")) as session:
        await session.execute(
            update(ingest.JobRun)
            .where(ingest.JobRun.id == run_id)
            .values(
                status="schema_drift" if drift else "failed",
                error=message,
                failure_kind=kind,
                finished_at=datetime.datetime.now(datetime.UTC),
            )
        )
        await session.execute(
            update(ingest.IngestionJob)
            .where(ingest.IngestionJob.id == event.payload.get("job_id", event.aggregate_id))
            .values(
                consecutive_failures=ingest.IngestionJob.consecutive_failures + 1,
                # A drifted job leaves the schedule (it runs only when active) until a person acts.
                **({"status": "schema_drift"} if drift else {}),
            )
        )


async def handle(event: Event) -> None:
    principal_id = event.payload.get("principal_id")
    if principal_id is None:
        raise ValueError("ingestion event has no principal_id")
    job_id = uuid.UUID(str(event.payload.get("job_id", event.aggregate_id)))
    run_id = (
        uuid.UUID(str(event.payload["job_run_id"]))
        if event.event_type == BACKFILL
        else uuid.uuid4()
    )
    is_backfill = event.event_type == BACKFILL
    rebuild_full = bool(event.payload.get("rebuild_full"))
    try:
        if event.event_type == CREATED:
            async with scoped_session(ROLE, event.org_id, principal_id) as session:
                session.add(ingest.JobRun(id=run_id, org_id=event.org_id, job_id=job_id))
        async with scoped_session(ROLE, event.org_id, principal_id) as session:
            job = await session.scalar(
                select(ingest.IngestionJob).where(ingest.IngestionJob.id == job_id)
            )
            run_row = await session.scalar(select(ingest.JobRun).where(ingest.JobRun.id == run_id))
            source = (
                await session.scalar(
                    select(catalog.Source).where(catalog.Source.id == job.source_id)
                )
                if job
                else None
            )
            if job is None or source is None:
                raise ValueError("ingestion job or source not found")
            if job.status == "paused" and not rebuild_full:
                raise ValueError("ingestion job is paused")
            if job.mode not in ("full", "incremental", "cdc"):
                raise ValueError(f"ingestion mode {job.mode} is not wired")
            # Poll-CDC (mode=cdc) shares the incremental cursor window; Temporal fires it densely.
            cursor_mode = job.mode in ("incremental", "cdc") and not rebuild_full
            mode_full = job.mode == "full" or rebuild_full
            dataset_id = job.target_dataset_id or uuid.uuid4()
            table_name = _table_name(job)
            window: Window | None = None
            cursor_type = "text"
            advance_cursor = False
            if is_backfill and run_row and (run_row.cursor_from or run_row.cursor_to):
                if not job.cursor_column:
                    raise ValueError("a dated backfill needs cursor_column on the job")
                _, cursor_type = await _max_cursor(
                    source, job.source_object, job.cursor_column, event.org_id
                )
                window = Window(
                    job.cursor_column,
                    run_row.cursor_from,
                    run_row.cursor_to or run_row.cursor_from or "",
                    inclusive_lower=True,
                )
                if not window.upto:
                    raise ValueError("backfill needs cursor_to")
            elif cursor_mode:
                if not job.cursor_column:
                    raise ValueError(f"a {job.mode} job needs cursor_column")
                upto, cursor_type = await _max_cursor(
                    source, job.source_object, job.cursor_column, event.org_id
                )
                if upto is not None:
                    upto = _cap_skip_today(upto, bool(getattr(job, "skip_today", False)))
                if upto is not None and upto != job.cursor_value:
                    window = Window(job.cursor_column, job.cursor_value, upto)
                    advance_cursor = True
            # Full: the first batch replaces the table, the others append to it. Incremental/CDC:
            # every batch appends (or upserts when merge_key_columns is set).
            columns: Columns = []
            read = 0
            nothing_new = cursor_mode and window is None and not mode_full
            batches = (
                _source_batches(source, job.source_object, event.org_id, window, cursor_type)
                if not nothing_new
                else _no_batches()
            )
            existing: dict[str, str] | None = None
            merge_keys = list(job.merge_key_columns or []) or None
            on_schema = getattr(job, "on_schema_change", "accept_new_columns") or "accept_new_columns"
            async for columns, batch in batches:
                if existing is None and cursor_mode:
                    existing = await _workspace_columns(session, table_name)
                    # Retype always stops as schema_drift; additive columns follow on_schema_change.
                    check_drift(existing, columns)
                    if on_schema == "pause_and_ask" and existing:
                        gained = [c["name"] for c in columns if c["name"] not in existing]
                        if gained:
                            await session.execute(
                                update(ingest.IngestionJob)
                                .where(ingest.IngestionJob.id == job.id)
                                .values(
                                    status="paused",
                                    paused_reason="schema change: new columns",
                                )
                            )
                            raise ValueError("paused for schema change: " + ", ".join(gained))
                read += len(batch)
                if read > MAX_ROWS:
                    raise ValueError(
                        f"the source table has more than {MAX_ROWS} rows; ingest it incrementally"
                    )
                if batch or mode_full:
                    good, dlq_n = await _filter_batch(
                        session,
                        event.org_id,
                        job,
                        run_id,
                        columns,
                        batch,
                        getattr(job, "on_invalid_record", "quarantine_30d") or "quarantine_30d",
                    )
                    if dlq_n:
                        await session.execute(
                            update(ingest.JobRun)
                            .where(ingest.JobRun.id == run_id)
                            .values(rows_dlq=ingest.JobRun.rows_dlq + dlq_n)
                        )
                    if good or mode_full:
                        await _materialize(
                            session,
                            table_name,
                            columns,
                            good,
                            replace=mode_full and read == len(batch),
                            merge_keys=None if mode_full else merge_keys,
                        )
            if advance_cursor and window is not None:
                await session.execute(
                    update(ingest.IngestionJob)
                    .where(ingest.IngestionJob.id == job.id)
                    .values(cursor_value=window.upto)
                )
                await session.execute(
                    update(ingest.JobRun)
                    .where(ingest.JobRun.id == run_id)
                    .values(cursor_from=window.after, cursor_to=window.upto)
                )
            await session.execute(
                update(ingest.JobRun)
                .where(ingest.JobRun.id == run_id)
                .values(
                    status="succeeded",
                    rows_read=read,
                    rows_written=read,
                    finished_at=datetime.datetime.now(datetime.UTC),
                )
            )
            await session.execute(
                update(ingest.IngestionJob)
                .where(ingest.IngestionJob.id == job.id)
                .values(
                    target_dataset_id=dataset_id if columns else job.target_dataset_id,
                    consecutive_failures=0,
                )
            )
        if not columns:
            # An incremental run with nothing new before any table was made: nothing to catalogue.
            return
        async with scoped_session(CATALOG_ROLE, event.org_id, principal_id) as session:
            dataset = await session.get(catalog.Dataset, dataset_id)
            if dataset is None:
                session.add(
                    catalog.Dataset(
                        id=dataset_id,
                        org_id=event.org_id,
                        # AUD-07: provenance — origin=source iff source_id is set (CHECK).
                        source_id=job.source_id,
                        data_space_id=job.data_space_id,
                        origin="source",
                        schema_name="workspace",
                        table_name=table_name,
                        display_name=job.name,
                        row_estimate=read,
                        enabled=True,
                        scope="data_space",
                        quality_state="unknown",
                        created_by=principal_id,
                        updated_by=principal_id,
                    )
                )
                version_no = 1
                session.add(
                    catalog.DatasetVersion(
                        id=uuid.uuid4(),
                        org_id=event.org_id,
                        dataset_id=dataset_id,
                        version=version_no,
                        schema_hash=_schema_hash(columns),
                        diff={"added": [c["name"] for c in columns]},
                        change_kind="initial",
                        valid_from=datetime.datetime.now(datetime.UTC),
                        created_by=principal_id,
                    )
                )
            else:
                if dataset.source_id is None:
                    dataset.source_id = job.source_id
                    dataset.origin = "source"
                dataset.row_estimate = read
                dataset.updated_by = principal_id
                version_no = (
                    await session.scalar(
                        select(func.max(catalog.DatasetVersion.version)).where(
                            catalog.DatasetVersion.dataset_id == dataset_id
                        )
                    )
                    or 1
                )
            known = set(
                (
                    await session.scalars(
                        select(catalog.ColumnMeta.name).where(
                            catalog.ColumnMeta.dataset_id == dataset_id
                        )
                    )
                ).all()
            )
            added = [column for column in columns if column["name"] not in known]
            if added:
                if known:
                    version_no = int(version_no) + 1
                    session.add(
                        catalog.DatasetVersion(
                            id=uuid.uuid4(),
                            org_id=event.org_id,
                            dataset_id=dataset_id,
                            version=version_no,
                            schema_hash=_schema_hash(columns),
                            diff={"added": [c["name"] for c in added]},
                            change_kind="additive",
                            valid_from=datetime.datetime.now(datetime.UTC),
                            created_by=principal_id,
                        )
                    )
                await session.flush()
                session.add_all(
                    catalog.ColumnMeta(
                        id=uuid.uuid4(),
                        org_id=event.org_id,
                        dataset_id=dataset_id,
                        dataset_version=version_no,
                        name=column["name"],
                        ordinal=len(known) + ordinal,
                        data_type=column["type"],
                        normalized_type=_NORMALIZED[column["type"]],
                        display_name=column["label"] if column["label"] != column["name"] else None,
                        created_by=principal_id,
                        updated_by=principal_id,
                    )
                    for ordinal, column in enumerate(added)
                )
    except (
        SecretUnavailable,
        ValueError,
        asyncpg.PostgresError,
        source_drivers.DriverError,
        # A source that cannot be reached at all raises these from the connect, not a driver
        # error: the run was left `running` with no error before.
        OSError,
        TimeoutError,
    ) as exc:
        await _fail(
            event,
            run_id,
            str(exc) or type(exc).__name__,
            failure_kind(exc),
            drift=isinstance(exc, SchemaDrift),
        )
        # RES-19: re-queue a transient failure when the job asks for retries.
        if failure_kind(exc) == "transient" and not isinstance(exc, SchemaDrift):
            await _maybe_retry(event, job_id, principal_id)


async def _filter_batch(
    session: Any,
    org_id: uuid.UUID,
    job: ingest.IngestionJob,
    run_id: uuid.UUID,
    columns: Columns,
    batch: list[list[Any]],
    policy: str,
) -> tuple[list[list[Any]], int]:
    """Drop or quarantine rows that do not fit column types; stop the run when policy is stop."""
    from services.worker_ingest import dlq as dlq_store

    good: list[list[Any]] = []
    quarantined = 0
    for row in batch:
        bad = None
        for column, value in zip(columns, row, strict=True):
            if not _fits(column["type"], value):
                bad = f"column {column['label']} value does not fit {column['type']}"
                break
        if bad is None:
            good.append(row)
            continue
        if policy == "stop":
            raise ValueError(bad)
        if policy == "skip_and_log":
            continue
        # quarantine_30d (default)
        payload = {c["name"]: v for c, v in zip(columns, row, strict=True)}
        ref = await dlq_store.store_payload(org_id, job.id, payload)
        session.add(
            ingest.DlqRecord(
                id=uuid.uuid4(),
                org_id=org_id,
                job_id=job.id,
                job_run_id=run_id,
                payload_ref=ref,
                error_kind="type_error",
                error=bad,
            )
        )
        quarantined += 1
    return good, quarantined


def _schema_hash(columns: Columns) -> str:
    payload = json.dumps(
        [(c["name"], c["type"]) for c in sorted(columns, key=lambda c: c["name"])],
        separators=(",", ":"),
    )
    return str(abs(hash(payload)))


async def _maybe_retry(event: Event, job_id: uuid.UUID, principal_id: Any) -> None:
    async with scoped_session(ROLE, event.org_id, principal_id) as session:
        job = await session.scalar(
            select(ingest.IngestionJob).where(ingest.IngestionJob.id == job_id)
        )
        if job is None:
            return
        policy = getattr(job, "retry_policy", "backoff_3") or "backoff_3"
        if policy == "none":
            return
        failures = int(job.consecutive_failures or 0)
        if policy == "once" and failures > 1:
            return
        if policy == "backoff_3" and failures > 3:
            await session.execute(
                update(ingest.IngestionJob)
                .where(ingest.IngestionJob.id == job.id)
                .values(status="failing")
            )
            return
        run = ingest.JobRun(id=uuid.uuid4(), org_id=event.org_id, job_id=job.id)
        session.add(run)
        await session.flush()
        session.add(
            ingest.Outbox(
                id=uuid.uuid4(),
                org_id=event.org_id,
                aggregate_type="job_run",
                aggregate_id=run.id,
                event_type=BACKFILL,
                payload={
                    "job_id": str(job.id),
                    "job_run_id": str(run.id),
                    "principal_id": str(principal_id),
                    "retry_of": str(event.event_id),
                },
            )
        )


async def handle_sample(event: Event) -> None:
    principal_id = event.payload.get("principal_id")
    sample_id = uuid.UUID(str(event.payload.get("sample_id", event.aggregate_id)))
    if principal_id is None:
        raise ValueError("sample event has no principal_id")
    async with scoped_session(ROLE, event.org_id, principal_id) as session:
        sample = await session.scalar(
            select(ingest.JobSample).where(ingest.JobSample.id == sample_id)
        )
        source = (
            await session.scalar(
                select(catalog.Source).where(catalog.Source.id == sample.source_id)
            )
            if sample
            else None
        )
        if sample is None or source is None:
            return
        try:
            rows_out: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            read = 0
            async for columns, batch in _source_batches(
                source, sample.source_object, event.org_id, None, "text"
            ):
                names = [c["name"] for c in columns]
                for row in batch:
                    read += 1
                    if read > SAMPLE_ROWS:
                        break
                    rows_out.append(dict(zip(names, row, strict=True)))
                if read >= SAMPLE_ROWS:
                    break
            sample.sample = rows_out
            sample.errors = errors
            sample.status = "succeeded"
            sample.note = f"Sampled {len(rows_out)} rows from the source (cap {SAMPLE_ROWS})."
            sample.finished_at = datetime.datetime.now(datetime.UTC)
        except Exception as exc:  # noqa: BLE001 — surface any sample failure to the poller
            sample.status = "failed"
            sample.error = str(exc) or type(exc).__name__
            sample.finished_at = datetime.datetime.now(datetime.UTC)


async def handle_replay(event: Event) -> None:
    principal_id = event.payload.get("principal_id")
    record_id = uuid.UUID(str(event.payload.get("dlq_record_id", event.aggregate_id)))
    if principal_id is None:
        raise ValueError("replay event has no principal_id")
    async with scoped_session(ROLE, event.org_id, principal_id) as session:
        record = await session.scalar(
            select(ingest.DlqRecord).where(ingest.DlqRecord.id == record_id)
        )
        job = (
            await session.scalar(
                select(ingest.IngestionJob).where(ingest.IngestionJob.id == record.job_id)
            )
            if record
            else None
        )
        if record is None or job is None or record.status != "pending":
            return
        try:
            # Payload is a JSON object list of values aligned to the workspace table columns.
            from services.worker_ingest.dlq import load_payload

            columns, row = await load_payload(record.payload_ref)
            await _materialize(
                session,
                _table_name(job),
                columns,
                [row],
                replace=False,
                merge_keys=list(job.merge_key_columns or []) or None,
            )
            record.status = "replayed"
            record.replay_count = int(record.replay_count or 0) + 1
        except Exception as exc:  # noqa: BLE001
            record.replay_count = int(record.replay_count or 0) + 1
            record.error = (record.error or "") + f"; replay failed: {exc}"
            if record.replay_count >= 3:
                record.status = "locked"


HANDLERS: dict[str, Handler] = {
    CREATED: handle,
    BACKFILL: handle,
    SAMPLE: handle_sample,
    REPLAY: handle_replay,
}
