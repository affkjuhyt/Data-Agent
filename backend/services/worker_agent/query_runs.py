"""Running a query the api recorded `queued`: the `query_run.requested` handler.

Two producers (services/api/modules/qa/query.py):
- the metric explorer (POST /v1/explore/queries, caller_kind question): metrics split by and
  filtered on dimensions. Each metric is compiled by the semantic compiler (BR-01: only compiled
  SQL runs); several metrics are one long result, a `metric` column first, so they must live on
  one source.
- an ad-hoc run (POST /v1/query-runs, caller_kind ad_hoc): one dataset with column filters, built
  here, or the caller's own read-only SQL. SQL names no source, so the source is the one whose
  datasets of this Data Space hold every table the SQL reads; none or several is a refusal.

The run is claimed `queued` → `running` in one statement, so a redelivered event is a no-op, then
completed by the executor, which checks the tables again (Data Space, policies) and stores the
rows the api's GET /v1/query-runs/{id}/rows reads. A run that cannot be built ends `failed` with
the reason. Knowledge time (`as_of` before today) needs the history route, and `compare` a period
to compare (semantic/compare.py, R1-06), which the explorer's request does not carry yet; such a
run fails saying so, it never runs live instead.
"""

from __future__ import annotations

import datetime
import logging
import os
import uuid
from typing import Any

from sqlalchemy import select, update
from sqlglot import exp

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import query as q
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import ROLE, run_query
from services.worker_agent.query.guard import SqlRejected, check_read_only, dialect_of, tables_of
from services.worker_agent.query.scope import default_schema
from services.worker_agent.questions import QUERYABLE, secrets
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import load

logger = logging.getLogger(__name__)

EVENT_TYPE = "query_run.requested"
INTERNAL_WORKSPACE = "internal://workspace"
INTERNAL_SOURCE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
HISTORY = "Truy vấn theo thời điểm tri thức (as_of) chưa chạy được: cần đường lịch sử của nguồn."


class Unbuildable(ValueError):
    """The run cannot be turned into SQL on one source; the message is the run's error."""


def _internal_source(data_space_id: uuid.UUID, org_id: uuid.UUID) -> catalog.Source:
    return catalog.Source(
        id=INTERNAL_SOURCE_ID,
        org_id=org_id,
        data_space_id=data_space_id,
        name="Data Space workspace",
        kind="postgresql",
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("PGPORT", "8300")),
        database_name=os.environ.get("POSTGRES_DB", "dataagent"),
        conn_options={"schema": "workspace"},
        secret_ref=INTERNAL_WORKSPACE,
        mode="federated",
        status="active",
        max_concurrency=4,
        query_timeout_s=120,
    )


async def _claim(event: Event) -> q.QueryRun | None:
    p = event.payload
    async with scoped_session(ROLE, event.org_id, uuid.UUID(str(p["principal_id"]))) as session:
        return await session.scalar(
            update(q.QueryRun)
            .where(
                q.QueryRun.id == uuid.UUID(str(p["query_run_id"])),
                q.QueryRun.status == "queued",
            )
            .values(status="running")
            .returning(q.QueryRun)
        )


async def _fail(run: q.QueryRun, reason: str) -> None:
    async with scoped_session(ROLE, run.org_id, run.principal_id) as session:
        await session.execute(
            update(q.QueryRun)
            .where(q.QueryRun.id == run.id, q.QueryRun.status == "running")
            .values(status="failed", error=reason)
        )


def _queryable(source: catalog.Source | None) -> catalog.Source:
    if source is None or source.mode != "federated" or source.status not in QUERYABLE:
        raise Unbuildable("Nguồn của bảng này chưa được phép truy vấn tại chỗ.")
    return source


async def _explore(
    session: Any, run: q.QueryRun, p: dict[str, Any]
) -> tuple[catalog.Source, str, list[uuid.UUID]]:
    if p.get("compare"):
        raise Unbuildable(
            "So sánh kỳ (compare) cần kỳ được hỏi; yêu cầu Khám phá chưa mang kỳ. Hãy hỏi trên màn"
            " Hỏi đáp, ví dụ: Dư nợ tháng 9/2026 so với tháng trước."
        )
    layer = await load(session, run.data_space_id)
    by_id = {d.id: d.code for d in layer.layer.dimensions.values()}
    try:
        group_by = [by_id[uuid.UUID(str(d))] for d in p.get("dimensions") or []]
        filters: dict[str, list[str]] = {}
        for f in p.get("filters") or []:
            filters.setdefault(by_id[uuid.UUID(str(f["dimension_id"]))], []).extend(
                str(v) for v in f["values"]
            )
    except KeyError as exc:
        raise Unbuildable("Một chiều trong yêu cầu không còn trong lớp ngữ nghĩa.") from exc

    compiled: list[tuple[str, c.Compiled]] = []
    source: catalog.Source | None = None
    for metric_id in (uuid.UUID(str(m)) for m in p["metric_ids"]):
        version = layer.versions.get(metric_id)
        if version is None:
            raise Unbuildable("Chỉ số không còn phiên bản đang dùng.")
        request = c.Request(version, group_by=group_by, filters=filters)
        first = c.compile_metric(request, layer.layer, dialect="postgres")
        dataset = layer.datasets[first.dataset_ids[0]]
        here = _queryable(await session.get(catalog.Source, dataset.source_id))
        if source is not None and here.id != source.id:
            raise Unbuildable("Các chỉ số đã chọn nằm trên các nguồn khác nhau; hãy chạy riêng.")
        source = here
        dialect = dialect_of(source.kind)
        same = (
            first
            if dialect == "postgres"
            else c.compile_metric(request, layer.layer, dialect=dialect)
        )
        compiled.append((version.metric_code, same))
    assert source is not None
    dialect = dialect_of(source.kind)
    parts = [
        exp.select(exp.alias_(exp.Literal.string(code), "metric"), exp.Star()).from_(
            exp.Subquery(
                this=check_read_only(one.sql, dialect),
                alias=exp.TableAlias(this=exp.to_identifier(f"m{i}")),
            )
        )
        for i, (code, one) in enumerate(compiled)
    ]
    union: exp.Query = parts[0]
    for part in parts[1:]:
        union = exp.union(union, part, distinct=False)
    return source, union.sql(dialect=dialect), [one.metric_version_id for _, one in compiled]


_OPS = {"eq": exp.EQ, "ne": exp.NEQ, "gt": exp.GT, "gte": exp.GTE, "lt": exp.LT, "lte": exp.LTE}


def _literal(value: Any) -> exp.Expression:
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, int | float):
        return exp.Literal.number(value)
    return exp.Literal.string(str(value))


def _condition(f: dict[str, Any]) -> exp.Expression:
    column = exp.column(str(f["column"]), quoted=True)
    op, value = f.get("op", "eq"), f.get("value")
    if op == "in":
        return exp.In(this=column, expressions=[_literal(v) for v in value or []])
    if value is None:
        if op == "eq":
            return exp.Is(this=column, expression=exp.Null())
        if op == "ne":
            return exp.Not(this=exp.Is(this=column, expression=exp.Null()))
        raise Unbuildable(f"Không so sánh {op} với giá trị rỗng.")
    return _OPS[op](this=column, expression=_literal(value))


async def dataset_sql(
    session: Any, data_space_id: uuid.UUID, p: dict[str, Any]
) -> tuple[catalog.Source, str]:
    """A dataset's rows under column filters ({dataset_id, filters}), as SQL on its source."""
    dataset = await session.get(catalog.Dataset, uuid.UUID(str(p["dataset_id"])))
    if dataset is None or dataset.data_space_id != data_space_id:
        raise Unbuildable("Bảng không còn trong Data Space.")
    source = (
        _internal_source(data_space_id, dataset.org_id)
        if dataset.schema_name == "workspace" and dataset.source_id is None
        else _queryable(await session.get(catalog.Source, dataset.source_id))
    )
    query = exp.select(exp.Star()).from_(
        exp.table_(dataset.table_name, db=dataset.schema_name, quoted=True)
    )
    for f in p.get("filters") or []:
        query = query.where(_condition(f))
    return source, query.sql(dialect=dialect_of(source.kind))


async def sql_source(session: Any, data_space_id: uuid.UUID, sql: str) -> catalog.Source:
    """The one queryable source whose datasets of the Data Space hold every table the SQL reads."""
    ds = catalog.Dataset
    datasets = (
        await session.scalars(
            select(ds).where(
                ds.data_space_id == data_space_id,
                ds.deleted_at.is_(None),
                ds.enabled.is_(True),
            )
        )
    ).all()
    local_datasets = [d for d in datasets if d.schema_name == "workspace" and d.source_id is None]
    sources = {
        s.id: s
        for s in (
            await session.scalars(
                select(catalog.Source).where(catalog.Source.id.in_({d.source_id for d in datasets}))
            )
        ).all()
    }
    fits: list[catalog.Source] = []
    local_source = (
        _internal_source(data_space_id, uuid.UUID(str(local_datasets[0].org_id)))
        if local_datasets
        else None
    )
    if local_source is not None:
        try:
            tables = tables_of(check_read_only(sql, "postgres"), "postgres")
            held = {(d.schema_name, d.table_name) for d in local_datasets}
            if tables and all((t_schema or "workspace", name) in held for t_schema, name in tables):
                fits.append(local_source)
        except SqlRejected:
            pass
    for source in sources.values():
        try:
            dialect = dialect_of(source.kind)
            tables = tables_of(check_read_only(sql, dialect), dialect)
        except SqlRejected:
            continue
        if not tables:
            raise Unbuildable("Câu SQL không đọc bảng nào của Data Space.")
        schema = default_schema(source)
        held = {(d.schema_name, d.table_name) for d in datasets if d.source_id == source.id}
        if all((t_schema or schema, name) in held for t_schema, name in tables):
            fits.append(source)
    if not fits:
        raise Unbuildable("Câu SQL đọc bảng không thuộc Data Space này.")
    if len(fits) > 1:
        raise Unbuildable("Bảng trong câu SQL có ở nhiều nguồn; hãy ghi rõ schema.")
    return _queryable(fits[0])


async def run_requested(event: Event) -> None:
    run = await _claim(event)
    if run is None:
        return  # already taken, finished or cancelled
    p = event.payload
    if run.as_of is not None and run.as_of < datetime.date.today():
        await _fail(run, HISTORY)
        return
    metric_version_ids: list[uuid.UUID] = []
    try:
        async with scoped_session(ROLE, run.org_id, run.principal_id) as session:
            if run.caller_kind == "question":
                source, sql, metric_version_ids = await _explore(session, run, p)
            elif p.get("mode") == "dataset":
                source, sql = await dataset_sql(session, run.data_space_id, p)
            else:
                source, sql = (
                    await sql_source(session, run.data_space_id, run.sql_generated),
                    run.sql_generated,
                )
    except (Unbuildable, c.CompileError, SqlRejected) as exc:
        await _fail(run, str(exc))
        return
    outcome = await run_query(
        org_id=run.org_id,
        data_space_id=run.data_space_id,
        principal_id=run.principal_id,
        caller_kind="ad_hoc",
        caller_id=run.caller_id,
        source=source,
        sql=sql,
        secrets=secrets(),
        metric_version_ids=metric_version_ids,
        run_id=run.id,
        limit=int(p.get("limit") or 10_000),
    )
    logger.info("query run %s: %s", run.id, outcome.status)


HANDLERS: dict[str, Handler] = {EVENT_TYPE: run_requested}
