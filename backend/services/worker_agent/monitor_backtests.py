"""Backtesting a monitor's rule (UC-08-01 step 3, BR-32): the monitor agent's half of
POST /v1/monitors, a rule PATCH and POST /v1/monitors/{id}:backtest.

The api writes monitor.backtest = {"status": "pending", "periods": n, "requested_at": t} and the
event (monitor.created, monitor.updated or monitor.backtest_requested, all three in
monitor.outbox); this reads the metric's series once through the semantic compiler and the Query
engine, as the monitor's owner (their row scope holds, BR-07), cut by the monitor's split and
filtered by its filters, and judges the rule on the last n complete periods that hold a figure:

- threshold: the period fires when a member is past the value (answer/thresholds.py decides,
  the early-warning band does not fire);
- change_vs_previous: when a member moved more than `value` percent, the way `direction` says,
  from the period before it in the series (a period without a figure, or a zero, is not compared);
- seasonal_anomaly needs a seasonal model the worker does not have: the result says so
  ("unsupported") instead of a count it did not compute.

The result replaces the pending request only while it is still the one that was read (a PATCH
during the run asks again), so a redelivered event, or the created/updated event of a backtest
already done, changes nothing. {"periods", "fired"} is what the api's rules.noise_warning reads
(over 30% fired is BR-32's warning). The queries are recorded as caller_kind `eval` with the
monitor's id: query_run has no monitor caller, and a backtest is an evaluation of the rule.
"""

from __future__ import annotations

import datetime
import decimal
import itertools
import logging
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select, update

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog, monitor
from libs.sql_guard import SqlRejected
from services.worker_agent.answer import thresholds
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import run_query
from services.worker_agent.query.scope import can_ask_data_space
from services.worker_agent.questions import (
    QUERYABLE,
    _internal_workspace_source,
    dialect_for_source,
    secrets,
)
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import load

logger = logging.getLogger(__name__)

REQUESTED = "monitor.backtest_requested"
CREATED = "monitor.created"
UPDATED = "monitor.updated"
ROLE = thresholds.ROLE  # svc_monitor: the monitor agent's role
LAYER_ROLE = "svc_qa"
DEFAULT_PERIODS = 12  # UC-08-01 step 3
GRAINS = ("day", "week", "month", "quarter", "year")
WHOLE = ""  # the member of an unsplit series

Series = Mapping[str, Mapping[str, decimal.Decimal | None]]


class Unjudgeable(Exception):
    """The backtest cannot run; the message, in Vietnamese, is the result's reason."""


class Unsupported(Unjudgeable):
    """The rule kind is not backtested by this worker."""


def grain_of(rule_kind: str, rule: Any, time_grain: str | None) -> str:
    """The period ("kỳ") the rule is judged on: a change's base ("so với tuần trước"), else a
    month, or the metric's own grain when it is coarser (a quarterly figure has no months).
    time_grain is the finest grain a metric can be read at, 'day' by default, not its period. A
    fiscal grain labels periods 'FY…', which do not order against the current period's label, so
    it is judged by calendar month."""
    base = rule.get("base") if isinstance(rule, dict) else None
    if rule_kind == "change_vs_previous" and base in GRAINS:
        return str(base)
    return str(time_grain) if time_grain in ("quarter", "year") else "month"


def complete_before(today: datetime.date, grain: str) -> str:
    """The label of the first period not yet complete on `today`, as the compiler writes labels
    (semantic/compiler._at_grain): every label below it is a complete period."""
    if grain == "day":
        return today.isoformat()
    if grain == "week":
        # A week labelled by its first day is complete once seven days have passed from it.
        return (today - datetime.timedelta(days=6)).isoformat()
    if grain == "quarter":
        return f"{today.year}-Q{(today.month - 1) // 3 + 1}"
    if grain == "year":
        return f"{today:%Y}"
    return f"{today:%Y-%m}"


def _number(raw: Any) -> decimal.Decimal | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        value = decimal.Decimal(str(raw))
    except decimal.InvalidOperation:
        return None
    return value if value.is_finite() else None


def _fired_by_threshold(rule: Any, series: Series, labels: list[str]) -> dict[str, bool]:
    t = thresholds.threshold_of(rule)
    if t is None:
        raise Unjudgeable("Luật ngưỡng thiếu hướng (lớn hơn/nhỏ hơn) hoặc giá trị.")
    out: dict[str, bool] = {}
    for label in labels:
        values = [v for v in series[label].values() if v is not None]
        if values:
            out[label] = any(thresholds.state(v, t) == "breach" for v in values)
    return out


def _fired_by_change(rule: Any, series: Series, labels: list[str]) -> dict[str, bool]:
    rule = rule if isinstance(rule, dict) else {}
    direction, percent = rule.get("direction"), _number(rule.get("value"))
    if direction not in ("up", "down") or percent is None or percent < 0:
        raise Unjudgeable("Luật thay đổi thiếu hướng (tăng/giảm) hoặc phần trăm.")
    out: dict[str, bool] = {}
    for before, label in itertools.pairwise(labels):
        fired, compared = False, False
        for member, value in series[label].items():
            previous = series[before].get(member)
            if value is None or previous is None or previous == 0:
                continue
            compared = True
            change = (value - previous) / abs(previous) * 100
            fired |= change > percent if direction == "up" else -change > percent
        if compared:
            out[label] = fired
    return out


def judge(
    rule_kind: str, rule: Any, series: Series, *, periods: int, before: str
) -> dict[str, Any]:
    """{"periods", "fired", "fired_at"} of the rule over the last `periods` complete periods
    (labels below `before`) that could be judged."""
    labels = sorted(label for label in series if label < before)
    if rule_kind == "threshold":
        judged = _fired_by_threshold(rule, series, labels)
    elif rule_kind == "change_vs_previous":
        judged = _fired_by_change(rule, series, labels)
    else:
        raise Unsupported(
            "Chưa chạy thử được luật bất thường theo mùa vụ: cần mô hình mùa vụ của chỉ số."
        )
    last = sorted(judged)[-periods:] if periods > 0 else []
    fired_at = [label for label in last if judged[label]]
    return {"periods": len(last), "fired": len(fired_at), "fired_at": fired_at}


def _scope(scope_filter: Any) -> tuple[str | None, dict[str, list[str]]]:
    """The monitor's split dimension and its filters ({dimension code: values})."""
    if not isinstance(scope_filter, dict):
        return None, {}
    split = scope_filter.get("split")
    raw = scope_filter.get("filters")
    filters = (
        {
            str(code): [str(v) for v in (values if isinstance(values, list) else [values])]
            for code, values in raw.items()
        }
        if isinstance(raw, dict)
        else {}
    )
    return (split if isinstance(split, str) and split and split != "none" else None), filters


def _today() -> datetime.date:
    return datetime.date.today()


async def _series(row: monitor.Monitor) -> tuple[Series, str]:
    """The metric in force cut by period (and the monitor's split), read in place as the owner,
    with the grain of its periods."""
    owner, space = row.owner_id, row.data_space_id
    split, filters = _scope(row.scope_filter)
    async with scoped_session(LAYER_ROLE, row.org_id, owner) as session:
        if not await can_ask_data_space(session, data_space_id=space, principal_id=owner):
            raise Unjudgeable("Chủ theo dõi không có quyền hỏi dữ liệu của Data Space này.")
        layer = await load(session, space)
        version = layer.versions.get(row.metric_id)
        if version is None or row.metric_id in layer.blocked_metric_ids:
            raise Unjudgeable("Chỉ số của theo dõi không có phiên bản đang dùng được để hỏi.")
        time_dim = c.time_dimension_of(version, layer.layer)
        if time_dim is None:
            raise Unjudgeable(f"{version.display_name} không có chiều thời gian để chạy thử.")
        grain = grain_of(row.rule_kind, row.rule, version.time_grain)
        request = c.Request(
            version,
            group_by=[time_dim.code] + ([split] if split else []),
            filters=filters,
            grain=grain,
        )
        try:
            probe = c.compile_metric(request, layer.layer, dialect="postgres")
            dataset = layer.datasets.get(probe.dataset_ids[0])
            if dataset is None:
                raise Unjudgeable(f"Bảng của {version.display_name} không có trong danh mục.")
            # A table materialised in the workspace schema is read on the platform database,
            # as questions.py does.
            source = (
                _internal_workspace_source(space, row.org_id)
                if dataset.schema_name == "workspace" and dataset.source_id is None
                else await session.get(catalog.Source, dataset.source_id)
            )
            if source is None or source.mode != "federated" or source.status not in QUERYABLE:
                raise Unjudgeable("Bảng của chỉ số nằm trên một nguồn chưa được phép truy vấn.")
            compiled = c.compile_metric(
                request, layer.layer, dialect=dialect_for_source(source.kind)
            )
        except (c.CompileError, SqlRejected) as exc:
            raise Unjudgeable(f"Không biên dịch được {version.display_name}: {exc}") from exc
    outcome = await run_query(
        org_id=row.org_id,
        data_space_id=space,
        principal_id=owner,
        caller_kind="eval",
        caller_id=row.id,
        source=source,
        sql=compiled.sql,
        secrets=secrets(),
        metric_version_ids=[version.id],
    )
    if not outcome.ok:
        raise Unjudgeable(f"Truy vấn chuỗi {version.display_name} không chạy được.")
    if outcome.truncated:
        raise Unjudgeable("Chuỗi của chỉ số quá dài để chạy thử (hơn 10.000 dòng).")
    names = [col["name"] for col in outcome.columns]
    at, value = names.index(time_dim.code), names.index("value")
    by = names.index(split) if split else None
    series: dict[str, dict[str, decimal.Decimal | None]] = {}
    for r in outcome.rows:
        if r[at] is None:
            continue
        member = WHOLE if by is None else str(r[by])
        series.setdefault(str(r[at]), {})[member] = _number(r[value])
    return series, grain


async def backtest(event: Event) -> None:
    monitor_id = uuid.UUID(str(event.payload.get("monitor_id") or event.aggregate_id))
    async with scoped_session(ROLE, event.org_id) as session:
        row = await session.scalar(
            select(monitor.Monitor).where(
                monitor.Monitor.id == monitor_id, monitor.Monitor.deleted_at.is_(None)
            )
        )
        if row is not None:
            session.expunge(row)
    asked = row.backtest if row is not None else None
    # A created or updated monitor whose backtest is not waiting (a PATCH of the schedule, an
    # event delivered again after the result) has nothing to run.
    if row is None or not isinstance(asked, dict) or asked.get("status") != "pending":
        return
    periods = asked.get("periods")
    periods = periods if isinstance(periods, int) and periods > 0 else DEFAULT_PERIODS
    requested_at = asked.get("requested_at")
    result: dict[str, Any] = {"requested_at": requested_at, "requested_periods": periods}
    try:
        # A rule that cannot be judged (or a kind not backtested here) is refused before any query.
        judge(row.rule_kind, row.rule, {}, periods=periods, before="")
        series, grain = await _series(row)
        result |= {"status": "done", "grain": grain}
        result |= judge(
            row.rule_kind,
            row.rule,
            series,
            periods=periods,
            before=complete_before(_today(), grain),
        )
        if not result["periods"]:
            result |= {"status": "failed", "reason": "Không có kỳ nào có số để chạy thử."}
    except Unsupported as exc:
        result |= {"status": "unsupported", "reason": str(exc)}
    except Unjudgeable as exc:
        result |= {"status": "failed", "reason": str(exc)}
    result["ran_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    asked_at = monitor.Monitor.backtest["requested_at"].astext
    async with scoped_session(ROLE, event.org_id) as session:
        written = await session.scalar(
            update(monitor.Monitor)
            .where(
                monitor.Monitor.id == monitor_id,
                monitor.Monitor.backtest["status"].astext == "pending",
                asked_at.is_(None) if requested_at is None else asked_at == str(requested_at),
            )
            .values(backtest=result)
            .returning(monitor.Monitor.id)
        )
    if written is not None:
        logger.info("worker-agent | monitor %s backtest %s", monitor_id, result["status"])


HANDLERS: dict[str, Handler] = {REQUESTED: backtest, CREATED: backtest, UPDATED: backtest}
