"""Gathering a dataset's audit: the aggregates audit/findings.py reads, by read-only queries in place.

Every query goes through query/executor.run_query (one read-only statement, row cap, recorded as
a query_run of the agent run), built with sqlglot in the source's dialect with quoted names, so
no catalog text is spliced into SQL. A snapshot table (history_kind snapshot with its business
date) is audited on its latest snapshot: the same loan six times is one loan.

The queries, in order: one row of counts per column; the values of each category; for each column
with gaps, its filled rows by each category (what explains the gap); the cross of each small
category (CROSS_FROM values at most) with the others; duplicate keys; the histogram of each wide
numeric column.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp

from libs.dataagent_db.models import catalog
from services.worker_agent.audit.findings import CATEGORICAL_MAX, Aggregates, Column, is_category
from services.worker_agent.query.executor import QueryOutcome, run_query
from services.worker_agent.query.secrets import SecretReader
from services.worker_agent.questions import QUERYABLE, _internal_workspace_source

# A category crossed against the others has at most this many values (gender, a segment, a region).
CROSS_FROM = 3
HISTOGRAM_BINS = 10


class AuditFailed(Exception):
    """A query of the audit did not run; the message is what the owner reads."""


@dataclass
class Target:
    """What is audited and how to reach it."""

    dataset: catalog.Dataset
    columns: list[Column]
    source: catalog.Source
    date_column: str | None = None  # the snapshot's business date


@dataclass
class Gathered:
    aggregates: Aggregates
    snapshot: datetime.date | None
    query_run_ids: list[str] = field(default_factory=list)


def _col(name: str, alias: str = "t") -> exp.Column:
    return exp.column(name, table=alias, quoted=True)


def _count(arg: exp.Expression | None = None, *, distinct: bool = False) -> exp.Expression:
    if arg is None:
        return exp.Count(this=exp.Star())
    return exp.Count(this=exp.Distinct(expressions=[arg]) if distinct else arg)


class Builder:
    """The audit's SQL over one table, in one dialect, limited to its latest snapshot when set."""

    def __init__(self, target: Target, dialect: str, snapshot: Any | None) -> None:
        self.table = exp.table_(
            target.dataset.table_name, db=target.dataset.schema_name, quoted=True, alias="t"
        )
        self.dialect = dialect
        self.where: exp.Expression | None = (
            exp.EQ(this=_col(target.date_column), expression=exp.Literal.string(str(snapshot)))
            if snapshot is not None and target.date_column
            else None
        )

    def _select(self, *cols: exp.Expression) -> exp.Select:
        q = exp.select(*cols).from_(self.table.copy())
        return q.where(self.where.copy()) if self.where is not None else q

    def latest(self, date_column: str) -> str:
        return (
            exp.select(exp.Max(this=_col(date_column)))
            .from_(self.table.copy())
            .sql(dialect=self.dialect)
        )

    def counts(self, columns: Sequence[Column]) -> str:
        cols: list[exp.Expression] = [_count()]
        for c in columns:
            cols += [_count(_col(c.name)), _count(_col(c.name), distinct=True)]
            if c.pii or c.kind == "boolean":
                cols += [exp.Null(), exp.Null()]
            else:
                cols += [exp.Min(this=_col(c.name)), exp.Max(this=_col(c.name))]
        return self._select(*cols).sql(dialect=self.dialect)

    def values(self, column: str) -> str:
        return (
            self._select(_col(column), _count())
            .group_by(_col(column))
            .limit(CATEGORICAL_MAX + 1)
            .sql(dialect=self.dialect)
        )

    def filled_by(self, column: str, group: str) -> str:
        return (
            self._select(_col(group), _count(), _count(_col(column)))
            .group_by(_col(group))
            .sql(dialect=self.dialect)
        )

    def crossed(self, a: str, b: str) -> str:
        q = self._select(_col(a), _col(b), _count()).where(
            exp.and_(
                exp.Not(this=exp.Is(this=_col(a), expression=exp.Null())),
                exp.Not(this=exp.Is(this=_col(b), expression=exp.Null())),
            )
        )
        return q.group_by(_col(a), _col(b)).sql(dialect=self.dialect)

    def duplicates(self, keys: Sequence[str]) -> str:
        inner = (
            self._select(*[_col(k) for k in keys])
            .group_by(*[_col(k) for k in keys])
            .having(exp.GT(this=_count(), expression=exp.Literal.number(1)))
        )
        return exp.select(_count()).from_(inner.subquery("d")).sql(dialect=self.dialect)

    def histogram(self, column: str, low: decimal.Decimal, high: decimal.Decimal) -> str:
        step = (high - low) / HISTOGRAM_BINS
        bucket = exp.Least(
            this=exp.Floor(
                this=exp.Div(
                    this=exp.paren(exp.Sub(this=_col(column), expression=exp.Literal.number(low))),
                    expression=exp.Literal.number(step),
                )
            ),
            expressions=[exp.Literal.number(HISTOGRAM_BINS - 1)],
        )
        return (
            self._select(exp.alias_(bucket, "b", quoted=True), _count())
            .where(exp.Not(this=exp.Is(this=_col(column), expression=exp.Null())))
            .group_by(exp.column("b", quoted=True))
            .order_by(exp.column("b", quoted=True))
            .sql(dialect=self.dialect)
        )


async def load_target(
    session: AsyncSession, dataset: catalog.Dataset, org_id: uuid.UUID, space_id: uuid.UUID
) -> Target:
    """A catalogued dataset with its columns and where to query it in place (a session that reads
    catalog, as svc_qa). The objects are detached, for use after the session."""
    metas = (
        await session.scalars(
            select(catalog.ColumnMeta)
            .where(
                catalog.ColumnMeta.dataset_id == dataset.id,
                catalog.ColumnMeta.removed_in_version.is_(None),
            )
            .order_by(catalog.ColumnMeta.ordinal)
        )
    ).all()
    name = dataset.display_name or dataset.table_name
    if not metas:
        raise AuditFailed(
            f"Bảng {name} chưa có cột trong danh mục; hãy quét schema của nguồn trước khi kiểm toán."
        )
    source = (
        _internal_workspace_source(space_id, org_id)
        if dataset.schema_name == "workspace" and dataset.source_id is None
        else await session.get(catalog.Source, dataset.source_id)
    )
    if source is None or source.mode != "federated" or source.status not in QUERYABLE:
        raise AuditFailed(f"Bảng {name} nằm trên một nguồn chưa được phép truy vấn tại chỗ.")
    session.expunge(dataset)
    # The workspace's source is built here, not read: it is not in the session.
    if source in session:
        session.expunge(source)
    columns = [
        Column(
            m.name, m.display_name or m.name, m.normalized_type, m.is_key, m.pii_label is not None
        )
        for m in metas
    ]
    date_column = (
        dataset.business_date_col
        if dataset.history_kind == "snapshot"
        and dataset.business_date_col in {c.name for c in columns}
        else None
    )
    return Target(dataset, columns, source, date_column)


def _int(v: Any) -> int:
    return int(v or 0)


def _number(v: Any) -> decimal.Decimal | None:
    try:
        return decimal.Decimal(str(v)) if v is not None else None
    except decimal.InvalidOperation:
        return None


async def gather(
    target: Target,
    *,
    dialect: str,
    org_id: uuid.UUID,
    space_id: uuid.UUID,
    principal: uuid.UUID,
    run_id: uuid.UUID,
    secrets: SecretReader,
) -> Gathered:
    ids: list[str] = []

    async def ask(sql: str) -> QueryOutcome:
        out = await run_query(
            org_id=org_id,
            data_space_id=space_id,
            principal_id=principal,
            caller_kind="agent_run",
            caller_id=run_id,
            source=target.source,
            sql=sql,
            secrets=secrets,
        )
        ids.append(str(out.query_run_id))
        if not out.ok:
            raise AuditFailed(
                f"Chưa truy vấn được {target.dataset.display_name or target.dataset.table_name} "
                "để kiểm toán; người phụ trách dữ liệu có thể xem lượt chạy trong lịch sử truy vấn."
            )
        return out

    snapshot = None
    if target.date_column:
        latest = await ask(Builder(target, dialect, None).latest(target.date_column))
        snapshot = latest.rows[0][0] if latest.rows else None
    b = Builder(target, dialect, snapshot)
    cols = target.columns

    row = (await ask(b.counts(cols))).rows[0]
    a = Aggregates(rows=_int(row[0]), non_null={}, distinct={})
    for i, c in enumerate(cols):
        filled, distinct, low, high = row[1 + 4 * i : 5 + 4 * i]
        a.non_null[c.name], a.distinct[c.name] = _int(filled), _int(distinct)
        if low is not None:
            a.minimum[c.name] = str(low)
            a.maximum[c.name] = str(high)

    categories = [c for c in cols if is_category(c, a)]
    for c in categories:
        a.values[c.name] = [(v, _int(n)) for v, n in (await ask(b.values(c.name))).rows]
        # The empty value is a value of its own: kept with its count.
    for c in cols:
        if a.non_null[c.name] < a.rows:
            for g in categories:
                if g.name != c.name:
                    a.filled_by[(c.name, g.name)] = [
                        (v, _int(n), _int(f))
                        for v, n, f in (await ask(b.filled_by(c.name, g.name))).rows
                    ]
    for x in categories:
        if 2 <= a.distinct[x.name] <= CROSS_FROM:
            for y in categories:
                if y.name != x.name:
                    a.crossed[(x.name, y.name)] = [
                        (u, v, _int(n)) for u, v, n in (await ask(b.crossed(x.name, y.name))).rows
                    ]
    keys = [c.name for c in cols if c.is_key and c.name != target.date_column]
    if keys:
        a.duplicate_keys = _int((await ask(b.duplicates(keys))).rows[0][0])
        a.key_columns = keys
    for c in cols:
        low, high = _number(a.minimum.get(c.name)), _number(a.maximum.get(c.name))
        wide = c.numeric and not c.is_key and c not in categories
        if wide and low is not None and high is not None and high > low:
            step = (high - low) / HISTOGRAM_BINS
            a.histograms[c.name] = [
                (float(low + step * int(k)), float(low + step * (int(k) + 1)), _int(n))
                for k, n in (await ask(b.histogram(c.name, low, high))).rows
            ]
    return Gathered(a, _date(snapshot), ids)


def _date(v: Any) -> datetime.date | None:
    """The snapshot's date: the executor returns dates as ISO text (executor._plain)."""
    if isinstance(v, datetime.date):
        return v
    try:
        return datetime.date.fromisoformat(str(v)[:10]) if v is not None else None
    except ValueError:
        return None
