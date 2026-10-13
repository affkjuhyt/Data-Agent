"""Initial PostgreSQL schema scans requested by the catalog API.

The first scan records structure only.  Value profiling is deliberately a later worker step;
the connection wizard needs the table list before the source can be activated.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import uuid
from typing import Any

import asyncpg
from sqlalchemy import select

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, context
from services.worker_agent.events import Event, Handler
from services.worker_agent.questions import secrets
from services.worker_agent.source_tests import _ssl_mode

EVENT_TYPE = "scan_run.created"
ROLE = "svc_catalog"
RELATIONSHIP_ROLE = "svc_context"

_PII_NAME_PARTS = frozenset(
    {
        "address",
        "cccd",
        "email",
        "name",
        "phone",
        "passport",
        "account",
        "tax",
    }
)


def _quote_identifier(schema: str, table: str) -> str:
    """Quote source identifiers; metadata never becomes executable SQL."""
    return ".".join(f'"{part.replace(chr(34), chr(34) * 2)}"' for part in (schema, table))


def _profile_value(column_name: str, value: Any) -> str:
    """Return a safe JSON text value; likely personal columns are never sampled into the catalog."""
    if _is_sensitive_column(column_name):
        return "***"
    return str(value)


def _is_sensitive_column(column_name: str) -> bool:
    name = column_name.lower().replace("-", "_")
    return any(part in name.split("_") for part in _PII_NAME_PARTS)


def _cardinality_for_foreign_key(child_unique: bool) -> str:
    return "one_to_one" if child_unique else "many_to_one"


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
        keys = await conn.fetch(
            """
            SELECT n.nspname AS schema_name, c.relname AS table_name, a.attname
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN LATERAL unnest(con.conkey) AS key(attnum) ON true
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = key.attnum
            WHERE con.contype = 'p'
              AND n.nspname = ANY($1::text[])
              AND ($2::text[] IS NULL OR n.nspname || '.' || c.relname = ANY($2::text[]))
            """,
            schemas,
            sorted(requested) if requested else None,
        )
        foreign_keys = await conn.fetch(
            """
            SELECT child_ns.nspname AS child_schema, child.relname AS child_table,
                   child_col.attname AS child_column,
                   parent_ns.nspname AS parent_schema, parent.relname AS parent_table,
                   parent_col.attname AS parent_column,
                   EXISTS (
                       SELECT 1 FROM pg_constraint unique_con
                       WHERE unique_con.conrelid = con.conrelid
                         AND unique_con.contype IN ('p', 'u')
                         AND unique_con.conkey = con.conkey
                   ) AS child_unique
            FROM pg_constraint con
            JOIN pg_class child ON child.oid = con.conrelid
            JOIN pg_namespace child_ns ON child_ns.oid = child.relnamespace
            JOIN pg_class parent ON parent.oid = con.confrelid
            JOIN pg_namespace parent_ns ON parent_ns.oid = parent.relnamespace
            JOIN LATERAL unnest(con.conkey, con.confkey) AS key(child_attnum, parent_attnum) ON true
            JOIN pg_attribute child_col
              ON child_col.attrelid = child.oid AND child_col.attnum = key.child_attnum
            JOIN pg_attribute parent_col
              ON parent_col.attrelid = parent.oid AND parent_col.attnum = key.parent_attnum
            WHERE con.contype = 'f'
              AND child_ns.nspname = ANY($1::text[])
              AND ($2::text[] IS NULL OR child_ns.nspname || '.' || child.relname = ANY($2::text[]))
            ORDER BY child_ns.nspname, child.relname, con.conname, child_col.attnum
            """,
            schemas,
            sorted(requested) if requested else None,
        )
    finally:
        await conn.close()
    primary_keys = {
        (str(row["schema_name"]), str(row["table_name"]), str(row["attname"])) for row in keys
    }
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
                "foreign_keys": [],
            },
        )
        table["columns"].append(
            {
                "name": str(row["attname"]),
                "ordinal": int(row["ordinal"]),
                "data_type": str(row["data_type"]),
                "normalized_type": _normalized_type(str(row["data_type"])),
                "is_nullable": bool(row["is_nullable"]),
                "is_key": (
                    str(row["schema_name"]),
                    str(row["table_name"]),
                    str(row["attname"]),
                )
                in primary_keys,
            }
        )
    for row in foreign_keys:
        key = (str(row["child_schema"]), str(row["child_table"]))
        table = grouped.get(key)
        if table is not None:
            table["foreign_keys"].append(
                {
                    "child_column": str(row["child_column"]),
                    "parent_schema": str(row["parent_schema"]),
                    "parent_table": str(row["parent_table"]),
                    "parent_column": str(row["parent_column"]),
                    "child_unique": bool(row["child_unique"]),
                }
            )
    return list(grouped.values())


async def _profile_source(
    source: catalog.Source, tables: list[dict[str, object]]
) -> dict[tuple[str, str, str], dict[str, object]]:
    """Profile current source tables with bounded aggregates, never returning raw rows."""
    credentials = await secrets().read(source.org_id, source.secret_ref)
    conn = await asyncpg.connect(
        host=source.host,
        port=source.port or 5432,
        database=source.database_name,
        user=credentials.user,
        password=credentials.password,
        ssl=_ssl_mode(source),
        timeout=30,
        statement_cache_size=0,
        server_settings={"application_name": "dataagent-schema-profile"},
    )
    profiles: dict[tuple[str, str, str], dict[str, object]] = {}
    timeout_ms = min(int(source.query_timeout_s or 120), 300) * 1000
    try:
        await conn.execute(f"SET statement_timeout = {timeout_ms}")
        for table in tables:
            schema_name, table_name = str(table["schema_name"]), str(table["table_name"])
            table_sql = _quote_identifier(schema_name, table_name)
            for column in table["columns"]:
                name = str(column["name"])
                column_sql = f'"{name.replace(chr(34), chr(34) * 2)}"'
                stats = await conn.fetchrow(
                    f"""
                    SELECT count(*)::bigint AS sample_rows,
                           count(*) FILTER (WHERE {column_sql} IS NULL)::bigint AS null_count,
                           count(DISTINCT {column_sql})::bigint AS distinct_est,
                           min({column_sql})::text AS min_value,
                           max({column_sql})::text AS max_value
                    FROM {table_sql}
                    """
                )
                top_values: list[dict[str, object]] = []
                if not _is_sensitive_column(name):
                    top = await conn.fetch(
                        f"""
                        SELECT {column_sql}::text AS value, count(*)::bigint AS value_count
                        FROM {table_sql}
                        WHERE {column_sql} IS NOT NULL
                        GROUP BY {column_sql}
                        ORDER BY value_count DESC, value
                        LIMIT 10
                        """
                    )
                    top_values = [
                        {
                            "value": _profile_value(name, row["value"]),
                            "count": int(row["value_count"]),
                        }
                        for row in top
                    ]
                sample_rows = int(stats["sample_rows"] or 0)
                profiles[(schema_name, table_name, name)] = {
                    "sample_rows": sample_rows,
                    "null_ratio": decimal.Decimal(str(int(stats["null_count"] or 0) / sample_rows))
                    if sample_rows
                    else decimal.Decimal("0"),
                    "distinct_est": int(stats["distinct_est"] or 0),
                    "min_value": None if _is_sensitive_column(name) else stats["min_value"],
                    "max_value": None if _is_sensitive_column(name) else stats["max_value"],
                    "top_values": top_values,
                }
    finally:
        await conn.close()
    return profiles


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
        if run is None or source is None or run.kind not in ("schema", "profile"):
            return
    try:
        found = await _scan_source(source, requested if isinstance(requested, list) else None)
        profiles = await _profile_source(source, found) if run.kind == "profile" else {}
        relationship_proposals: list[dict[str, object]] = []
        now = datetime.datetime.now(datetime.UTC)
        async with scoped_session(ROLE, event.org_id, event.payload.get("requested_by")) as session:
            run = await session.scalar(
                select(catalog.ScanRun).where(catalog.ScanRun.id == run_id).with_for_update()
            )
            datasets_by_name: dict[tuple[str, str], catalog.Dataset] = {}
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
                datasets_by_name[(str(item["schema_name"]), str(item["table_name"]))] = dataset
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
                                is_key=bool(column.get("is_key", False)),
                                created_by=event.payload.get("requested_by"),
                            )
                        )
            if run.kind == "schema":
                for item in found:
                    from_dataset = datasets_by_name.get(
                        (str(item["schema_name"]), str(item["table_name"]))
                    )
                    if from_dataset is None:
                        continue
                    for foreign_key in item.get("foreign_keys", []):
                        to_dataset = datasets_by_name.get(
                            (str(foreign_key["parent_schema"]), str(foreign_key["parent_table"]))
                        )
                        if to_dataset is None:
                            continue
                        child_column = str(foreign_key["child_column"])
                        parent_column = str(foreign_key["parent_column"])
                        if not any(
                            proposal["from_dataset_id"] == from_dataset.id
                            and proposal["to_dataset_id"] == to_dataset.id
                            and proposal["from_columns"] == [child_column]
                            and proposal["to_columns"] == [parent_column]
                            for proposal in relationship_proposals
                        ):
                            relationship_proposals.append(
                                {
                                    "from_dataset_id": from_dataset.id,
                                    "from_columns": [child_column],
                                    "to_dataset_id": to_dataset.id,
                                    "to_columns": [parent_column],
                                    "cardinality": _cardinality_for_foreign_key(
                                        bool(foreign_key["child_unique"])
                                    ),
                                }
                            )
            else:
                for item in found:
                    dataset = datasets_by_name.get(
                        (str(item["schema_name"]), str(item["table_name"]))
                    )
                    if dataset is None:
                        continue
                    columns = (
                        await session.scalars(
                            select(catalog.ColumnMeta).where(
                                catalog.ColumnMeta.dataset_id == dataset.id,
                                catalog.ColumnMeta.dataset_version == dataset.current_version,
                                catalog.ColumnMeta.removed_in_version.is_(None),
                            )
                        )
                    ).all()
                    for column in columns:
                        profile = profiles.get(
                            (str(item["schema_name"]), str(item["table_name"]), column.name)
                        )
                        if profile is None:
                            continue
                        session.add(
                            catalog.ColumnProfile(
                                id=uuid7(),
                                org_id=event.org_id,
                                column_id=column.id,
                                scan_run_id=run_id,
                                sample_rows=int(profile["sample_rows"]),
                                null_ratio=profile["null_ratio"],
                                distinct_est=int(profile["distinct_est"]),
                                min_value=profile["min_value"],
                                max_value=profile["max_value"],
                                top_values=profile["top_values"],
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
        if relationship_proposals:
            async with scoped_session(
                RELATIONSHIP_ROLE, event.org_id, event.payload.get("requested_by")
            ) as relation_session:
                for proposal in relationship_proposals:
                    existing = (
                        await relation_session.scalars(
                            select(context.Relationship).where(
                                context.Relationship.org_id == event.org_id,
                                context.Relationship.from_dataset_id == proposal["from_dataset_id"],
                                context.Relationship.to_dataset_id == proposal["to_dataset_id"],
                                context.Relationship.status != "rejected",
                            )
                        )
                    ).all()
                    if any(
                        relation.from_columns == proposal["from_columns"]
                        and relation.to_columns == proposal["to_columns"]
                        for relation in existing
                    ):
                        continue
                    relation = context.Relationship(
                        id=uuid7(),
                        org_id=event.org_id,
                        data_space_id=source.data_space_id,
                        from_dataset_id=proposal["from_dataset_id"],
                        from_columns=proposal["from_columns"],
                        to_dataset_id=proposal["to_dataset_id"],
                        to_columns=proposal["to_columns"],
                        cardinality=proposal["cardinality"],
                        origin="inferred",
                        status="pending_review",
                        created_by=event.payload.get("requested_by"),
                    )
                    relation_session.add(relation)
                    relation_session.add(
                        context.Outbox(
                            id=uuid7(),
                            org_id=event.org_id,
                            aggregate_type="relationship",
                            aggregate_id=relation.id,
                            event_type="relationship.created",
                            payload={
                                "relationship_id": str(relation.id),
                                "data_space_id": str(source.data_space_id),
                                "status": "pending_review",
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
