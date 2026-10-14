"""Schema and profile scans of a PostgreSQL source (UC-04-02), requested by the catalog API.

A schema scan reads tables, views, columns, keys and row estimates (step 1). A new table becomes
a dataset with its first version; a table whose structure hash is unchanged keeps its version;
a changed one is not touched and is listed in tables_skipped as schema_changed, for UC-04-03
(step 2). Columns are labelled personal by their name (step 4) and tables that keep history get
their history_kind and business date (3a); a label or a date a person set is never replaced.
Declared foreign keys are recorded as declared relationships, awaiting review. The first scan of
a source (trigger initial) then queues its profile, so the source has one (step 3).

A profile scan reads each table once, through one aggregate query over a sample of it when it is
larger than BR-15 allows to read whole: share empty, distinct values, min and max, and how many
text values follow a personal-data pattern, which is matched inside the source so the values
never leave it (step 4). Most common values come from the same sample; a personal column keeps
none of its values, nor its min and max. Undeclared joins are inferred from column names and the
share of sampled values found in the other table's key (step 5).

A profile only runs inside the source's scan window, outside the bank's peak hours (BR-15). A
table whose statistics outlast the source's query timeout is skipped with the reason (E1); when
the connection is lost, or the window closes, the run stops as partial with resume_from, and
the next profile of the source starts from that table (E2).
"""

from __future__ import annotations

import contextlib
import datetime
import decimal
import uuid
import zoneinfo
from typing import Any

import asyncpg
from sqlalchemy import func, select

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, context, org
from services.worker_agent.events import Event, Handler
from services.worker_agent.questions import secrets
from services.worker_agent.scan_rules import (
    OVERLAP_SHARE,
    SAMPLE_ROWS,
    VALUE_PATTERNS,
    JoinCandidate,
    ValuePattern,
    history_of,
    join_candidates,
    matched_pattern,
    pii_label_for_name,
    sample_percent,
    schema_hash,
    window_allows,
)
from services.worker_agent.source_tests import _ssl_mode

EVENT_TYPE = "scan_run.created"
ROLE = "svc_catalog"
RELATIONSHIP_ROLE = "svc_context"
DEFAULT_ZONE = "Asia/Ho_Chi_Minh"
# Join candidates checked per profile, and values of each read for the overlap.
MAX_JOIN_CANDIDATES = 50
OVERLAP_SAMPLE = 10_000
# Types whose min and max mean something; a boolean or a document has none.
_ORDERABLE = ("integer", "decimal", "date", "timestamp", "string")
# Types not compared value by value (json has no equality, binary is not profiled).
_OPAQUE = ("json", "binary")
# The connection is gone, as opposed to one query failing (E2 against E1).
_LOST = (
    OSError,
    asyncpg.InterfaceError,
    asyncpg.exceptions.PostgresConnectionError,
    asyncpg.exceptions.AdminShutdownError,
    asyncpg.exceptions.CrashShutdownError,
)


def _quote_identifier(schema: str, table: str) -> str:
    """Quote source identifiers; metadata never becomes executable SQL."""
    return ".".join(_quote(part) for part in (schema, table))


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


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


async def _connect(source: catalog.Source, purpose: str) -> asyncpg.Connection:
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
        server_settings={
            "application_name": purpose,
            # Nothing a scan sends can change the source, whatever the account may do.
            "default_transaction_read_only": "on",
        },
    )
    # E1: one statistics query is stopped at the source's own limit (BR-99).
    await conn.execute(f"SET statement_timeout = {int(source.query_timeout_s or 60) * 1000}")
    return conn


async def _scan_source(source: catalog.Source, tables: list[str] | None) -> list[dict[str, Any]]:
    schemas = list(source.scan_schemas or ["public"])
    requested = set(tables or [])
    conn = await _connect(source, "dataagent-schema-scan")
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
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
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
        # The keys and their order are those the stored schema hashes were computed from.
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
        child = grouped.get(key)
        if child is not None:
            child["foreign_keys"].append(
                {
                    "child_column": str(row["child_column"]),
                    "parent_schema": str(row["parent_schema"]),
                    "parent_table": str(row["parent_table"]),
                    "parent_column": str(row["parent_column"]),
                    "child_unique": bool(row["child_unique"]),
                }
            )
    return list(grouped.values())


# --- profile --------------------------------------------------------------------------------------


def _from_clause(table: dict[str, Any]) -> str:
    """What a profile reads of a table: all of it up to BR-15's limit, a repeatable sample above
    it; a view is read up to SAMPLE_ROWS rows, since its size is not known."""
    quoted = _quote_identifier(str(table["schema_name"]), str(table["table_name"]))
    if table["object_kind"] == "view":
        return f"(SELECT * FROM {quoted} LIMIT {SAMPLE_ROWS}) AS s"
    percent = sample_percent(int(table["row_estimate"]))
    if percent is None:
        return f"{quoted} AS s"
    return f"{quoted} AS s TABLESAMPLE SYSTEM ({percent}) REPEATABLE (7)"


def _profile_sql(table: dict[str, Any], masked: set[str]) -> tuple[str, list[str]]:
    """One aggregate over the table's sample for every column, and its regex parameters."""
    parts = ["count(*)::bigint AS n"]
    params: list[str] = []
    for i, column in enumerate(table["columns"]):
        col = f"s.{_quote(str(column['name']))}"
        kind = str(column["normalized_type"])
        parts.append(f"count({col})::bigint AS nn{i}")
        if kind not in _OPAQUE:
            parts.append(f"count(DISTINCT {col})::bigint AS d{i}")
        if kind in _ORDERABLE and str(column["name"]) not in masked:
            parts.append(f"min({col})::text AS lo{i}")
            parts.append(f"max({col})::text AS hi{i}")
        if kind == "string":
            for pattern in VALUE_PATTERNS:
                params.append(pattern.regex)
                value = (
                    f"regexp_replace({col}::text, '[[:space:].()-]', '', 'g')"
                    if pattern.key == "phone"
                    else f"{col}::text"
                )
                parts.append(
                    f"count(*) FILTER (WHERE {value} ~ ${len(params)})::bigint AS p{i}_{pattern.key}"
                )
    return f"SELECT {', '.join(parts)} FROM {_from_clause(table)}", params


async def _profile_table(
    conn: asyncpg.Connection, table: dict[str, Any], masked: set[str]
) -> dict[str, dict[str, Any]]:
    """Each column's profile, by name. A column labelled personal, before or by its pattern now,
    keeps no value of its own."""
    sql, params = _profile_sql(table, masked)
    row = await conn.fetchrow(sql, *params)
    if row is None:
        return {}
    n = int(row["n"] or 0)
    out: dict[str, dict[str, Any]] = {}
    for i, column in enumerate(table["columns"]):
        name, kind = str(column["name"]), str(column["normalized_type"])
        non_null = int(row[f"nn{i}"] or 0)
        pattern: ValuePattern | None = None
        if kind == "string":
            pattern = matched_pattern(
                non_null, {p.key: int(row[f"p{i}_{p.key}"] or 0) for p in VALUE_PATTERNS}
            )
        personal = name in masked or pattern is not None
        top: list[dict[str, Any]] = []
        if not personal and kind not in _OPAQUE and non_null:
            col = f"s.{_quote(name)}"
            values = await conn.fetch(
                f"SELECT {col}::text AS value, count(*)::bigint AS hits FROM {_from_clause(table)}"
                f" WHERE {col} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10"
            )
            top = [{"value": str(v["value"]), "count": int(v["hits"])} for v in values]
        lo = row.get(f"lo{i}") if not personal else None
        hi = row.get(f"hi{i}") if not personal else None
        out[name] = {
            "sample_rows": n,
            "null_ratio": decimal.Decimal(str(round((n - non_null) / n, 5)))
            if n
            else decimal.Decimal("0"),
            "distinct_est": None if kind in _OPAQUE else int(row[f"d{i}"] or 0),
            "min_value": None if lo is None else str(lo),
            "max_value": None if hi is None else str(hi),
            "top_values": top,
            "pattern": pattern,
        }
    return out


async def _overlap(
    conn: asyncpg.Connection, candidate: JoinCandidate, child: dict[str, Any]
) -> float | None:
    """The share of the child column's sampled values found in the parent's key."""
    col = _quote(candidate.child_column)
    parent = _quote_identifier(*candidate.parent)
    key = _quote(candidate.parent_column)
    row = await conn.fetchrow(
        f"SELECT count(*)::bigint AS n,"
        f" count(*) FILTER (WHERE EXISTS (SELECT 1 FROM {parent} p WHERE p.{key} = v.c))::bigint"
        f" AS hits FROM (SELECT s.{col} AS c FROM {_from_clause(child)}"
        f" WHERE s.{col} IS NOT NULL LIMIT {OVERLAP_SAMPLE}) AS v"
    )
    if row is None or not row["n"]:
        return None
    return int(row["hits"]) / int(row["n"])


# --- writing --------------------------------------------------------------------------------------


def _scan_completed(run: catalog.ScanRun, source_id: uuid.UUID) -> catalog.Outbox:
    return catalog.Outbox(
        id=uuid7(),
        org_id=run.org_id,
        aggregate_type="scan_run",
        aggregate_id=run.id,
        event_type="scan.completed",
        payload={
            "scan_run_id": str(run.id),
            "source_id": str(source_id),
            "tables_scanned": run.tables_scanned,
        },
    )


async def _record_relationships(
    event: Event, source: catalog.Source, proposals: list[dict[str, Any]]
) -> None:
    """Relationships awaiting review (BR-100): declared foreign keys and inferred joins."""
    if not proposals:
        return
    async with scoped_session(
        RELATIONSHIP_ROLE, event.org_id, event.payload.get("requested_by")
    ) as session:
        for proposal in proposals:
            existing = (
                await session.scalars(
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
                origin=proposal["origin"],
                overlap_ratio=proposal.get("overlap_ratio"),
                status="pending_review",
                created_by=event.payload.get("requested_by"),
            )
            session.add(relation)
            session.add(
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


def _label_by_name(column: catalog.ColumnMeta) -> None:
    """Step 4 by name, where no label is set and nobody has confirmed one."""
    if column.pii_label is None and not column.pii_confirmed:
        column.pii_label = pii_label_for_name(column.name)


def _history(dataset: catalog.Dataset, columns: list[dict[str, Any]]) -> None:
    """3a, where nobody has said how the table keeps history."""
    if dataset.history_kind != "none" or dataset.business_date_col is not None:
        return
    found = history_of(columns)
    if found is not None:
        dataset.history_kind, dataset.business_date_col = found


async def _schema(event: Event, run_id: uuid.UUID, source: catalog.Source, requested: Any) -> None:
    found = await _scan_source(source, requested if isinstance(requested, list) else None)
    now = datetime.datetime.now(datetime.UTC)
    by = event.payload.get("requested_by")
    proposals: list[dict[str, Any]] = []
    async with scoped_session(ROLE, event.org_id, by) as session:
        run = await session.scalar(
            select(catalog.ScanRun).where(catalog.ScanRun.id == run_id).with_for_update()
        )
        if run is None:
            return
        datasets_by_name: dict[tuple[str, str], catalog.Dataset] = {}
        skipped: list[dict[str, Any]] = []
        for item in found:
            name = (str(item["schema_name"]), str(item["table_name"]))
            columns: list[dict[str, Any]] = item["columns"]
            new_hash = schema_hash(columns)
            existing = await session.scalar(
                select(catalog.Dataset)
                .where(
                    catalog.Dataset.org_id == event.org_id,
                    catalog.Dataset.source_id == source.id,
                    catalog.Dataset.schema_name == name[0],
                    catalog.Dataset.table_name == name[1],
                    catalog.Dataset.deleted_at.is_(None),
                )
                .with_for_update()
            )
            if existing is not None:
                stored = await session.scalar(
                    select(catalog.DatasetVersion.schema_hash).where(
                        catalog.DatasetVersion.dataset_id == existing.id,
                        catalog.DatasetVersion.version == existing.current_version,
                    )
                )
                if stored is not None and stored != new_hash:
                    # Step 2: UC-04-03 compares the versions and asks a person; this scan
                    # writes nothing over the version in use.
                    skipped.append(
                        {
                            "table": f"{name[0]}.{name[1]}",
                            "reason": "schema_changed",
                            "schema_hash": new_hash,
                        }
                    )
                    continue
                existing.row_estimate = int(item["row_estimate"])
                _history(existing, columns)
                for meta in (
                    await session.scalars(
                        select(catalog.ColumnMeta).where(
                            catalog.ColumnMeta.dataset_id == existing.id,
                            catalog.ColumnMeta.dataset_version == existing.current_version,
                            catalog.ColumnMeta.removed_in_version.is_(None),
                        )
                    )
                ).all():
                    _label_by_name(meta)
                datasets_by_name[name] = existing
                continue
            dataset = catalog.Dataset(
                id=uuid7(),
                org_id=event.org_id,
                source_id=source.id,
                data_space_id=source.data_space_id,
                origin="source",
                schema_name=name[0],
                table_name=name[1],
                object_kind=str(item["object_kind"]),
                row_estimate=int(item["row_estimate"]),
                created_by=by,
            )
            _history(dataset, columns)
            session.add(dataset)
            await session.flush()
            datasets_by_name[name] = dataset
            version = int(dataset.current_version or 1)
            session.add(
                catalog.DatasetVersion(
                    id=uuid7(),
                    org_id=event.org_id,
                    dataset_id=dataset.id,
                    version=version,
                    schema_hash=new_hash,
                    valid_from=now,
                    scan_run_id=run_id,
                    created_by=by,
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
                        pii_label=pii_label_for_name(str(column["name"])),
                        created_by=by,
                    )
                )
        for item in found:
            child = datasets_by_name.get((str(item["schema_name"]), str(item["table_name"])))
            if child is None:
                continue
            for foreign_key in item["foreign_keys"]:
                parent = datasets_by_name.get(
                    (str(foreign_key["parent_schema"]), str(foreign_key["parent_table"]))
                )
                if parent is None:
                    continue
                proposal = {
                    "from_dataset_id": child.id,
                    "from_columns": [str(foreign_key["child_column"])],
                    "to_dataset_id": parent.id,
                    "to_columns": [str(foreign_key["parent_column"])],
                    "cardinality": _cardinality_for_foreign_key(bool(foreign_key["child_unique"])),
                    # The source declares it: not an inference, still reviewed before use.
                    "origin": "declared",
                }
                if proposal not in proposals:
                    proposals.append(proposal)
        run.tables_total = len(found)
        run.tables_scanned = len(found) - len(skipped)
        run.tables_skipped = skipped
        # A changed table is handed over, not failed: the scan itself is complete.
        run.status = "succeeded"
        run.finished_at = now
        session.add(_scan_completed(run, source.id))
        if run.trigger == "initial":
            await _queue_profile(session, event, run, source)
    await _record_relationships(event, source, proposals)


async def _queue_profile(
    session: Any, event: Event, run: catalog.ScanRun, source: catalog.Source
) -> None:
    """Step 3 after a source's first scan: its profile, unless one is already running."""
    running = await session.scalar(
        select(func.count())
        .select_from(catalog.ScanRun)
        .where(
            catalog.ScanRun.source_id == source.id,
            catalog.ScanRun.kind == "profile",
            catalog.ScanRun.status == "running",
        )
    )
    if running:
        return
    profile = catalog.ScanRun(
        id=uuid7(), org_id=event.org_id, source_id=source.id, kind="profile", trigger="initial"
    )
    session.add(profile)
    await session.flush()
    session.add(
        catalog.Outbox(
            id=uuid7(),
            org_id=event.org_id,
            aggregate_type="scan_run",
            aggregate_id=profile.id,
            event_type=EVENT_TYPE,
            payload={
                "scan_run_id": str(profile.id),
                "source_id": str(source.id),
                "data_space_id": str(source.data_space_id),
                "kind": "profile",
                "trigger": "initial",
                "tables": event.payload.get("tables"),
            },
        )
    )


async def _zone(session: Any, source: catalog.Source) -> datetime.tzinfo:
    """The source's calendar zone, in which its scan window is written."""
    name = None
    if source.work_calendar_id is not None:
        name = await session.scalar(
            select(org.WorkCalendar.time_zone).where(org.WorkCalendar.id == source.work_calendar_id)
        )
    try:
        return zoneinfo.ZoneInfo(str(name or DEFAULT_ZONE))
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return zoneinfo.ZoneInfo(DEFAULT_ZONE)


async def _profile(event: Event, run_id: uuid.UUID, source: catalog.Source, requested: Any) -> None:
    only = requested if isinstance(requested, list) else None
    found = sorted(
        await _scan_source(source, only),
        key=lambda t: (str(t["schema_name"]), str(t["table_name"])),
    )
    by = event.payload.get("requested_by")
    async with scoped_session(ROLE, event.org_id, by) as session:
        zone = await _zone(session, source)
        start = ""
        if only is None:
            # E2: the last profile of the source stopped at a table; this one starts there.
            last = await session.scalar(
                select(catalog.ScanRun)
                .where(
                    catalog.ScanRun.source_id == source.id,
                    catalog.ScanRun.kind == "profile",
                    catalog.ScanRun.id != run_id,
                    catalog.ScanRun.finished_at.is_not(None),
                )
                .order_by(catalog.ScanRun.finished_at.desc())
                .limit(1)
            )
            if last is not None and last.status == "partial" and last.resume_from:
                start = last.resume_from
        datasets = {
            (d.schema_name, d.table_name): d
            for d in (
                await session.scalars(
                    select(catalog.Dataset).where(
                        catalog.Dataset.source_id == source.id,
                        catalog.Dataset.deleted_at.is_(None),
                    )
                )
            ).all()
        }
        labelled = {
            (c.dataset_id, c.name)
            for c in (
                await session.scalars(
                    select(catalog.ColumnMeta).where(
                        catalog.ColumnMeta.dataset_id.in_([d.id for d in datasets.values()]),
                        catalog.ColumnMeta.removed_in_version.is_(None),
                        catalog.ColumnMeta.pii_label.is_not(None),
                    )
                )
            ).all()
        }
    names = [f"{t['schema_name']}.{t['table_name']}" for t in found]
    first = names.index(start) if start in names else 0
    todo = [t for t in found[first:] if (t["schema_name"], t["table_name"]) in datasets]
    results: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    skipped: list[dict[str, Any]] = []
    stopped_at: str | None = None
    inferred: list[dict[str, Any]] = []
    conn = await _connect(source, "dataagent-schema-profile")
    try:
        for table in todo:
            name = (str(table["schema_name"]), str(table["table_name"]))
            if not window_allows(source.scan_window, datetime.datetime.now(zone)):
                # BR-15: the peak hours begin; the next profile goes on from here.
                stopped_at = f"{name[0]}.{name[1]}"
                break
            dataset = datasets[name]
            masked = {
                str(c["name"])
                for c in table["columns"]
                if (dataset.id, str(c["name"])) in labelled or pii_label_for_name(str(c["name"]))
            }
            try:
                results[name] = await _profile_table(conn, table, masked)
            except asyncpg.QueryCanceledError:
                skipped.append({"table": f"{name[0]}.{name[1]}", "reason": "timeout"})
            except _LOST:
                stopped_at = f"{name[0]}.{name[1]}"
                break
        if stopped_at is None:
            declared = {
                (str(t["schema_name"]), str(t["table_name"]), str(fk["child_column"]))
                for t in found
                for fk in t["foreign_keys"]
            }
            by_name = {(str(t["schema_name"]), str(t["table_name"])): t for t in found}
            for candidate in join_candidates(found, declared, MAX_JOIN_CANDIDATES):
                if not window_allows(source.scan_window, datetime.datetime.now(zone)):
                    break
                child, parent = datasets.get(candidate.child), datasets.get(candidate.parent)
                if child is None or parent is None:
                    continue
                try:
                    share = await _overlap(conn, candidate, by_name[candidate.child])
                except _LOST:
                    break
                except asyncpg.PostgresError:
                    continue
                if share is not None and share >= OVERLAP_SHARE:
                    inferred.append(
                        {
                            "from_dataset_id": child.id,
                            "from_columns": [candidate.child_column],
                            "to_dataset_id": parent.id,
                            "to_columns": [candidate.parent_column],
                            "cardinality": "many_to_one",
                            "origin": "inferred",
                            "overlap_ratio": decimal.Decimal(str(round(share, 4))),
                        }
                    )
    finally:
        # A connection already lost cannot be closed cleanly; it is gone either way.
        with contextlib.suppress(*_LOST):
            await conn.close()
    now = datetime.datetime.now(datetime.UTC)
    async with scoped_session(ROLE, event.org_id, by) as session:
        run = await session.scalar(
            select(catalog.ScanRun).where(catalog.ScanRun.id == run_id).with_for_update()
        )
        if run is None:
            return
        for name, profiles in results.items():
            columns = (
                await session.scalars(
                    select(catalog.ColumnMeta).where(
                        catalog.ColumnMeta.dataset_id == datasets[name].id,
                        catalog.ColumnMeta.dataset_version == datasets[name].current_version,
                        catalog.ColumnMeta.removed_in_version.is_(None),
                    )
                )
            ).all()
            for column in columns:
                profile = profiles.get(column.name)
                if profile is None:
                    continue
                pattern: ValuePattern | None = profile["pattern"]
                if pattern is not None and column.pii_label is None and not column.pii_confirmed:
                    column.pii_label = pattern.label
                session.add(
                    catalog.ColumnProfile(
                        id=uuid7(),
                        org_id=event.org_id,
                        column_id=column.id,
                        scan_run_id=run_id,
                        sample_rows=int(profile["sample_rows"]),
                        null_ratio=profile["null_ratio"],
                        distinct_est=profile["distinct_est"],
                        min_value=profile["min_value"],
                        max_value=profile["max_value"],
                        top_values=profile["top_values"],
                        pattern_hint=None if pattern is None else pattern.hint,
                    )
                )
        run.tables_total = len(names)
        run.tables_scanned = first + len(results)
        run.tables_skipped = skipped
        run.resume_from = stopped_at
        run.status = "partial" if skipped or stopped_at else "succeeded"
        run.finished_at = now
        session.add(_scan_completed(run, source.id))
    await _record_relationships(event, source, inferred)


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
        kind = run.kind
    try:
        if kind == "schema":
            await _schema(event, run_id, source, requested)
        else:
            await _profile(event, run_id, source, requested)
    except Exception:
        async with scoped_session(ROLE, event.org_id, event.payload.get("requested_by")) as session:
            run = await session.scalar(
                select(catalog.ScanRun).where(catalog.ScanRun.id == run_id).with_for_update()
            )
            if run is not None and run.status == "running":
                run.status = "failed"
                run.finished_at = datetime.datetime.now(datetime.UTC)
        raise


HANDLERS: dict[str, Handler] = {EVENT_TYPE: scan_schema}
