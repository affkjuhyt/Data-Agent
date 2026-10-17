"""Source-table ingestion for the first real worker-ingest slice.

Only PostgreSQL table sources are enabled here. The worker refuses unsupported source kinds and
records a failed run; it never marks a job successful without materializing and cataloguing rows.
"""

from __future__ import annotations

import datetime
import json
import re
import uuid
from typing import Any

import asyncpg
from sqlalchemy import select, text, update

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


async def _read_source(
    source: catalog.Source, object_name: str, org_id: uuid.UUID
) -> tuple[list[dict[str, str]], list[list[Any]]]:
    if source.kind != "postgresql":
        raise ValueError(f"source connector {source.kind} is not wired for ingestion")
    parts = object_name.split(".")
    if len(parts) == 1:
        schema, table = str((source.conn_options or {}).get("schema") or "public"), parts[0]
    elif len(parts) == 2:
        schema, table = parts
    else:
        raise ValueError("source_object must be table or schema.table")
    schema, table = _identifier(schema), _identifier(table)
    credentials = await Reader().read(org_id, source.secret_ref)
    options = source.conn_options or {}
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
        columns = [
            {"name": attr.name, "type": _WORKSPACE_TYPES.get(attr.type.name, "text")}
            for attr in statement.get_attributes()
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
    typed = [{"name": _identifier(column["name"]), "type": column["type"]} for column in columns]
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
                        created_by=principal_id,
                        updated_by=principal_id,
                    )
                    for ordinal, column in enumerate(columns)
                )
    except (SecretUnavailable, ValueError, asyncpg.PostgresError) as exc:
        await _fail(event, run_id, str(exc))


HANDLERS: dict[str, Handler] = {CREATED: handle, BACKFILL: handle}
