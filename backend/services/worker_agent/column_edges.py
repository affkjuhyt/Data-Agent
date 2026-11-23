"""Column lineage of a table a pipeline or a derived dataset wrote (DF-16, F-23).

Both linked the table they wrote to the datasets they read, table by table. After writing, the
table's columns are recorded in the catalogue (column_meta, as an upload's are) and each is linked
to the source columns it was computed from (`libs/sql_guard/lineage.py`): a column → column edge
per pair, `via` and `via_ref` the pipeline or the derived dataset. The columns an earlier save
traced and this one does not have their edges closed (valid_to), so the graph says what the
current version reads.

A query the trace cannot follow, or a column it cannot place, keeps the table-level edges the
caller already writes: column edges add to them, never replace them.

An ingest job's table gets its `source → dataset` edge here too (`source_edge`), from the
`dataset.version_created` its run announces: worker-ingest has no svc_pipeline connection, and the
api drew that edge from catalog.dataset.source_id at read time, an edge no writer recorded.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, update

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, ingest
from libs.dataagent_db.models import pipeline as pl
from libs.sql_guard import SqlRejected, dialect_of
from libs.sql_guard.lineage import column_lineage
from services.worker_agent import schema_changes
from services.worker_agent.events import Event
from services.worker_agent.query.scope import default_schema

logger = logging.getLogger(__name__)

# The workspace column types (da_materialize_upload) in the catalogue's vocabulary.
_NORMALIZED = {
    "text": "string",
    "numeric": "decimal",
    "boolean": "boolean",
    "date": "date",
    "timestamptz": "timestamp",
    "jsonb": "json",
}


def _fold(name: str) -> str:
    return name.lower()


async def record(
    *,
    org_id: uuid.UUID,
    data_space_id: uuid.UUID,
    actor: uuid.UUID | None,
    source: catalog.Source,
    sql: str,
    inputs: Sequence[uuid.UUID],
    target_id: uuid.UUID,
    result_names: Sequence[str],
    columns: Sequence[dict[str, Any]],
    via: str,
    via_ref: uuid.UUID,
) -> int:
    """Record the target's columns and link each to its sources; returns the column edges open
    after the call. `result_names` are the query's own output names, `columns` the physical
    columns written ({name, type}), in the same order."""
    async with scoped_session("svc_catalog", org_id, actor) as session:
        target = await session.get(catalog.Dataset, target_id)
        if target is None:
            return 0
        target_columns = await _target_columns(
            session, org_id, target_id, int(target.current_version or 1), columns, actor
        )
        schema, by_name = await _input_columns(session, inputs)
    if not by_name:
        return 0
    dialect = dialect_of(source.kind)
    try:
        traced = column_lineage(sql, schema, dialect, default_schema=default_schema(source))
    except SqlRejected:
        return 0
    if not traced.complete:
        logger.info("column lineage of %s %s not traced; tables only", via, via_ref)
    # The query's output name of each physical column: a result column "Doanh thu" was written
    # as doanh_thu. The trace names outputs as the query does, folded like the engine folds.
    physical = {_fold(r): c["name"] for r, c in zip(result_names, columns, strict=True)}
    pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for edge in traced.edges:
        target_name = physical.get(_fold(edge.output_column))
        to_id = target_columns.get(target_name) if target_name else None
        from_id = by_name.get(
            (_fold(edge.source_schema or ""), _fold(edge.source_table), _fold(edge.source_column))
        )
        if to_id is not None and from_id is not None:
            pairs.add((from_id, to_id))
    now = datetime.datetime.now(datetime.UTC)
    async with scoped_session("svc_pipeline", org_id, actor) as session:
        open_edges = (
            await session.execute(
                select(pl.LineageEdge.id, pl.LineageEdge.from_id, pl.LineageEdge.to_id).where(
                    pl.LineageEdge.org_id == org_id,
                    pl.LineageEdge.to_kind == "column",
                    pl.LineageEdge.to_id.in_(list(target_columns.values()) or [uuid.uuid4()]),
                    pl.LineageEdge.via == via,
                    pl.LineageEdge.via_ref == via_ref,
                    pl.LineageEdge.valid_to.is_(None),
                )
            )
        ).all()
        kept = {(e.from_id, e.to_id) for e in open_edges}
        stale = [e.id for e in open_edges if (e.from_id, e.to_id) not in pairs]
        if stale:
            await session.execute(
                update(pl.LineageEdge).where(pl.LineageEdge.id.in_(stale)).values(valid_to=now)
            )
        for from_id, to_id in sorted(pairs - kept):
            session.add(
                pl.LineageEdge(
                    id=uuid7(),
                    org_id=org_id,
                    data_space_id=data_space_id,
                    from_kind="column",
                    from_id=from_id,
                    to_kind="column",
                    to_id=to_id,
                    via=via,
                    via_ref=via_ref,
                )
            )
    return len(pairs)


async def _target_columns(
    session: Any,
    org_id: uuid.UUID,
    target_id: uuid.UUID,
    version: int,
    columns: Sequence[dict[str, Any]],
    actor: uuid.UUID | None,
) -> dict[str, uuid.UUID]:
    """The target's column ids by name, adding the ones the catalogue has not recorded."""
    live = (await session.scalars(schema_changes.live_columns(target_id, version))).all()
    known = {c.name: c.id for c in live}
    ordinal = max((c.ordinal for c in live), default=0)
    for column in columns:
        name = str(column["name"])
        if name in known:
            continue
        ordinal += 1
        row = catalog.ColumnMeta(
            id=uuid7(),
            org_id=org_id,
            dataset_id=target_id,
            dataset_version=version,
            name=name,
            ordinal=ordinal,
            data_type=str(column["type"]),
            normalized_type=_NORMALIZED.get(str(column["type"]), "string"),
            created_by=actor,
            updated_by=actor,
        )
        session.add(row)
        known[name] = row.id
    await session.flush()
    return known


async def _input_columns(
    session: Any, inputs: Sequence[uuid.UUID]
) -> tuple[dict[str, dict[str, dict[str, str]]], dict[tuple[str, str, str], uuid.UUID]]:
    """The inputs' current columns as a schema for the trace, and their ids by (schema, table,
    column), names folded."""
    schema: dict[str, dict[str, dict[str, str]]] = {}
    by_name: dict[tuple[str, str, str], uuid.UUID] = {}
    if not inputs:
        return schema, by_name
    datasets = (
        await session.scalars(select(catalog.Dataset).where(catalog.Dataset.id.in_(list(inputs))))
    ).all()
    for dataset in datasets:
        columns = (
            await session.scalars(
                schema_changes.live_columns(dataset.id, int(dataset.current_version or 1))
            )
        ).all()
        table = schema.setdefault(dataset.schema_name, {}).setdefault(dataset.table_name, {})
        for column in columns:
            # The trace needs names only; a source's type spelling ("numeric(19,4)",
            # "Nullable(String)") is not sqlglot's to parse here.
            table[column.name] = "text"
            by_name[(_fold(dataset.schema_name), _fold(dataset.table_name), _fold(column.name))] = (
                column.id
            )
    return schema, by_name


async def source_edge(event: Event) -> bool:
    """`source → dataset` for a version an ingest run wrote (created_by_kind job_run), `via`
    ingest and `via_ref` the job; once per pair while it stays open. True when one was written."""
    p = event.payload
    if p.get("created_by_kind") != "job_run" or not p.get("run_id"):
        return False
    dataset_id = uuid.UUID(str(p["dataset_id"]))
    run_id = uuid.UUID(str(p["run_id"]))
    async with scoped_session("svc_ingest", event.org_id) as session:
        job_id = await session.scalar(
            select(ingest.JobRun.job_id).where(ingest.JobRun.id == run_id)
        )
    async with scoped_session("svc_catalog", event.org_id) as session:
        dataset = await session.get(catalog.Dataset, dataset_id)
    if job_id is None or dataset is None or dataset.source_id is None:
        return False
    async with scoped_session("svc_pipeline", event.org_id) as session:
        known = await session.scalar(
            select(pl.LineageEdge.id).where(
                pl.LineageEdge.org_id == event.org_id,
                pl.LineageEdge.from_kind == "source",
                pl.LineageEdge.from_id == dataset.source_id,
                pl.LineageEdge.to_kind == "dataset",
                pl.LineageEdge.to_id == dataset_id,
                pl.LineageEdge.valid_to.is_(None),
            )
        )
        if known is not None:
            return False
        session.add(
            pl.LineageEdge(
                id=uuid7(),
                org_id=event.org_id,
                data_space_id=dataset.data_space_id,
                from_kind="source",
                from_id=dataset.source_id,
                to_kind="dataset",
                to_id=dataset_id,
                via="ingest",
                via_ref=job_id,
            )
        )
    return True
