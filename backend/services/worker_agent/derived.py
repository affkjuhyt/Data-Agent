"""Derived datasets saved from Explore and the SQL editor (UC-06-05).

The api registers the dataset, disabled, and emits on catalog.outbox:
- dataset.derive_requested: a table's rows under Explore's column filters, optionally without
  duplicate rows;
- dataset.sql_derived_requested: the rows of the editor's read-only SQL, on the one source of
  the Data Space that holds every table it reads (query_runs.sql_source).

Either way the rows are read through the query executor as the person who saved (the Data Space
boundary, the read-only guard and their column masks apply), written to a workspace table whose
physical name is the server's (pipelines.physical_name), and the dataset is pointed at it,
enabled, with lineage edges from what it was read from. A dataset is built once, when saved: a
redelivered event finds it enabled and leaves it. write_mode merge needs a key the request does
not carry and is refused; replace and append both start the new table. A dataset that cannot be
built stays disabled, its description saying why.

The SQL is not stored with the dataset, so a `refresh` after the build at save time (and append
or replace on it) waits for a definition column on catalog.dataset.
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid
from typing import Any

from sqlalchemy import select, text
from sqlglot import exp

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import pipeline as pl
from services.worker_agent.events import Event, Handler
from services.worker_agent.pipelines import _TYPES, MATERIALIZE_ROWS, physical_name
from services.worker_agent.query.executor import ROLE as QUERY_ROLE
from services.worker_agent.query.executor import run_query
from services.worker_agent.query.guard import SqlRejected, check_read_only, dialect_of, tables_of
from services.worker_agent.query.scope import default_schema

logger = logging.getLogger(__name__)

DERIVE = "dataset.derive_requested"
SQL_DERIVED = "dataset.sql_derived_requested"


class _Refused(Exception):
    """The dataset cannot be built; the message is what its description says."""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _fail(org_id: uuid.UUID, dataset_id: uuid.UUID, actor: uuid.UUID, why: str) -> None:
    logger.info("worker-agent | derived dataset %s not built: %s", dataset_id, why)
    async with scoped_session("svc_catalog", org_id, actor) as session:
        row = await session.get(catalog.Dataset, dataset_id)
        if row is not None and not row.enabled:
            row.description = f"Chưa tạo được bảng: {why}"[:2000]
            row.updated_by, row.updated_at = actor, _now()


async def _inputs(
    session: Any, space: uuid.UUID, source: catalog.Source, sql: str
) -> list[uuid.UUID]:
    """The datasets of the Data Space the SQL reads, for lineage."""
    dialect = dialect_of(source.kind)
    tables = tables_of(check_read_only(sql, dialect), dialect)
    schema = default_schema(source)
    rows = (
        await session.scalars(
            select(catalog.Dataset).where(
                catalog.Dataset.data_space_id == space, catalog.Dataset.deleted_at.is_(None)
            )
        )
    ).all()
    wanted = {(s or schema, n) for s, n in tables}
    return [d.id for d in rows if (d.schema_name, d.table_name) in wanted]


async def _build(
    event: Event,
    *,
    source: catalog.Source,
    sql: str,
    inputs: list[uuid.UUID],
) -> None:
    from services.worker_agent.questions import secrets

    p = event.payload
    dataset_id = uuid.UUID(str(p["dataset_id"]))
    space = uuid.UUID(str(p["data_space_id"]))
    actor = uuid.UUID(str(p["requested_by"]))
    out = await run_query(
        org_id=event.org_id,
        data_space_id=space,
        principal_id=actor,
        caller_kind="ad_hoc",
        caller_id=dataset_id,
        source=source,
        sql=sql,
        secrets=secrets(),
        limit=MATERIALIZE_ROWS,
    )
    if not out.ok:
        raise _Refused(out.error or out.status)
    if out.truncated:
        raise _Refused(f"hơn {MATERIALIZE_ROWS} dòng, chưa lưu thành bảng được")
    if not out.columns:
        raise _Refused("câu truy vấn không trả cột nào")
    columns = [
        {"name": c["name"].lower(), "type": _TYPES.get(c["type"], "text")} for c in out.columns
    ]
    rows = [[None if v is None else str(v) for v in row] for row in out.rows]
    async with scoped_session("svc_catalog", event.org_id, actor) as session:
        dataset = await session.get(catalog.Dataset, dataset_id)
        if dataset is None or dataset.data_space_id != space or dataset.origin != "derived":
            return
        if dataset.enabled:
            return  # built already
        name = str(dataset.display_name or dataset.table_name)
    # The workspace schema is shared by every organisation: the table is the dataset's own.
    table = physical_name(name.split(".")[-1], dataset_id)
    async with scoped_session("svc_ingest", event.org_id, actor) as session:
        await session.execute(
            text(
                "SELECT public.da_materialize_upload(:t, CAST(:c AS jsonb), CAST(:r AS jsonb), true)"
            ),
            {"t": table, "c": json.dumps(columns), "r": json.dumps(rows)},
        )
    async with scoped_session("svc_catalog", event.org_id, actor) as session:
        dataset = await session.get(catalog.Dataset, dataset_id)
        if dataset is None:
            return
        dataset.schema_name, dataset.table_name = "workspace", table
        dataset.row_estimate, dataset.enabled = len(rows), True
        dataset.updated_by, dataset.updated_at = actor, _now()
    async with scoped_session("svc_pipeline", event.org_id, actor) as session:
        for from_id in inputs:
            known = await session.scalar(
                select(pl.LineageEdge.id).where(
                    pl.LineageEdge.from_id == from_id,
                    pl.LineageEdge.to_id == dataset_id,
                    pl.LineageEdge.valid_to.is_(None),
                )
            )
            if known is None:
                session.add(
                    pl.LineageEdge(
                        id=uuid7(),
                        org_id=event.org_id,
                        data_space_id=space,
                        from_kind="dataset",
                        from_id=from_id,
                        to_kind="dataset",
                        to_id=dataset_id,
                        via="derive",
                        via_ref=dataset_id,
                    )
                )
    logger.info(
        "worker-agent | derived dataset %s saved as workspace.%s (%d rows)",
        dataset_id,
        table,
        len(rows),
    )


async def derive_requested(event: Event) -> None:
    from services.worker_agent.query_runs import Unbuildable, dataset_sql

    p = event.payload
    dataset_id, actor = uuid.UUID(str(p["dataset_id"])), uuid.UUID(str(p["requested_by"]))
    space = uuid.UUID(str(p["data_space_id"]))
    try:
        async with scoped_session(QUERY_ROLE, event.org_id, actor) as session:
            source, sql = await dataset_sql(
                session,
                space,
                {"dataset_id": p["source_dataset_id"], "filters": p.get("filters") or []},
            )
        if p.get("drop_duplicates"):
            sql = (
                exp.select(exp.Star())
                .distinct()
                .from_(exp.paren(exp.maybe_parse(sql, dialect=dialect_of(source.kind))).as_("d"))
                .sql(dialect=dialect_of(source.kind))
            )
        await _build(
            event,
            source=source,
            sql=sql,
            inputs=[uuid.UUID(str(p["source_dataset_id"]))],
        )
    except (_Refused, Unbuildable, SqlRejected, KeyError) as exc:
        await _fail(event.org_id, dataset_id, actor, str(exc) or type(exc).__name__)


async def sql_derived_requested(event: Event) -> None:
    from services.worker_agent.query_runs import Unbuildable, sql_source

    p = event.payload
    dataset_id, actor = uuid.UUID(str(p["dataset_id"])), uuid.UUID(str(p["requested_by"]))
    space = uuid.UUID(str(p["data_space_id"]))
    sql = str(p["sql"]).strip().rstrip(";")
    try:
        if p.get("write_mode") == "merge":
            raise _Refused("ghi gộp (merge) cần khoá dòng; hãy chọn thay thế hoặc nối thêm")
        async with scoped_session(QUERY_ROLE, event.org_id, actor) as session:
            source = await sql_source(session, space, sql)
            inputs = await _inputs(session, space, source, sql)
        await _build(event, source=source, sql=sql, inputs=inputs)
    except (_Refused, Unbuildable, SqlRejected) as exc:
        await _fail(event.org_id, dataset_id, actor, str(exc) or type(exc).__name__)


HANDLERS: dict[str, Handler] = {DERIVE: derive_requested, SQL_DERIVED: sql_derived_requested}
