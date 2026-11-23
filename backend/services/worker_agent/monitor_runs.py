"""A monitor evaluated on its schedule (AL-10, ANALYTICS_SPEC F-20): schedules/targets.py keeps one
Temporal Schedule per active monitor (kind `monitor`, its cron read where the organisation is),
and each firing calls `evaluate`.

The evaluation is deterministic and is the backtest's (monitor_backtests.figures_at): the metric
in force is read in place as the monitor's owner, cut by period and by the monitor's split, and
the rule is judged on the last complete period that has a figure. The quality gate comes first:
a dataset its rules or contract hold back is not read and nothing fires (`skipped
QUALITY_BLOCKED`). A seasonal rule needs a model this worker does not have and is skipped as
unsupported, as its backtest says.

One alert per monitor and period (dedup_key = the period's label) holds every member that fired,
each as a figure with the query_run that read it. A member already in an alert of that period is
not alerted again; a member firing later in the period joins the period's open alert (occurrences
+ 1, BR-33). A new alert waits out the rule's cooldown (`cooldown_hours`, 24 by default, BR-33)
after the monitor's last one. The alert is written with the alert.fired event on monitor.outbox,
in one transaction under the monitor's row lock, so two firings never write it twice.

No LLM is called: the summary is written from the figures (invariant 11 allows one only after the
rules decided there is an alert, to explain it). The queries are recorded as caller_kind `eval`
with the monitor's id, as the backtest's are: query_run has no monitor caller.
"""

from __future__ import annotations

import datetime
import decimal
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs import clock, cron
from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import monitor
from services.worker_agent import monitor_backtests as backtests

logger = logging.getLogger(__name__)

ROLE = backtests.ROLE
EVENT_TYPE = "alert.fired"
DEFAULT_COOLDOWN_HOURS = 24  # BR-33: repeated signals within 24 hours are one alert


def _due(expr: str | None, local: datetime.datetime) -> bool:
    try:
        return expr is not None and cron.matches(expr, local)
    except ValueError:
        return False


def rule_value(rule: Any) -> Any:
    return rule.get("value") if isinstance(rule, dict) else None


def _number(raw: Any) -> decimal.Decimal | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return decimal.Decimal(str(raw))
    except decimal.InvalidOperation:
        return None


def cooldown_of(rule: Any) -> datetime.timedelta:
    raw = rule.get("cooldown_hours") if isinstance(rule, dict) else None
    hours = raw if isinstance(raw, int | float) and not isinstance(raw, bool) and raw >= 0 else None
    return datetime.timedelta(hours=DEFAULT_COOLDOWN_HOURS if hours is None else hours)


def latest(
    rule_kind: str, rule: Any, series: backtests.Series, *, before: str
) -> tuple[str, list[backtests.Figure]] | None:
    """The last complete period (label below `before`) the rule could judge, with the members
    past it there; None when no period can be judged."""
    labels = sorted(label for label in series if label < before)
    for i in range(len(labels) - 1, -1, -1):
        found = backtests.figures_at(
            rule_kind, rule, series, labels[i], labels[i - 1] if i > 0 else None
        )
        if found is not None:
            return labels[i], found
    return None


def _text(value: decimal.Decimal) -> str:
    # Amounts leave as decimal strings (A1.1); a percent is rounded for reading.
    return format(value.normalize(), "f")


def figure_json(f: backtests.Figure, query_run_id: uuid.UUID) -> dict[str, Any]:
    return {
        "member": f.member or None,
        "value": _text(f.value),
        "compared_to": _text(f.compared_to),
        "change": _text(f.change.quantize(decimal.Decimal("0.01"))),
        "query_run_id": str(query_run_id),
    }


def summary(row: monitor.Monitor, metric: str, period: str, figures: list[dict[str, Any]]) -> str:
    """Three lines: what changed, against which rule, what to do (the column's own contract).

    The figures were read with the owner's row scope, and every reader of the Data Space's alerts
    sees the summary: a split monitor's summary counts the members that fired and names neither
    them nor their numbers (they stay in `figures`, which no endpoint serves yet)."""
    rule = row.rule if isinstance(row.rule, dict) else {}
    split = backtests.split_of(row.scope_filter) is not None
    if row.rule_kind == "threshold":
        side = "trên" if rule.get("direction") == "above" else "dưới"
        why = f"Luật: {side} ngưỡng {rule.get('value')}."
        what = "" if split else f" = {figures[0]['value']}"
    else:
        side = "tăng" if rule.get("direction") == "up" else "giảm"
        why = f"Luật: {side} quá {rule.get('value')}% so với kỳ trước."
        first = figures[0]
        what = "" if split else f": {first['compared_to']} → {first['value']} ({first['change']}%)"
    count = f", {len(figures)} thành viên vượt luật" if split else ""
    return (
        f"{metric} kỳ {period}{what}{count}.\n{why}\n"
        "Mở điều tra từ cảnh báo này để xem nguyên nhân trước khi xử lý."
    )


async def evaluate(org_id: uuid.UUID, monitor_id: uuid.UUID, minute: datetime.datetime) -> str:
    """One scheduled evaluation of a monitor for the minute of `minute`; returns what it did, for
    the schedule workflow's result."""
    async with scoped_session(ROLE, org_id) as session:
        local = minute.astimezone(await clock.org_zone(session, org_id))
        row = await session.scalar(
            select(monitor.Monitor).where(
                monitor.Monitor.id == monitor_id,
                monitor.Monitor.status == "active",
                monitor.Monitor.deleted_at.is_(None),
            )
        )
        if row is None or not _due(row.schedule_cron, local.replace(tzinfo=None)):
            return "not due"
        session.expunge(row)
    try:
        backtests.check_rule(row.rule_kind, row.rule)
        read = await backtests.read_series(row, gate=True)
    except backtests.Blocked:
        logger.info("worker-agent | monitor %s skipped: data held back", monitor_id)
        return "skipped QUALITY_BLOCKED"
    except backtests.Unsupported:
        return "skipped unsupported"
    except backtests.Unjudgeable as exc:
        logger.warning("worker-agent | monitor %s not evaluated: %s", monitor_id, exc)
        return f"failed: {exc}"
    found = latest(
        row.rule_kind,
        row.rule,
        read.series,
        before=backtests.complete_before(local.date(), read.grain),
    )
    if found is None:
        return "no complete period"
    period, fired = found
    if not fired:
        return f"quiet {period}"
    # The widest move first: the summary and the alert's value/threshold_value name it.
    fired = sorted(fired, key=lambda f: abs(f.change), reverse=True)
    figures = [figure_json(f, read.query_run_id) for f in fired]
    return await _write(row, read.metric, period, figures, minute)


async def _write(
    row: monitor.Monitor,
    metric: str,
    period: str,
    figures: list[dict[str, Any]],
    minute: datetime.datetime,
) -> str:
    async with scoped_session(ROLE, row.org_id) as session:
        # The monitor's row first: two firings of it never both read "no alert yet".
        await session.scalar(
            select(monitor.Monitor.id).where(monitor.Monitor.id == row.id).with_for_update()
        )
        same_period = (
            await session.scalars(
                select(monitor.Alert).where(
                    monitor.Alert.monitor_id == row.id, monitor.Alert.dedup_key == period
                )
            )
        ).all()
        alerted = {
            f.get("member")
            for alert in same_period
            for f in (alert.figures or [])
            if isinstance(f, dict)
        }
        fresh = [f for f in figures if f["member"] not in alerted]
        if not fresh:
            return f"already alerted {period}"
        open_one = next((a for a in same_period if a.status != "closed"), None)
        if open_one is not None:
            open_one.figures = [*(open_one.figures or []), *fresh]
            open_one.occurrences += 1
            open_one.summary = summary(row, metric, period, open_one.figures)
            open_one.updated_at = minute
            _event(session, row, open_one, "merged")
            return f"alert {open_one.id} +{len(fresh)}"
        last = await session.scalar(
            select(monitor.Alert.fired_at)
            .where(monitor.Alert.monitor_id == row.id)
            .order_by(monitor.Alert.fired_at.desc())
            .limit(1)
        )
        if last is not None and last > minute - cooldown_of(row.rule):
            return f"cooldown {period}"
        first = fresh[0]
        alert = monitor.Alert(
            id=uuid7(),
            org_id=row.org_id,
            monitor_id=row.id,
            # The scheduled minute, not the write's: a daily rule's 24 hours of cooldown end at the
            # next day's firing, not a few seconds after it.
            fired_at=minute,
            period_label=period,
            # A split monitor's numbers are its members' (see summary): none on the row.
            # BR-124's closing note reads figures too (api monitor/rules.close_needs_note).
            value=None
            if backtests.split_of(row.scope_filter) is not None
            else decimal.Decimal(first["value"]),
            threshold_value=_number(rule_value(row.rule)) if row.rule_kind == "threshold" else None,
            summary=summary(row, metric, period, fresh),
            dedup_key=period,
            kind="metric",
            figures=fresh,
        )
        session.add(alert)
        _event(session, row, alert, "new")
        return f"alert {alert.id} {len(fresh)}"


def _event(session: AsyncSession, row: monitor.Monitor, alert: monitor.Alert, how: str) -> None:
    session.add(
        monitor.Outbox(
            id=uuid7(),
            org_id=row.org_id,
            aggregate_type="alert",
            aggregate_id=alert.id,
            event_type=EVENT_TYPE,
            payload={
                "alert_id": str(alert.id),
                "monitor_id": str(row.id),
                "data_space_id": str(row.data_space_id),
                "period_label": alert.period_label,
                "members": len(alert.figures or []),
                "occurrences": alert.occurrences if how == "merged" else 1,
                "status": alert.status if how == "merged" else "open",
                "notify_mode": row.notify_mode,
            },
        )
    )
