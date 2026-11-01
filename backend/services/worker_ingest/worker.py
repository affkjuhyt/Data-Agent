"""Source-table ingestion: a table of a customer's database copied into the workspace.

PostgreSQL through asyncpg; MySQL, SQL Server and ClickHouse through libs/source_drivers, the
drivers worker-agent's executor uses (GEN-12). Another engine is refused and the run recorded as
failed; a job is never marked successful without materializing and cataloguing rows.
"""

from __future__ import annotations

import datetime
import decimal
import json
import re
import uuid
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


async def _read_driver(
    source: catalog.Source, schema: str, table: str, org_id: uuid.UUID
) -> tuple[list[dict[str, str]], list[list[Any]]]:
    if source.kind not in _QUOTED:
        raise ValueError(f"source connector {source.kind} is not wired for ingestion")
    credentials = await Reader().read(org_id, source.secret_ref)
    names, rows = await source_drivers.read(
        source,
        credentials.user,
        credentials.password,
        "SELECT * FROM " + _QUOTED[source.kind].format(schema=schema, table=table),
        min(int(source.query_timeout_s or 120), 300),
    )
    labels = [c["name"] for c in names]
    columns = [
        {"name": name, "label": label, "type": _workspace_type([row[i] for row in rows])}
        for i, (name, label) in enumerate(zip(tabular.physical_names(labels), labels, strict=True))
    ]
    return columns, [[_plain(value) for value in row] for row in rows]


async def _read_source(
    source: catalog.Source, object_name: str, org_id: uuid.UUID
) -> tuple[list[dict[str, str]], list[list[Any]]]:
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
    schema, table = _identifier(schema), _identifier(table)
    if source.kind != "postgresql":
        return await _read_driver(source, schema, table, org_id)
    credentials = await Reader().read(org_id, source.secret_ref)
    conn = await asyncpg.connect(
        host=source.host,
        port=source.port or 5432,
        database=source.database_name,
        user=credentials.user,
        password=credentials.password,
        ssl=str(options.get("sslmode") or "verify-full"),
        timeout=min(source.query_timeout_s, 30),
        statement_cache_size=0,
    )
    try:
        statement = await conn.prepare(f'SELECT * FROM "{schema}"."{table}"')
        records = await statement.fetch()
        attributes = statement.get_attributes()
        # The source's own names ("CustomerID", "Ngày hạch toán") are labels; the table takes
        # physical ones (GEN-13).
        columns = [
            {"name": name, "label": attr.name, "type": _WORKSPACE_TYPES.get(attr.type.name, "text")}
            for name, attr in zip(
                tabular.physical_names([a.name for a in attributes]), attributes, strict=True
            )
        ]
        return columns, [[_plain(value) for value in record] for record in records]
    finally:
        await conn.close()


def _table_name(job: ingest.IngestionJob) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", job.name.lower()).strip("_")[:45] or "source"
    return f"ingest_{stem}_{job.id.hex[:8]}"


async def _materialize(
    session: Any, table_name: str, columns: list[dict[str, str]], rows: list[list[Any]]
) -> None:
    typed = [{"name": column["name"], "type": column["type"]} for column in columns]
    await session.execute(
        text(
            "SELECT public.da_materialize_upload(:table_name, CAST(:columns AS jsonb), CAST(:rows AS jsonb), true)"
        ),
        {"table_name": table_name, "columns": json.dumps(typed), "rows": json.dumps(rows)},
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
            if job.mode != "full":
                raise ValueError(
                    "only full ingestion is wired; incremental and CDC need cursor adapters"
                )
            columns, rows = await _read_source(source, job.source_object, event.org_id)
            dataset_id = job.target_dataset_id or uuid.uuid4()
            table_name = _table_name(job)
            await _materialize(session, table_name, columns, rows)
            await session.execute(
                update(ingest.JobRun)
                .where(ingest.JobRun.id == run_id)
                .values(
                    status="succeeded",
                    rows_read=len(rows),
                    rows_written=len(rows),
                    finished_at=datetime.datetime.now(datetime.UTC),
                )
            )
            await session.execute(
                update(ingest.IngestionJob)
                .where(ingest.IngestionJob.id == job.id)
                .values(target_dataset_id=dataset_id, consecutive_failures=0)
            )
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
                        row_estimate=len(rows),
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
