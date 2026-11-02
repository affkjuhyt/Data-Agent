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
from sqlalchemy import select, text, update

from libs import source_drivers, tabular
from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog, ingest
from services.worker_ingest.events import Event, Handler
from services.worker_ingest.secrets import Reader, SecretUnavailable

CREATED = "ingestion_job.created"
BACKFILL = "job_run.backfill_requested"
ROLE = "svc_ingest"
CATALOG_ROLE = "svc_catalog"
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
    last run reached (none on the first run), up to the maximum sampled when this run started."""

    column: str
    after: str | None
    upto: str


def _literal(value: str) -> str:
    """A cursor value as a SQL literal for the DB-API engines: a number as it is, anything else
    quoted with its quotes doubled (the value is the source's own maximum, read back as text)."""
    if re.fullmatch(r"-?\d+(\.\d+)?", value):
        return value
    return "'" + value.replace("'", "''") + "'"


def _driver_where(kind: str, window: Window | None) -> str:
    if window is None:
        return ""
    column = _QUOTED[kind].split(".")[0].format(schema=_identifier(window.column))
    upto = f"{column} <= {_literal(window.upto)}"
    return (
        f" WHERE {column} > {_literal(window.after)} AND {upto}"
        if window.after
        else f" WHERE {upto}"
    )


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
    return (None if value is None else str(_plain(value)), "text")


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
            where = (
                f' WHERE "{column}" > $1::text::{cursor_type}'
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


async def _materialize(
    session: Any,
    table_name: str,
    columns: list[dict[str, str]],
    rows: list[list[Any]],
    *,
    replace: bool,
) -> None:
    typed = [{"name": column["name"], "type": column["type"]} for column in columns]
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


async def _fail(event: Event, run_id: uuid.UUID, message: str) -> None:
    async with scoped_session(ROLE, event.org_id, event.payload.get("principal_id")) as session:
        await session.execute(
            update(ingest.JobRun)
            .where(ingest.JobRun.id == run_id)
            .values(status="failed", error=message, finished_at=datetime.datetime.now(datetime.UTC))
        )
        await session.execute(
            update(ingest.IngestionJob)
            .where(ingest.IngestionJob.id == event.payload.get("job_id", event.aggregate_id))
            .values(consecutive_failures=ingest.IngestionJob.consecutive_failures + 1)
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
    try:
        if event.event_type == CREATED:
            async with scoped_session(ROLE, event.org_id, principal_id) as session:
                session.add(ingest.JobRun(id=run_id, org_id=event.org_id, job_id=job_id))
        async with scoped_session(ROLE, event.org_id, principal_id) as session:
            job = await session.scalar(
                select(ingest.IngestionJob).where(ingest.IngestionJob.id == job_id)
            )
            source = (
                await session.scalar(
                    select(catalog.Source).where(catalog.Source.id == job.source_id)
                )
                if job
                else None
            )
            if job is None or source is None:
                raise ValueError("ingestion job or source not found")
            if job.mode not in ("full", "incremental"):
                raise ValueError("CDC ingestion is not wired yet")
            dataset_id = job.target_dataset_id or uuid.uuid4()
            table_name = _table_name(job)
            window: Window | None = None
            cursor_type = "text"
            if job.mode == "incremental":
                if not job.cursor_column:
                    raise ValueError("an incremental job needs cursor_column")
                upto, cursor_type = await _max_cursor(
                    source, job.source_object, job.cursor_column, event.org_id
                )
                if upto is not None and upto != job.cursor_value:
                    window = Window(job.cursor_column, job.cursor_value, upto)
            # Full: the first batch replaces the table, the others append to it. Incremental: every
            # batch appends (the table is made on the first run). All in this session's
            # transaction, the cursor reached too: a run that fails half way leaves the previous
            # copy and the previous cursor as they were (state never ahead of the data).
            columns: Columns = []
            read = 0
            nothing_new = job.mode == "incremental" and window is None
            batches = (
                _source_batches(source, job.source_object, event.org_id, window, cursor_type)
                if not nothing_new
                else _no_batches()
            )
            async for columns, batch in batches:
                read += len(batch)
                if read > MAX_ROWS:
                    raise ValueError(
                        f"the source table has more than {MAX_ROWS} rows; ingest it incrementally"
                    )
                if batch or job.mode == "full":
                    await _materialize(
                        session,
                        table_name,
                        columns,
                        batch,
                        replace=job.mode == "full" and read == len(batch),
                    )
            if window is not None:
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
            if await session.get(catalog.Dataset, dataset_id) is None:
                session.add(
                    catalog.Dataset(
                        id=dataset_id,
                        org_id=event.org_id,
                        source_id=None,
                        data_space_id=job.data_space_id,
                        origin="derived",
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
            # The columns as materialized, so the semantic layer knows a text month from a date.
            # A dataset catalogued before its columns were gets them on its next run.
            if (
                await session.scalar(
                    select(catalog.ColumnMeta.id)
                    .where(catalog.ColumnMeta.dataset_id == dataset_id)
                    .limit(1)
                )
                is None
            ):
                await session.flush()
                session.add_all(
                    catalog.ColumnMeta(
                        id=uuid.uuid4(),
                        org_id=event.org_id,
                        dataset_id=dataset_id,
                        dataset_version=1,
                        name=column["name"],
                        ordinal=ordinal,
                        data_type=column["type"],
                        normalized_type=_NORMALIZED[column["type"]],
                        display_name=column["label"] if column["label"] != column["name"] else None,
                        created_by=principal_id,
                        updated_by=principal_id,
                    )
                    for ordinal, column in enumerate(columns)
                )
    except (
        SecretUnavailable,
        ValueError,
        asyncpg.PostgresError,
        source_drivers.DriverError,
    ) as exc:
        await _fail(event, run_id, str(exc))


HANDLERS: dict[str, Handler] = {CREATED: handle, BACKFILL: handle}
