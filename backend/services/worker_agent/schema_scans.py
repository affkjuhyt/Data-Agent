"""Initial PostgreSQL schema scans requested by the catalog API.

The first scan records structure only.  Value profiling is deliberately a later worker step;
the connection wizard needs the table list before the source can be activated.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid

import asyncpg
from sqlalchemy import select

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog
from services.worker_agent.events import Event, Handler
from services.worker_agent.questions import secrets
from services.worker_agent.source_tests import _ssl_mode

EVENT_TYPE = "scan_run.created"
ROLE = "svc_catalog"


def _normalized_type(data_type: str) -> str:
    value = data_type.lower()
    if any(token in value for token in ("int", "serial", "numeric", "decimal", "real", "double")):
        return "integer" if "int" in value or "serial" in value else "decimal"
    if "bool" in value:
        return "boolean"
    if "date" in value and "time" not in value:
        return "date"
    if "time" in value:
        return "timestamp"
    if "json" in value:
        return "json"
    if "bytea" in value or "binary" in value:
        return "binary"
    return "string"


async def _scan_source(source: catalog.Source, tables: list[str] | None) -> list[dict[str, object]]:
    credentials = await secrets().read(source.org_id, source.secret_ref)
    schemas = list(source.scan_schemas or ["public"])
    requested = set(tables or [])
    conn = await asyncpg.connect(
        host=source.host,
        port=source.port or 5432,
        database=source.database_name,
        user=credentials.user,
        password=credentials.password,
        ssl=_ssl_mode(source),
        timeout=30,
        statement_cache_size=0,
        server_settings={"application_name": "dataagent-schema-scan"},
    )
    try:
        rows = await conn.fetch(
            """
            SELECT n.nspname AS schema_name, c.relname AS table_name,
                   CASE c.relkind WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized_view'
                                  ELSE 'table' END AS object_kind,
                   c.reltuples::bigint AS row_estimate,
                   a.attname, format_type(a.atttypid, a.atttypmod) AS data_type,
                   NOT a.attnotnull AS is_nullable, a.attnum AS ordinal
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
            WHERE c.relkind IN ('r', 'v', 'm')
              AND n.nspname = ANY($1::text[])
              AND ($2::text[] IS NULL OR n.nspname || '.' || c.relname = ANY($2::text[]))
            ORDER BY n.nspname, c.relname, a.attnum
            """,
            schemas,
            sorted(requested) if requested else None,
        )
    finally:
        await conn.close()
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        key = (str(row["schema_name"]), str(row["table_name"]))
        table = grouped.setdefault(
            key,
            {
                "schema_name": key[0],
                "table_name": key[1],
                "object_kind": str(row["object_kind"]),
                "row_estimate": max(int(row["row_estimate"] or 0), 0),
                "columns": [],
            },
        )
        table["columns"].append(
            {
                "name": str(row["attname"]),
                "ordinal": int(row["ordinal"]),
                "data_type": str(row["data_type"]),
                "normalized_type": _normalized_type(str(row["data_type"])),
                "is_nullable": bool(row["is_nullable"]),
            }
        )
    return list(grouped.values())


async def scan_schema(event: Event) -> None:
    run_id = uuid.UUID(str(event.payload["scan_run_id"]))
    source_id = uuid.UUID(str(event.payload["source_id"]))
    requested = event.payload.get("tables")
    async with scoped_session(ROLE, event.org_id, event.payload.get("requested_by")) as session:
        run = await session.scalar(
            select(catalog.ScanRun)
            .where(
                catalog.ScanRun.id == run_id,
                catalog.ScanRun.org_id == event.org_id,
                catalog.ScanRun.status == "running",
            )
            .with_for_update()
        )
        source = await session.scalar(
            select(catalog.Source).where(
                catalog.Source.id == source_id,
                catalog.Source.org_id == event.org_id,
            )
        )
        if run is None or source is None or run.kind != "schema":
            return
    try:
        found = await _scan_source(source, requested if isinstance(requested, list) else None)
        now = datetime.datetime.now(datetime.UTC)
        async with scoped_session(ROLE, event.org_id, event.payload.get("requested_by")) as session:
            run = await session.scalar(
                select(catalog.ScanRun).where(catalog.ScanRun.id == run_id).with_for_update()
            )
            for item in found:
                existing = await session.scalar(
                    select(catalog.Dataset)
                    .where(
                        catalog.Dataset.org_id == event.org_id,
                        catalog.Dataset.source_id == source_id,
                        catalog.Dataset.schema_name == item["schema_name"],
                        catalog.Dataset.table_name == item["table_name"],
                        catalog.Dataset.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
                dataset = existing or catalog.Dataset(
                    id=uuid7(),
                    org_id=event.org_id,
                    source_id=source_id,
                    data_space_id=source.data_space_id,
                    origin="source",
                    schema_name=str(item["schema_name"]),
                    table_name=str(item["table_name"]),
                    object_kind=str(item["object_kind"]),
                    row_estimate=int(item["row_estimate"]),
                    created_by=event.payload.get("requested_by"),
                )
                session.add(dataset)
                await session.flush()
                version = int(dataset.current_version or 1)
                if existing is None:
                    columns = item["columns"]
                    schema_hash = hashlib.sha256(repr(columns).encode()).hexdigest()
                    session.add(
                        catalog.DatasetVersion(
                            id=uuid7(),
                            org_id=event.org_id,
                            dataset_id=dataset.id,
                            version=version,
                            schema_hash=schema_hash,
                            valid_from=now,
                            scan_run_id=run_id,
                            created_by=event.payload.get("requested_by"),
                        )
                    )
                    for column in columns:
                        session.add(
                            catalog.ColumnMeta(
                                id=uuid7(),
                                org_id=event.org_id,
                                dataset_id=dataset.id,
                                dataset_version=version,
                                name=str(column["name"]),
                                ordinal=int(column["ordinal"]),
                                data_type=str(column["data_type"]),
                                normalized_type=str(column["normalized_type"]),
                                is_nullable=bool(column["is_nullable"]),
                                created_by=event.payload.get("requested_by"),
                            )
                        )
            run.tables_total = len(found)
            run.tables_scanned = len(found)
            run.status = "succeeded"
            run.finished_at = now
            session.add(
                catalog.Outbox(
                    id=uuid7(),
                    org_id=event.org_id,
                    aggregate_type="scan_run",
                    aggregate_id=run_id,
                    event_type="scan.completed",
                    payload={
                        "scan_run_id": str(run_id),
                        "source_id": str(source_id),
                        "tables_scanned": len(found),
                    },
                )
            )
    except Exception:
        async with scoped_session(ROLE, event.org_id, event.payload.get("requested_by")) as session:
            run = await session.scalar(
                select(catalog.ScanRun).where(catalog.ScanRun.id == run_id).with_for_update()
            )
            if run is not None:
                run.status = "failed"
                run.finished_at = datetime.datetime.now(datetime.UTC)
        raise


HANDLERS: dict[str, Handler] = {EVENT_TYPE: scan_schema}
