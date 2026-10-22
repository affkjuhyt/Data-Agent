"""Running a pipeline (UC-06-01) and saving its result as a table (UC-06-02).

A pipeline is a list of SQL steps over its input datasets. The first step reads the inputs by
their table names; every later step reads the step before it as the relation `t` (the convention
the authoring screen and the api tests use). The steps run as one read-only SELECT, each a CTE,
on the source of the inputs (the Data Space workspace for uploaded and loaded tables), through
the query executor (caller_kind "pipeline", BR-22: nothing is written to a source).

pipeline_run.requested (preview or full): for each step the row count, and for every money
column, a numeric column of the output that the first step already has, its total. A filter or dedupe step may
lower a total (its drop goes to rows_dropped with the condition it names, BR-23); any other step
must keep every total (UC-06-01 E2), else totals_check says which column moved and the api will
not save the result. A preview runs on the first `sample_rows` rows of the first input.

pipeline.materialize_requested: the full result (up to MATERIALIZE_ROWS) is written to
workspace.{table_name} through public.da_materialize_upload (svc_ingest), registered as a derived
dataset (svc_catalog), linked to its inputs in lineage (svc_pipeline, via "pipeline") and the
pipeline becomes active with that table as its target.
"""

from __future__ import annotations

import datetime
import decimal
import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select, text, update

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import pipeline as pl
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import run_query

logger = logging.getLogger(__name__)

ROLE = "svc_pipeline"
RUN = "pipeline_run.requested"
MATERIALIZE = "pipeline.materialize_requested"
MATERIALIZE_ROWS = 200_000
DROPPING = ("filter", "dedupe")
_PREVIOUS = re.compile(r"\b(from|join)\s+t\b(?!\s*\.)", re.IGNORECASE)
_NUMERIC = {"numeric", "int2", "int4", "int8", "float4", "float8", "decimal", "money"}
_TYPES = {"bool": "boolean", "date": "date"} | {t: "numeric" for t in _NUMERIC}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def chain(steps: list[dict[str, Any]], upto: int, sample: str | None = None) -> str:
    """One SELECT returning step `upto`'s result: each step a CTE, `t` the step before it.

    `sample` replaces the first input's name by a sampled subquery (a preview)."""
    ctes = []
    for i, step in enumerate(steps[: upto + 1]):
        code = str(step["code"]).strip().rstrip(";")
        if i == 0 and sample is not None:
            name, sampled = sample.split("|", 1)
            code = re.sub(
                rf"\b(from|join)\s+{re.escape(name)}\b",
                rf"\1 ({sampled}) AS {name}",
                code,
                flags=re.IGNORECASE,
            )
        if i > 0:
            code = _PREVIOUS.sub(rf"\1 s{i - 1} AS t", code)
        ctes.append(f"s{i} AS ({code})")
    return "WITH " + ", ".join(ctes) + f" SELECT * FROM s{upto}"


def physical_name(name: str, dataset_id: uuid.UUID) -> str:
    """The workspace table of a saved pipeline: the chosen name, cut to fit, and the dataset's
    own id, so no two Data Spaces (or organisations) ever share one."""
    # All 32 hex digits: a uuid7's first ones are its timestamp, shared by ids made together.
    stem = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:28] or "bang"
    return f"p_{stem}_{dataset_id.hex}"


async def _context(event: Event, *, need_run: bool = True) -> dict[str, Any] | None:
    """The pipeline, its running run (unless `need_run` is off), its inputs and their source."""
    from services.worker_agent.questions import _internal_workspace_source

    p = event.payload
    async with scoped_session(ROLE, event.org_id) as session:
        run = (
            await session.get(pl.PipelineRun, uuid.UUID(str(p["pipeline_run_id"])))
            if need_run
            else None
        )
        row = await session.get(pl.Pipeline, uuid.UUID(str(p["pipeline_id"])))
        if row is None or (need_run and (run is None or run.status != "running")):
            return None
        steps = list(row.steps or [])
        inputs = list(row.input_dataset_ids or [])
        space = row.data_space_id
    async with scoped_session("svc_catalog", event.org_id) as session:
        datasets = list(
            (
                await session.scalars(select(catalog.Dataset).where(catalog.Dataset.id.in_(inputs)))
            ).all()
        )
        sources = {d.source_id for d in datasets}
        source = None
        if len(sources) == 1:
            sid = next(iter(sources))
            source = (
                _internal_workspace_source(space, event.org_id)
                if sid is None
                else await session.get(catalog.Source, sid)
            )
    return {
        "run": run,
        "pipeline": row,
        "steps": steps,
        "datasets": datasets,
        "source": source,
        "space": space,
        "actor": uuid.UUID(str(p["requested_by"])),
    }


async def _fail(event: Event, run_id: uuid.UUID, message: str) -> None:
    async with scoped_session(ROLE, event.org_id) as session:
        await session.execute(
            update(pl.PipelineRun)
            .where(pl.PipelineRun.id == run_id, pl.PipelineRun.status == "running")
            .values(status="failed", error=message[:2000], finished_at=_now())
        )


async def _query(ctx: dict[str, Any], event: Event, sql: str, limit: int) -> Any:
    from services.worker_agent.questions import secrets

    return await run_query(
        org_id=event.org_id,
        data_space_id=ctx["space"],
        principal_id=ctx["actor"],
        caller_kind="pipeline",
        caller_id=ctx["run"].id,
        source=ctx["source"],
        sql=sql,
        secrets=secrets(),
        limit=limit,
    )


async def _measure(ctx: dict[str, Any], event: Event, sample: str | None) -> dict[str, Any] | str:
    """Row counts per step, rows dropped, the money totals check; or why it could not run."""
    steps = ctx["steps"]
    shapes = []
    for i in range(len(steps)):
        out = await _query(ctx, event, f"SELECT * FROM ({chain(steps, i, sample)}) q LIMIT 0", 1)
        if not out.ok:
            return f"step {i + 1}: {out.error or out.status}"
        shapes.append({c["name"]: c["type"] for c in out.columns})
    unknown = [name for name, kind in shapes[-1].items() if kind == "unknown"]
    if unknown:
        # Fail closed: a column whose type the source did not say could be money.
        return {
            "rows_in": None,
            "rows_out": None,
            "rows_dropped": [],
            "totals_check": {
                "matched": False,
                "columns": [
                    {"column": name, "matched": False, "reason": "type unknown"} for name in unknown
                ],
            },
        }
    money = [
        name
        for name, kind in shapes[-1].items()
        if kind in _NUMERIC and all(name in s for s in shapes[:1])
    ]
    counts: list[int] = []
    totals: list[dict[str, decimal.Decimal | None]] = []
    for i in range(len(steps)):
        cols = [m for m in money if shapes[i].get(m) in _NUMERIC]
        sums = "".join(f', sum("{m}")::numeric AS "{m}"' for m in cols)
        out = await _query(
            ctx, event, f"SELECT count(*) AS n{sums} FROM ({chain(steps, i, sample)}) q", 1
        )
        if not out.ok:
            return f"step {i + 1}: {out.error or out.status}"
        names = [c["name"] for c in out.columns]
        values = dict(zip(names, out.rows[0], strict=True))
        counts.append(int(values["n"]))
        totals.append(
            {m: None if values.get(m) is None else decimal.Decimal(str(values[m])) for m in cols}
        )
    first = ctx["datasets"][0] if ctx["datasets"] else None
    rows_in = counts[0]
    if first is not None and sample is None:
        out = await _query(
            ctx, event, f'SELECT count(*) FROM "{first.schema_name}"."{first.table_name}"', 1
        )
        if out.ok:
            rows_in = int(out.rows[0][0])
    dropped = []
    checks: dict[str, dict[str, Any]] = {m: {"column": m, "matched": True} for m in money}
    for i in range(1, len(steps)):
        kind = steps[i].get("kind")
        # Only a step that keeps the columns as they are and does not add rows is a filter;
        # a step labelled filter that does anything else is held to the totals like any other.
        if kind in DROPPING and shapes[i] == shapes[i - 1] and counts[i] <= counts[i - 1]:
            dropped.append(
                {
                    "step": i + 1,
                    "kind": kind,
                    "rows": counts[i - 1] - counts[i],
                    "condition": steps[i].get("drop_condition"),
                }
            )
            continue
        for m in money:
            before, after = totals[i - 1].get(m), totals[i].get(m)
            if m in totals[i - 1] and m in totals[i] and before != after:
                checks[m] = {
                    "column": m,
                    "matched": False,
                    "step": i + 1,
                    "before": str(before),
                    "after": str(after),
                }
    columns = list(checks.values())
    return {
        "rows_in": rows_in,
        "rows_out": counts[-1],
        "rows_dropped": dropped,
        "totals_check": {"matched": all(c["matched"] for c in columns), "columns": columns},
    }


async def step_preview_sql(
    org_id: uuid.UUID, principal_id: uuid.UUID, p: dict[str, Any]
) -> tuple[catalog.Source, str]:
    """One step previewed (a query run of mode pipeline_step, UC-06-01 step 3), as SQL on the
    pipeline's source: the rows a filter or dedupe drops (the step before EXCEPT the step), any
    other step's output. The rows go through the run's policy like any query's."""
    from services.worker_agent.query_runs import Unbuildable

    ctx = await _context(
        Event(
            event_id=uuid7(),
            org_id=org_id,
            event_type="query_run.requested",
            aggregate_type="pipeline",
            aggregate_id=uuid.UUID(str(p["pipeline_id"])),
            payload={"pipeline_id": str(p["pipeline_id"]), "requested_by": str(principal_id)},
        ),
        need_run=False,
    )
    if ctx is None:
        raise Unbuildable("Pipeline không còn.")
    if ctx["source"] is None:
        raise Unbuildable("Các bảng đầu vào không cùng một nguồn.")
    steps, seq = ctx["steps"], int(p["seq"])
    if not 1 <= seq <= len(steps):
        raise Unbuildable("Bước không còn trong pipeline.")
    if any(s.get("language", "sql") != "sql" for s in steps[:seq]):
        raise Unbuildable("Chưa xem trước được bước Python.")
    if steps[seq - 1].get("kind") in DROPPING and seq > 1:
        sql = f"({chain(steps, seq - 2)}) EXCEPT ({chain(steps, seq - 1)})"
        return ctx["source"], f"SELECT * FROM ({sql}) q"
    return ctx["source"], chain(steps, seq - 1)


async def run_requested(event: Event) -> None:
    ctx = await _context(event)
    if ctx is None:
        return
    run = ctx["run"]
    if ctx["source"] is None:
        await _fail(event, run.id, "The inputs are not on one queryable source.")
        return
    if not ctx["steps"]:
        await _fail(event, run.id, "The pipeline has no step.")
        return
    if any(s.get("language", "sql") != "sql" for s in ctx["steps"]):
        await _fail(event, run.id, "Python steps are not run yet; only SQL steps are.")
        return
    sample = None
    if run.mode == "preview" and ctx["datasets"]:
        first = ctx["datasets"][0]
        limit = int(run.sample_rows or 1000)
        sample = f'{first.table_name}|SELECT * FROM "{first.schema_name}"."{first.table_name}" LIMIT {limit}'
    result = await _measure(ctx, event, sample)
    if isinstance(result, str):
        await _fail(event, run.id, result)
        return
    async with scoped_session(ROLE, event.org_id) as session:
        await session.execute(
            update(pl.PipelineRun)
            .where(pl.PipelineRun.id == run.id, pl.PipelineRun.status == "running")
            .values(status="succeeded", finished_at=_now(), **result)
        )
    logger.info(
        "worker-agent | pipeline run %s: %s → %s rows",
        run.id,
        result["rows_in"],
        result["rows_out"],
    )


async def materialize_requested(event: Event) -> None:
    ctx = await _context(event)
    if ctx is None:
        return
    run, p = ctx["run"], event.payload
    if ctx["source"] is None or not ctx["steps"]:
        await _fail(event, run.id, "The pipeline cannot run: no step or no single source.")
        return
    result = await _measure(ctx, event, None)
    if isinstance(result, str):
        await _fail(event, run.id, result)
        return
    if not result["totals_check"]["matched"]:
        await _fail(
            event, run.id, "Money column totals moved in a step that is not a filter (UC-06-01 E2)."
        )
        return
    out = await _query(ctx, event, chain(ctx["steps"], len(ctx["steps"]) - 1), MATERIALIZE_ROWS)
    if not out.ok:
        await _fail(event, run.id, out.error or out.status)
        return
    if out.truncated:
        await _fail(event, run.id, f"More than {MATERIALIZE_ROWS} rows: not saved as a table yet.")
        return
    columns = [
        {"name": c["name"].lower(), "type": _TYPES.get(c["type"], "text")} for c in out.columns
    ]
    rows = [[None if v is None else str(v) for v in row] for row in out.rows]
    actor = ctx["actor"]
    dataset_id = (
        uuid.UUID(str(p["overwrite_dataset_id"])) if p.get("overwrite_dataset_id") else uuid7()
    )
    async with scoped_session("svc_catalog", event.org_id, actor) as session:
        target = await session.get(catalog.Dataset, dataset_id)
        if target is not None and (
            target.data_space_id != ctx["space"] or target.schema_name != "workspace"
        ):
            target = None
    # The workspace schema is shared by every organisation: the physical name is the server's,
    # unique to this dataset, and the name the author chose is its display name. Replacing a
    # table only ever replaces this pipeline's own target.
    table = (
        target.table_name if target is not None else physical_name(str(p["table_name"]), dataset_id)
    )
    async with scoped_session("svc_ingest", event.org_id, actor) as session:
        await session.execute(
            text(
                "SELECT public.da_materialize_upload(:t, CAST(:c AS jsonb), CAST(:r AS jsonb), true)"
            ),
            {"t": table, "c": json.dumps(columns), "r": json.dumps(rows)},
        )
    async with scoped_session("svc_catalog", event.org_id, actor) as session:
        existing = await session.get(catalog.Dataset, dataset_id)
        if existing is None:
            session.add(
                catalog.Dataset(
                    id=dataset_id,
                    org_id=event.org_id,
                    source_id=None,
                    data_space_id=ctx["space"],
                    origin="derived",
                    schema_name="workspace",
                    table_name=table,
                    display_name=str(p["table_name"]),
                    row_estimate=len(rows),
                    enabled=True,
                    scope=str(p.get("scope") or "data_space"),
                    created_by=actor,
                    updated_by=actor,
                )
            )
        else:
            existing.row_estimate, existing.updated_by, existing.updated_at = (
                len(rows),
                actor,
                _now(),
            )
            existing.current_version = existing.current_version + 1
    async with scoped_session(ROLE, event.org_id, actor) as session:
        for d in ctx["datasets"]:
            known = await session.scalar(
                select(pl.LineageEdge.id).where(
                    pl.LineageEdge.from_id == d.id,
                    pl.LineageEdge.to_id == dataset_id,
                    pl.LineageEdge.valid_to.is_(None),
                )
            )
            if known is None:
                session.add(
                    pl.LineageEdge(
                        id=uuid7(),
                        org_id=event.org_id,
                        data_space_id=ctx["space"],
                        from_kind="dataset",
                        from_id=d.id,
                        to_kind="dataset",
                        to_id=dataset_id,
                        via="pipeline",
                        via_ref=ctx["pipeline"].id,
                    )
                )
        await session.execute(
            update(pl.Pipeline)
            .where(pl.Pipeline.id == ctx["pipeline"].id)
            .values(
                target_dataset_id=dataset_id, status="active", updated_by=actor, updated_at=_now()
            )
        )
        await session.execute(
            update(pl.PipelineRun)
            .where(pl.PipelineRun.id == run.id, pl.PipelineRun.status == "running")
            .values(status="succeeded", finished_at=_now(), **result)
        )
    # The table's quality rules run on what was just written (UC-06-07, run_kind pipeline_run).
    from services.worker_agent import quality

    await quality.evaluate_dataset(
        event.org_id, dataset_id, run_kind="pipeline_run", run_ref=run.id
    )
    logger.info(
        "worker-agent | pipeline %s saved as workspace.%s (%d rows)",
        ctx["pipeline"].id,
        table,
        len(rows),
    )


HANDLERS: dict[str, Handler] = {RUN: run_requested, MATERIALIZE: materialize_requested}
