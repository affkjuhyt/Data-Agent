"""Verified queries (R1-08, UC-07-02): a question a steward approved an SQL for is answered by that
SQL, not by SQL the model writes, and the answer is VERIFIED (invariant 14: Đã kiểm > Có quản trị
> Khám phá).

An approved example is context.example_query with status active. Its SQL may name the period it
reads with two placeholders, `:period_start` (inclusive) and `:period_end` (exclusive), so one
approved query answers "Doanh thu theo kênh tháng 9/2026" and "… tháng này" alike: a question
matches when it reads as the example's question once the period words are taken out of both, and
the period bound is the question's own, else the example's. An SQL without placeholders is fixed:
only the example's question, word for word, matches it. The design has no column for parameters,
so the placeholders are the whole of the binding.
"""

from __future__ import annotations

import datetime
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import catalog, context
from libs.sql_guard import SqlRejected, check_read_only, tables_of
from services.worker_agent.understand.text import FoundPeriod, find_period, norm

TRUST_VERIFIED = "Đã kiểm (truy vấn đã duyệt)"
_PLACEHOLDER = re.compile(r":(period_start|period_end)\b")


@dataclass(frozen=True)
class Verified:
    example_id: uuid.UUID
    question: str
    sql: str  # bound: the placeholders replaced by the period's dates
    period: FoundPeriod | None


def _key(text: str, today: datetime.date) -> str:
    """The question's words without its period, normalised."""
    t = norm(text)
    found = find_period(text, today)
    if found is not None:
        t = re.sub(rf"\b{re.escape(found.span)}\b", " ", t)
    return " ".join(t.split())


def has_placeholders(sql: str) -> bool:
    return bool(_PLACEHOLDER.search(sql))


def bind(sql: str, period: FoundPeriod | None, today: datetime.date) -> str:
    """The SQL with its period placeholders as date literals: the given period, else the month of
    `today` (what the checker runs an example with when its question names none)."""
    if period is None:
        start = today.replace(day=1)
        end = (start + datetime.timedelta(days=32)).replace(day=1)
    else:
        start, end = period.start, period.end
    dates = {"period_start": start.isoformat(), "period_end": end.isoformat()}
    return _PLACEHOLDER.sub(lambda m: f"'{dates[m.group(1)]}'", sql)


def match(
    question: str, examples: list[tuple[uuid.UUID, str, str]], today: datetime.date
) -> Verified | None:
    """The approved example the question is, with its SQL bound; None when none is."""
    asked, key = norm(question), _key(question, today)
    for example_id, text, sql in examples:
        if has_placeholders(sql):
            if _key(text, today) != key:
                continue
            period = find_period(question, today) or find_period(text, today)
            return Verified(example_id, text, bind(sql, period, today), period)
        if norm(text) == asked:
            return Verified(example_id, text, sql, find_period(text, today))
    return None


async def active_examples(
    session: AsyncSession, space_id: uuid.UUID
) -> list[tuple[uuid.UUID, str, str]]:
    rows = (
        await session.execute(
            select(context.ExampleQuery.id, context.ExampleQuery.question, context.ExampleQuery.sql)
            .where(
                context.ExampleQuery.data_space_id == space_id,
                context.ExampleQuery.status == "active",
            )
            .order_by(context.ExampleQuery.updated_at.desc(), context.ExampleQuery.id)
        )
    ).all()
    return [(i, str(q), str(s)) for i, q, s in rows]


@dataclass(frozen=True)
class Origin:
    """Where an approved SQL runs: one source (`source_id`), or the Data Space's workspace tables
    (None); `ok` False when its tables are of no dataset, or of several sources."""

    ok: bool
    source_id: uuid.UUID | None = None


async def origin_of(session: AsyncSession, space_id: uuid.UUID, sql: str) -> Origin:
    try:
        tables = tables_of(check_read_only(sql, "postgres"), "postgres")
    except SqlRejected:
        return Origin(False)
    if not tables:
        return Origin(False)
    datasets = (
        await session.scalars(
            select(catalog.Dataset).where(
                catalog.Dataset.data_space_id == space_id, catalog.Dataset.deleted_at.is_(None)
            )
        )
    ).all()
    sources: set[uuid.UUID | None] = set()
    for schema, name in tables:
        found = [
            d
            for d in datasets
            if d.table_name.lower() == name.lower()
            and (schema is None or d.schema_name.lower() == schema.lower())
        ]
        if not found:
            return Origin(False)
        sources |= {d.source_id for d in found}
    return Origin(True, sources.pop()) if len(sources) == 1 else Origin(False)
