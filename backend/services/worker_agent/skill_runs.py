"""Running a skill (UC-03-01): the `skill_run.requested` handler.

The api records the run `queued` and emits the event (modules/skill/routes.py); this moves it to
`running`, runs its steps, and ends it `succeeded` with a result or `failed` with why. AC-03-01-01
puts skill runs on Temporal (ADR-37, R3). Until then this outbox handler stands in, the way
runs.py does for agent runs: each step is written as it ends, and a redelivered event finds the
run past `queued` and leaves it alone.

First slice: the step kind `decompose` (skill category `decomposition`, "Phân rã biến động",
skills/decomposition.py) with the params {metric, dimension, period, base_period?}, periods as
months "YYYY-MM" or quarters "YYYY-Qn" (base_period defaults to the period before, same grain). Every number comes from a semantic
query run through the Query engine as caller_kind skill_run (AC-03-01-06): no connection of its
own and no sandbox. A step kind this worker does not run (python, external factors, …) fails the
run saying so, rather than producing something it did not compute.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import logging
import re
import time
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, notify, skill
from libs.sql_guard import SqlRejected
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import run_query
from services.worker_agent.questions import (
    QUERYABLE,
    _internal_workspace_source,
    dialect_for_source,
    secrets,
)
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import SpaceLayer, load
from services.worker_agent.skills.decomposition import (
    NO_VALUE,
    NotDecomposable,
    Shape,
    Values,
    decompose,
    result_of,
    shape_of,
)

logger = logging.getLogger(__name__)

EVENT_TYPE = "skill_run.requested"
ROLE = "svc_skill"
# The semantic layer and the catalogue are read as the Hỏi đáp block reads them.
LAYER_ROLE = "svc_qa"
NOTIFY_ROLE = "svc_notify"
# AC-03-01-02: a run longer than this is background work; its owner is told when it ends.
NOTIFY_AFTER = datetime.timedelta(seconds=30)
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_QUARTER = re.compile(r"^(\d{4})-Q([1-4])$")


class RunFailed(Exception):
    """Ends the run `failed` with this message, in Vietnamese, for the person who ran it."""


def _shift(day: datetime.date, months: int) -> datetime.date:
    index = day.year * 12 + day.month - 1 + months
    return datetime.date(index // 12, index % 12 + 1, 1)


@dataclasses.dataclass
class Period:
    """A month (`months` 1) or a quarter (3), from its first day."""

    start: datetime.date
    months: int = 1

    @property
    def end(self) -> datetime.date:
        return _shift(self.start, self.months)

    @property
    def label(self) -> str:
        if self.months == 3:
            return f"Q{(self.start.month - 1) // 3 + 1}/{self.start.year}"
        return f"{self.start.month:02d}/{self.start.year}"

    def before(self) -> Period:
        return Period(_shift(self.start, -self.months), self.months)


def period_of(raw: object, name: str) -> Period:
    text = str(raw or "")
    month = _MONTH.match(text)
    if month and 1 <= int(month.group(2)) <= 12:
        return Period(datetime.date(int(month.group(1)), int(month.group(2)), 1))
    quarter = _QUARTER.match(text)
    if quarter:
        first = (int(quarter.group(2)) - 1) * 3 + 1
        return Period(datetime.date(int(quarter.group(1)), first, 1), 3)
    raise RunFailed(f"Tham số {name} phải là tháng YYYY-MM hoặc quý YYYY-Qn, nhận được “{raw}”.")


async def run_skill(event: Event) -> None:
    run_id = uuid.UUID(str(event.payload["skill_run_id"]))
    async with scoped_session(ROLE, event.org_id) as session:
        run = await session.get(skill.SkillRun, run_id, with_for_update=True)
        if run is None or run.status != "queued":
            return
        version = await session.get(skill.SkillVersion, run.skill_version_id)
        row = await session.get(skill.Skill, version.skill_id) if version else None
        if version is None or row is None:
            run.status, run.error = "failed", "Không còn phiên bản kỹ năng này."
            run.finished_at = datetime.datetime.now(datetime.UTC)
            return
        run.status = "running"
        run.started_at = datetime.datetime.now(datetime.UTC)
        steps = list(version.steps or [])
        category = row.category
        about = _About(row.id, row.name, run.started_at)
        principal, space, params = run.principal_id, run.data_space_id, dict(run.params or {})
    try:
        kinds = [str(s.get("kind")) for s in steps if isinstance(s, dict)]
        if category != "decomposition" or kinds != ["decompose"]:
            raise RunFailed(
                "Kỹ năng có bước mà worker chưa chạy được ("
                + ", ".join(kinds or ["không có bước"])
                + "); hiện chỉ chạy được bước decompose."
            )
        result = await _decompose(event.org_id, run_id, principal, space, params)
    except RunFailed as exc:
        await _finish(event.org_id, run_id, about, status="failed", error=str(exc))
        return
    except Exception:
        # The run is past `queued`, so a redelivery would skip it: end it here, never leave it
        # `running` with nobody working on it.
        logger.exception("skill run %s failed", run_id)
        await _finish(event.org_id, run_id, about, status="failed", error="Lỗi khi chạy kỹ năng.")
        return
    await _finish(event.org_id, run_id, about, status="succeeded", result=result)


@dataclasses.dataclass(frozen=True)
class _About:
    skill_id: uuid.UUID
    name: str
    started: datetime.datetime


async def _finish(
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    about: _About,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    async with scoped_session(ROLE, org_id) as session:
        run = await session.get(skill.SkillRun, run_id, with_for_update=True)
        # Cancelled meanwhile: the api's status stands.
        if run is None or run.status != "running":
            return
        run.status, run.result, run.error = status, result, error
        run.finished_at = datetime.datetime.now(datetime.UTC)
        principal, space = run.principal_id, run.data_space_id
    logger.info("skill run %s: %s", run_id, status if error is None else f"{status}: {error}")
    if run.finished_at - about.started < NOTIFY_AFTER:
        return
    # The person has left the screen by now: tell them in the app, without the figures (the
    # notification body holds no data, notify.notification.body).
    async with scoped_session(NOTIFY_ROLE, org_id, principal) as session:
        session.add(
            notify.Notification(
                id=uuid7(),
                org_id=org_id,
                user_id=principal,
                data_space_id=space,
                kind="task_done",
                title=f"{about.name}: "
                + ("đã chạy xong" if status == "succeeded" else "chạy không thành công"),
                body=None if status == "succeeded" else error,
                link=f"/skill_run/{about.skill_id}",
                source_ref=run_id,
            )
        )


async def _step(
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    seq: int,
    name: str,
    *,
    status: str,
    started: float,
    query_run_id: uuid.UUID | None = None,
    output: dict[str, Any] | None = None,
) -> None:
    async with scoped_session(ROLE, org_id) as session:
        # A redelivery replays the steps already written; the first one stands.
        exists = await session.scalar(
            select(skill.SkillRunStep.id).where(
                skill.SkillRunStep.skill_run_id == run_id, skill.SkillRunStep.seq == seq
            )
        )
        if exists is None:
            session.add(
                skill.SkillRunStep(
                    id=uuid7(),
                    org_id=org_id,
                    skill_run_id=run_id,
                    seq=seq,
                    name=name,
                    query_run_id=query_run_id,
                    output=output,
                    status=status,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            )


async def _decompose(
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    principal: uuid.UUID,
    space: uuid.UUID,
    params: dict[str, Any],
) -> dict[str, Any]:
    current = period_of(params.get("period"), "period")
    base = period_of(params["base_period"], "base_period") if params.get("base_period") else None
    base = base or current.before()
    if base.months != current.months:
        raise RunFailed("Kỳ so sánh và kỳ phân tích phải cùng là tháng hoặc cùng là quý.")
    if base.start >= current.start:
        raise RunFailed("Kỳ so sánh phải trước kỳ phân tích.")
    async with scoped_session(LAYER_ROLE, org_id, principal) as session:
        layer = await load(session, space)
        version, metric_name, shape, dimensions = _plan(layer, params)
        source = await _source(session, layer, version, dimensions[0], space=space, org_id=org_id)
    measures = [shape.numerator] + ([shape.denominator] if shape.denominator else [])
    seq = 0
    skipped: list[str] = []
    # AC-03-01-04: the first dimension that has data in either period is decomposed; one with
    # none (no rows, or only empty members) is skipped and the result says so.
    for dimension in dimensions:
        values: dict[str, Values] = {}
        for period in (base, current):
            found: dict[str, list[decimal.Decimal | None]] = {}
            for i, measure in enumerate(measures):
                seq += 1
                rows = await _query(
                    org_id,
                    run_id,
                    seq,
                    principal,
                    space,
                    layer,
                    source,
                    dataclasses.replace(version, expression=measure),
                    dimension,
                    period,
                    name=f"Truy vấn {measure} theo {dimension.name} kỳ {period.label}",
                )
                for member, value in rows:
                    found.setdefault(member, [None, None])[i] = value
            values[period.label] = {
                m: (num or decimal.Decimal(0), den) for m, (num, den) in found.items()
            }
        members = set(values[base.label]) | set(values[current.label])
        if members <= {NO_VALUE}:
            skipped.append(f"chiều {dimension.name}")
            continue
        seq += 1
        started = time.monotonic()
        skipped += [f"kỳ {p.label}" for p in (base, current) if not values[p.label]]
        try:
            decomposed = decompose(shape, values[base.label], values[current.label])
        except NotDecomposable as exc:
            await _step(org_id, run_id, seq, "Phân rã", status="error", started=started)
            raise RunFailed(f"Không phân rã được: {exc}.") from exc
        result = result_of(
            decomposed,
            shape,
            metric_name=metric_name,
            dimension_name=dimension.name,
            unit=version.unit,
            decimals=version.decimals,
            base_label=base.label,
            current_label=current.label,
            skipped=skipped,
        )
        result["dimension"] = dimension.code
        await _step(
            org_id,
            run_id,
            seq,
            f"Phân rã theo {dimension.name}",
            status="ok",
            started=started,
            output={"reconciled": result["reconciled"], "members": len(decomposed.contributions)},
        )
        return result
    raise RunFailed(
        f"Không có dữ liệu {metric_name} theo {', '.join(d.name for d in dimensions)} trong hai kỳ."
    )


async def _query(
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    seq: int,
    principal: uuid.UUID,
    space: uuid.UUID,
    layer: SpaceLayer,
    source: catalog.Source,
    version: c.MetricVersion,
    dimension: c.Dimension,
    period: Period,
    *,
    name: str,
) -> list[tuple[str, decimal.Decimal | None]]:
    """One measure cut by one dimension over one period, through the Query engine, recorded as
    the run's step `seq`: (member, value) per row."""
    started = time.monotonic()
    request = c.Request(
        version, group_by=[dimension.code], period=c.Period(period.start, period.end)
    )
    try:
        compiled = c.compile_metric(request, layer.layer, dialect=dialect_for_source(source.kind))
    except (c.CompileError, SqlRejected) as exc:
        await _step(org_id, run_id, seq, name, status="error", started=started)
        raise RunFailed(str(exc)) from exc
    outcome = await run_query(
        org_id=org_id,
        data_space_id=space,
        principal_id=principal,
        caller_kind="skill_run",
        caller_id=run_id,
        source=source,
        sql=compiled.sql,
        secrets=secrets(),
        metric_version_ids=[version.id],
    )
    await _step(
        org_id,
        run_id,
        seq,
        name,
        status="ok" if outcome.ok else "error",
        started=started,
        query_run_id=outcome.query_run_id,
        output={"rows": len(outcome.rows), "truncated": outcome.truncated},
    )
    if not outcome.ok:
        raise RunFailed(f"Truy vấn kỳ {period.label} không chạy được: {outcome.error}")
    if outcome.truncated:
        raise RunFailed(f"{dimension.name} có quá nhiều giá trị để phân rã (hơn 10.000 dòng).")
    cols = [col["name"] for col in outcome.columns]
    key, val = cols.index(dimension.code), cols.index("value")
    return [
        (
            NO_VALUE if r[key] is None else str(r[key]),
            None if r[val] is None else decimal.Decimal(str(r[val])),
        )
        for r in outcome.rows
    ]


def _plan(
    layer: SpaceLayer, params: dict[str, Any]
) -> tuple[c.MetricVersion, str, Shape, list[c.Dimension]]:
    code = str(params.get("metric") or "")
    entry = next((m for m in layer.vocabulary.metrics if m.code == code), None)
    if entry is None or entry.metric_id not in layer.versions:
        raise RunFailed(f"Data Space không có chỉ số “{code}” đang hiệu lực.")
    version = layer.versions[entry.metric_id]
    # One dimension, or several in order of preference ("san_pham,vung" or a list).
    asked = params.get("dimension")
    codes = (
        [str(a) for a in asked]
        if isinstance(asked, list)
        else [a.strip() for a in str(asked or "").split(",") if a.strip()]
    )
    dimensions: list[c.Dimension] = []
    for name in codes or [""]:
        dimension = layer.layer.dimensions.get(name)
        if dimension is None or dimension.id not in version.allowed_dimension_ids:
            raise RunFailed(f"{entry.name} không được phép chia theo “{name}”.")
        if dimension.is_time:
            raise RunFailed("Phân rã theo một chiều không phải thời gian; kỳ đã là tham số riêng.")
        dimensions.append(dimension)
    try:
        shape = shape_of(
            version.expression, {k: m.expression for k, m in layer.layer.measures.items()}
        )
    except NotDecomposable as exc:
        raise RunFailed(
            f"{entry.name} không phải tổng hay tỷ lệ của hai tổng nên không phân rã chính xác được."
        ) from exc
    return version, entry.name, shape, dimensions


async def _source(
    session: AsyncSession,
    layer: SpaceLayer,
    version: c.MetricVersion,
    dimension: c.Dimension,
    *,
    space: uuid.UUID,
    org_id: uuid.UUID,
) -> catalog.Source:
    try:
        probe = c.compile_metric(
            c.Request(version, group_by=[dimension.code]), layer.layer, dialect="postgres"
        )
    except (c.CompileError, SqlRejected) as exc:
        raise RunFailed(str(exc)) from exc
    dataset = layer.datasets[probe.dataset_ids[0]]
    # A table materialised in the Data Space's workspace schema is queried in place on the
    # platform database, as questions.py does (the credit demo's loan snapshot is one).
    if dataset.schema_name == "workspace" and dataset.source_id is None:
        return _internal_workspace_source(space, org_id)
    source = await session.get(catalog.Source, dataset.source_id) if dataset.source_id else None
    if source is None or source.mode != "federated" or source.status not in QUERYABLE:
        raise RunFailed("Bảng của chỉ số này nằm trên một nguồn chưa được phép truy vấn tại chỗ.")
    return source


HANDLERS: dict[str, Handler] = {EVENT_TYPE: run_skill}
