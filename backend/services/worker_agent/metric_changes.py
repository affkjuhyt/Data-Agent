"""A metric version from preview to certified (UC-07-01 steps 3-4, R1-07), with what the change
reaches: the impact analysis the Holistics review asked for before a definition is published.

`metric_version.preview_requested` (POST /v1/metric-versions/{id}:preview): the dependents of the
metric are counted, read each with its own schema's role, and the ones the new version would break
are named. The figures A3 asks of :preview are computed: the new version and the certified one, by
month over the last six complete months (the event's `periods`), on the metric's own source, next
to the figure filed for each month. A new version that does not compile or run is a break
(AC-07-01-04). A metric_change proposal opens with all of it: `diff.preview`, `impact` and
`impact_level`, and the version waits in pending_review.

The month-by-month comparison is not an eval run: a month of a metric is no item. The Data
Space's standard set is run besides, with the new version in place (semantic_review.
evaluate_overlay), under the id the api's Location names: the proposal carries it and its
regressions as `diff.eval`.

`proposal.decided` for metric_change (proposals.decided routes it here): accepted, the breaks are
checked again, since the dependents may have changed while the proposal waited. With none, the
version is certified, the certified one before it superseded and the metric's current_version
moved to it; with some, nothing changes and the proposal ends apply_failed, named in the log.
Rejected or sent back for changes, the version is a draft again.

What breaks:
  * an active monitor cut (`scope_filter.split`) or filtered by a dimension the new version no
    longer allows: BR-81 would refuse every check it runs;
  * the unit changed under an active monitor: its threshold was written in the old unit.
Everything else is counted, not blocking: skills on the certified version are evaluated again
(BR-13), example queries keep the SQL they were verified with, filed figures keep the version they
were filed with, data products follow the metric's certified version. Tiles store an opaque spec
and forecast formula models are read by no role this worker holds, so neither is counted yet.
"""

from __future__ import annotations

import datetime
import decimal
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import String, cast, func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import (
    agent,
    catalog,
    context,
    governance,
    inbox,
    monitor,
    period,
    skill,
)
from libs.dataagent_db.models import org as org_models
from services.worker_agent import proposals, questions, semantic_review
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import run_query
from services.worker_agent.query.scope import can_ask_data_space
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import Overlay, load

logger = logging.getLogger(__name__)

PREVIEW = "metric_version.preview_requested"
KIND = "metric_change"
# What a definition change compares, field by field, in the proposal's diff.
_COMPARED = (
    "display_name",
    "expression",
    "filters",
    "unit",
    "decimals",
    "period_semantics",
    "time_grain",
    "additivity",
)


PREVIEW_PERIODS = 6  # A3: :preview runs on six periods
FILED = ("closed", "filed", "retro_detected")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _today() -> datetime.date:
    return datetime.date.today()


def _months_before(today: datetime.date, count: int) -> c.Period:
    """The last `count` complete months before the month of `today`."""
    end = today.replace(day=1)
    index = end.year * 12 + end.month - 1 - count
    return c.Period(datetime.date(index // 12, index % 12 + 1, 1), end)


def _month_labels(window: c.Period) -> list[str]:
    labels, day = [], window.start
    while day < window.end:
        labels.append(f"{day:%Y-%m}")
        day = (day.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return labels


def _compiled_version(row: context.MetricVersion, code: str) -> c.MetricVersion:
    """A version row as the compiler reads it (semantic/loader.py does the same for the one in
    force); the candidate is not in force, so the loader does not give it."""
    return c.MetricVersion(
        id=row.id,
        metric_code=code,
        display_name=row.display_name,
        expression=row.expression,
        filters=tuple(row.filters or ()),
        unit=row.unit,
        decimals=row.decimals,
        period_semantics=row.period_semantics,
        allowed_dimension_ids=frozenset(row.allowed_dimensions or ()),
        certified=row.status == "certified",
        time_grain=row.time_grain,
    )


def _number(value: Any) -> decimal.Decimal | None:
    return None if value is None else decimal.Decimal(str(value))


def _text(value: decimal.Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


@dataclass
class Impact:
    counts: dict[str, int] = field(default_factory=dict)
    breaks: list[dict[str, str]] = field(default_factory=list)

    @property
    def level(self) -> str:
        if self.breaks:
            return "high"
        return "medium" if any(self.counts.values()) else "low"

    def as_json(self) -> dict[str, Any]:
        return {"counts": self.counts, "breaks": self.breaks}


@dataclass(frozen=True)
class _Versions:
    metric: context.Metric
    new: context.MetricVersion
    certified: context.MetricVersion | None
    # Dimension codes by id, for the allowed dimensions of both versions.
    codes: dict[uuid.UUID, str]


async def _versions(org: uuid.UUID, actor: uuid.UUID, version_id: uuid.UUID) -> _Versions | None:
    async with scoped_session("svc_context", org, actor) as session:
        new = await session.get(context.MetricVersion, version_id)
        if new is None:
            return None
        metric = await session.get(context.Metric, new.metric_id)
        if metric is None:
            return None
        certified = await session.scalar(
            select(context.MetricVersion).where(
                context.MetricVersion.metric_id == metric.id,
                context.MetricVersion.status == "certified",
                context.MetricVersion.id != new.id,
            )
        )
        ids = set(new.allowed_dimensions or []) | set(
            (certified.allowed_dimensions or []) if certified else []
        )
        codes: dict[uuid.UUID, str] = {}
        if ids:
            found = await session.execute(
                select(context.SemanticDimension.id, context.SemanticDimension.code).where(
                    context.SemanticDimension.id.in_(ids)
                )
            )
            codes = dict(found.tuples().all())
        return _Versions(metric, new, certified, codes)


def changes(v: _Versions) -> dict[str, Any]:
    """The definition fields the new version changes against the certified one, and the
    dimensions it adds or removes."""
    before = v.certified
    out: dict[str, Any] = {}
    for name in _COMPARED:
        old = getattr(before, name) if before is not None else None
        new = getattr(v.new, name)
        if before is None or old != new:
            out[name] = {"before": old, "after": new}
    allowed_before = (
        {v.codes.get(d, str(d)) for d in (before.allowed_dimensions or [])} if before else set()
    )
    allowed_after = {v.codes.get(d, str(d)) for d in (v.new.allowed_dimensions or [])}
    if allowed_before - allowed_after:
        out["dimensions_removed"] = sorted(allowed_before - allowed_after)
    if allowed_after - allowed_before:
        out["dimensions_added"] = sorted(allowed_after - allowed_before)
    return out


def _monitor_dimensions(scope_filter: Any) -> set[str]:
    """The dimension codes a monitor cuts or filters by: `split`, and the keys of `filters`."""
    if not isinstance(scope_filter, dict):
        return set()
    out = set()
    split = scope_filter.get("split")
    if isinstance(split, str) and split and split != "none":
        out.add(split)
    filters = scope_filter.get("filters")
    if isinstance(filters, dict):
        out |= {str(k) for k in filters}
    return out


async def impact_of(org: uuid.UUID, actor: uuid.UUID, v: _Versions) -> Impact:
    impact = Impact()
    metric, certified = v.metric, v.certified
    allowed = {v.codes.get(d, str(d)) for d in (v.new.allowed_dimensions or [])}
    unit_changed = certified is not None and certified.unit != v.new.unit

    async with scoped_session("svc_monitor", org, actor) as session:
        monitors = (
            await session.scalars(
                select(monitor.Monitor).where(
                    monitor.Monitor.metric_id == metric.id,
                    monitor.Monitor.status == "active",
                    monitor.Monitor.deleted_at.is_(None),
                )
            )
        ).all()
    impact.counts["monitors"] = len(monitors)
    for m in monitors:
        lost = sorted(_monitor_dimensions(m.scope_filter) - allowed)
        if lost:
            impact.breaks.append(
                {
                    "kind": "monitor",
                    "id": str(m.id),
                    "text": f"Theo dõi chia hoặc lọc theo {', '.join(lost)}, bản mới không cho"
                    " phép chiều này.",
                }
            )
        if unit_changed:
            assert certified is not None
            impact.breaks.append(
                {
                    "kind": "monitor",
                    "id": str(m.id),
                    "text": f"Ngưỡng của theo dõi viết theo đơn vị {certified.unit or 'không có'},"
                    f" bản mới đổi sang {v.new.unit or 'không có'}.",
                }
            )

    if certified is not None:
        async with scoped_session("svc_skill", org, actor) as session:
            impact.counts["skills"] = int(
                await session.scalar(
                    select(func.count(func.distinct(skill.SkillVersion.skill_id))).where(
                        skill.SkillVersion.status.in_(("published", "needs_review")),
                        literal(certified.id) == func.any(skill.SkillVersion.metric_version_ids),
                    )
                )
                or 0
            )
        async with scoped_session("svc_qa", org, actor) as session:
            impact.counts["filed_periods"] = int(
                await session.scalar(
                    select(func.count(func.distinct(period.PublishedResult.period_close_id))).where(
                        period.PublishedResult.metric_version_id == certified.id
                    )
                )
                or 0
            )

    needle = f"%{metric.code}%"
    async with scoped_session("svc_agent", org, actor) as session:
        impact.counts["agents"] = int(
            await session.scalar(
                select(func.count(func.distinct(agent.AgentDef.id)))
                .join(
                    agent.AgentConfig,
                    (agent.AgentConfig.agent_id == agent.AgentDef.id)
                    & (agent.AgentConfig.version == agent.AgentDef.current_config_version),
                )
                .where(
                    agent.AgentDef.data_space_id == metric.data_space_id,
                    agent.AgentDef.status != "retired",
                    cast(agent.AgentConfig.data_scope, String).ilike(needle)
                    | cast(agent.AgentConfig.data_scope, String).ilike(f"%{metric.id}%"),
                )
            )
            or 0
        )

    async with scoped_session("svc_context", org, actor) as session:
        impact.counts["data_products"] = int(
            await session.scalar(
                select(func.count(func.distinct(context.DataProduct.id)))
                .join(
                    context.DataProductMember,
                    context.DataProductMember.data_product_id == context.DataProduct.id,
                )
                .where(
                    context.DataProductMember.member_kind == "metric",
                    context.DataProductMember.member_ref == metric.id,
                    context.DataProduct.status == "active",
                )
            )
            or 0
        )
        impact.counts["example_queries"] = int(
            await session.scalar(
                select(func.count()).where(
                    context.ExampleQuery.status == "active",
                    literal(metric.id) == func.any(context.ExampleQuery.metric_ids),
                )
            )
            or 0
        )
        impact.counts["knowledge_pages"] = int(
            await session.scalar(
                select(func.count(func.distinct(context.KnowledgePage.id)))
                .join(
                    context.KnowledgeFigure,
                    context.KnowledgeFigure.page_version_id
                    == context.KnowledgePage.current_version_id,
                )
                .where(
                    context.KnowledgePage.data_space_id == metric.data_space_id,
                    context.KnowledgeFigure.metric_code == metric.code,
                )
            )
            or 0
        )
    return impact


async def _by_month(
    event: Event,
    actor: uuid.UUID,
    version: c.MetricVersion,
    window: c.Period,
    space_id: uuid.UUID,
) -> tuple[dict[str, decimal.Decimal | None], str | None, str | None]:
    """The version's value for each month of the window, run in place on its source; or the break
    that keeps it from running; or, third, why its figures are withheld from this steward.

    The figures are held to the question path's rules (the security review of :preview): the
    steward must be allowed to ask in the Data Space, and a definition that reads a table under any
    access policy is not run: an aggregate over a masked column still discloses it
    (semantic/loader.py blocks such a metric from questions the same way), and row policies and
    row-scoped members make the figures depend on who reads them, while the proposal they go into
    is read by every reviewer, so figures computed under the steward's rows would show a reviewer
    rows outside theirs."""
    async with scoped_session("svc_qa", event.org_id, actor) as session:
        if not await can_ask_data_space(session, data_space_id=space_id, principal_id=actor):
            return {}, None, "Người chạy thử không có quyền hỏi dữ liệu của Data Space này."
        space = await load(session, space_id)
        try:
            time_dim = c.time_dimension_of(version, space.layer)
            if time_dim is None:
                return (
                    {},
                    f"{version.display_name} không có chiều thời gian để so theo tháng.",
                    None,
                )
            request = c.Request(version, group_by=[time_dim.code], period=window, grain="month")
            compiled = c.compile_metric(request, space.layer, dialect="postgres")
        except c.CompileError as exc:
            return {}, f"Không biên dịch được {version.display_name}: {exc}", None
        covered = await session.scalar(
            select(func.count()).where(
                governance.AccessPolicy.dataset_id.in_(list(compiled.dataset_ids)),
                governance.AccessPolicy.status == "active",
            )
        )
        scoped = await session.scalar(
            select(func.count()).where(
                org_models.DataSpaceMember.data_space_id == space_id,
                org_models.DataSpaceMember.row_scope.is_not(None),
            )
        )
        if covered or scoped:
            return (
                {},
                None,
                "Bảng của chỉ số có chính sách truy cập hoặc Data Space có phạm vi dòng theo người;"
                " số chạy thử không được hiện.",
            )
        dataset = space.datasets.get(compiled.dataset_ids[0])
        if dataset is None:
            return {}, f"Bảng của {version.display_name} không có trong danh mục.", None
        source = (
            questions._internal_workspace_source(space_id, event.org_id)
            if dataset.schema_name == "workspace" and dataset.source_id is None
            else await session.get(catalog.Source, dataset.source_id)
        )
        if source is None or source.mode != "federated" or source.status not in questions.QUERYABLE:
            return {}, "Bảng của chỉ số nằm trên một nguồn chưa được phép truy vấn tại chỗ.", None
        try:
            compiled = c.compile_metric(
                request, space.layer, dialect=questions.dialect_for_source(source.kind)
            )
        except c.CompileError as exc:
            return {}, f"Không biên dịch được {version.display_name}: {exc}", None
    outcome = await run_query(
        org_id=event.org_id,
        data_space_id=space_id,
        principal_id=actor,
        caller_kind="eval",
        caller_id=version.id,
        source=source,
        sql=compiled.sql,
        secrets=questions.secrets(),
        metric_version_ids=[version.id],
    )
    if not outcome.ok:
        return {}, f"Chạy thử {version.display_name} không thành công ({outcome.status}).", None
    return {str(row[0]): _number(row[-1]) for row in outcome.rows}, None, None


async def _filed(
    org: uuid.UUID, actor: uuid.UUID, version_id: uuid.UUID
) -> dict[str, decimal.Decimal]:
    """The whole-metric figure filed for each closed month with the certified version; the newest
    close of a month wins (a retro change files it again as a new version)."""
    async with scoped_session("svc_qa", org, actor) as session:
        rows = (
            await session.execute(
                select(period.PeriodClose.period_label, period.PublishedResult.value)
                .join(
                    period.PeriodClose,
                    period.PeriodClose.id == period.PublishedResult.period_close_id,
                )
                .where(
                    period.PublishedResult.metric_version_id == version_id,
                    period.PublishedResult.dimension_key == literal({}, type_=JSONB),
                    period.PeriodClose.status.in_(FILED),
                )
                .order_by(period.PeriodClose.version)
            )
        ).tuples()
        return dict(rows.all())


async def preview_figures(
    event: Event, actor: uuid.UUID, v: _Versions, periods: int, impact: Impact
) -> tuple[list[dict[str, str | None]], str | None]:
    """The new version next to the certified one and to the filed figure, month by month, and why
    figures are withheld if they are; what keeps the new one from running goes into `impact` as a
    break."""
    window = _months_before(_today(), periods)
    space_id = v.metric.data_space_id
    new, problem, withheld = await _by_month(
        event, actor, _compiled_version(v.new, v.metric.code), window, space_id
    )
    if problem is not None:
        impact.breaks.append({"kind": "definition", "id": str(v.new.id), "text": problem})
    current: dict[str, decimal.Decimal | None] = {}
    filed: dict[str, decimal.Decimal] = {}
    if v.certified is not None and withheld is None:
        current, _, withheld = await _by_month(
            event, actor, _compiled_version(v.certified, v.metric.code), window, space_id
        )
        if withheld is None:
            filed = await _filed(event.org_id, actor, v.certified.id)
        else:
            new = {}
    rows: list[dict[str, str | None]] = []
    mismatches = 0
    for label in _month_labels(window):
        n, cur, f = new.get(label), current.get(label), filed.get(label)
        if n is not None and f is not None and n != f:
            mismatches += 1
        rows.append(
            {
                "period": label,
                "new": _text(n),
                "current": _text(cur),
                "filed": _text(f),
                "change_vs_current": _text(n - cur) if n is not None and cur is not None else None,
                "change_vs_filed": _text(n - f) if n is not None and f is not None else None,
            }
        )
    impact.counts["filed_mismatches"] = mismatches
    return rows, withheld


async def _author(org: uuid.UUID, actor: uuid.UUID, version_id: uuid.UUID) -> uuid.UUID | None:
    """Who wrote the version: metric_version has no author column, so it is read from the event
    the api wrote when it created it (created_by); None for a version created before it did."""
    async with scoped_session("svc_context", org, actor) as session:
        payload = await session.scalar(
            select(context.Outbox.payload)
            .where(
                context.Outbox.event_type.in_(("metric.created", "metric_version.created")),
                context.Outbox.payload.op("->>")("version_id") == str(version_id),
            )
            .limit(1)
        )
    created_by = (payload or {}).get("created_by")
    return uuid.UUID(str(created_by)) if created_by else None


async def preview_requested(event: Event) -> None:
    p = event.payload
    version_id = uuid.UUID(str(p["version_id"]))
    actor = uuid.UUID(str(p["requested_by"]))
    v = await _versions(event.org_id, actor, version_id)
    if v is None or v.new.status != "testing":
        return
    impact = await impact_of(event.org_id, actor, v)
    figures, withheld = await preview_figures(
        event, actor, v, int(p.get("periods") or PREVIEW_PERIODS), impact
    )
    # The Data Space's standard set asked with the new version in place: the eval run the api's
    # Location names, and the items it would turn from passing to failing.
    eval_run_id, evaluated = await semantic_review.evaluate_overlay(
        event,
        v.metric.data_space_id,
        actor,
        Overlay(metric_version_ids=frozenset({version_id})),
        f"metric_version:{version_id}",
        version_id,
        run_id=uuid.UUID(str(p["eval_run_id"])) if p.get("eval_run_id") else None,
    )
    # Maker-checker: the proposer is the version's author, so its author cannot decide it even
    # when someone else asked for the preview; without a recorded author, whoever asked.
    author = await _author(event.org_id, actor, version_id)
    diff = {
        "action": "certify",
        "metric_id": v.metric.id,
        "metric_code": v.metric.code,
        "version_id": v.new.id,
        "version": v.new.version,
        "replaces_version": v.certified.version if v.certified else None,
        "changes": changes(v),
        "preview": figures,
        "preview_withheld": withheld,
        "eval": evaluated,
    }
    pid = await proposals._open(
        event,
        data_space_id=v.metric.data_space_id,
        kind=KIND,
        target_service="context",
        target_ref=v.new.id,
        title=f"Duyệt định nghĩa {v.metric.code} phiên bản {v.new.version}",
        diff=diff,
        proposed_by=author or actor,
        eval_run_id=eval_run_id,
        impact=impact.as_json(),
        impact_level=impact.level,
    )
    async with scoped_session("svc_context", event.org_id, actor) as session:
        await session.execute(
            update(context.MetricVersion)
            .where(
                context.MetricVersion.id == version_id, context.MetricVersion.status == "testing"
            )
            .values(status="pending_review", proposal_id=pid)
        )


async def apply(event: Event, proposal: dict[str, Any]) -> bool:
    """The decision on a metric_change proposal; True when it took effect."""
    reviewer = uuid.UUID(str(event.payload["reviewer_id"]))
    version_id = uuid.UUID(str(proposal["target_ref"]))
    if event.payload.get("status") != "accepted":
        async with scoped_session("svc_context", event.org_id, reviewer) as session:
            result = await session.execute(
                update(context.MetricVersion)
                .where(
                    context.MetricVersion.id == version_id,
                    context.MetricVersion.status == "pending_review",
                )
                .values(status="draft", proposal_id=None)
            )
        return result.rowcount == 1
    v = await _versions(event.org_id, reviewer, version_id)
    if v is None or v.new.status != "pending_review":
        return False
    impact = await impact_of(event.org_id, reviewer, v)
    # A version that did not compile or run at preview stays broken: the dependents are counted
    # again, the definition is not re-run, so its break is read back from the proposal.
    async with scoped_session("svc_inbox", event.org_id, reviewer) as session:
        row = await session.get(inbox.Proposal, proposal["id"])
        stored = row.impact if row is not None else None
        requested = (
            row.evidence[0].get("requested")
            if row is not None and isinstance(row.evidence, list) and row.evidence
            else None
        )
        edited = row is None or row.diff != requested
    # What is certified is the version as proposed: an accept_with_edit changed the diff, not the
    # version, so an edited definition is a new version to preview, never certified as this one.
    if edited:
        logger.info("worker-agent | metric_change %s not applied: edited", proposal["id"])
        return False
    # Fail closed: without a recorded author the reviewer could be it, so the version is not
    # certified; one created before the api recorded authors is saved again as a new version.
    author = await _author(event.org_id, reviewer, version_id)
    if author is None or reviewer == author:
        logger.info(
            "worker-agent | metric_change %s not applied: %s",
            proposal["id"],
            "no recorded author" if author is None else "its author",
        )
        return False
    impact.breaks += [
        b
        for b in (stored or {}).get("breaks", [])
        if isinstance(b, dict) and b.get("kind") == "definition"
    ]
    if impact.breaks:
        logger.info(
            "worker-agent | metric_change %s not applied: %s",
            proposal["id"],
            "; ".join(b["text"] for b in impact.breaks),
        )
        return False
    stamp = _now()
    async with scoped_session("svc_context", event.org_id, reviewer) as session:
        metric = await session.get(context.Metric, v.metric.id, with_for_update=True)
        new = await session.get(context.MetricVersion, version_id, with_for_update=True)
        if metric is None or new is None or new.status != "pending_review":
            return False
        # One certified version a metric (partial unique index): the old one leaves first.
        await session.execute(
            update(context.MetricVersion)
            .where(
                context.MetricVersion.metric_id == metric.id,
                context.MetricVersion.status == "certified",
            )
            .values(status="superseded", valid_to=stamp)
        )
        await session.flush()
        new.status, new.certified_by, new.certified_at, new.valid_from = (
            "certified",
            reviewer,
            stamp,
            stamp,
        )
        metric.current_version, metric.status = new.version, "active"
        metric.updated_at, metric.updated_by = stamp, reviewer
        session.add(
            context.Outbox(
                id=uuid7(),
                org_id=event.org_id,
                aggregate_type="metric_version",
                aggregate_id=new.id,
                event_type="metric_version.certified",
                payload={
                    "metric_id": str(metric.id),
                    "version_id": str(new.id),
                    "version": new.version,
                    "data_space_id": str(metric.data_space_id),
                    "certified_by": str(reviewer),
                    "proposal_id": str(proposal["id"]),
                },
            )
        )
    return True


HANDLERS: dict[str, Handler] = {PREVIEW: preview_requested}
