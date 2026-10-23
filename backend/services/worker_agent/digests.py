"""The leadership digest (UC-08-04): a report_run of kind `digest`, built for its one reader.

A digest has no workspace: it reads the metrics its owner chose (the metric_ids the api records
with report_schedule.created/updated, since no column holds them; without any, the metrics the
owner watches), the alerts fired in its Data Space over the week before as_of, and what waits
for the owner in "Cần bạn". Every figure is computed with the owner's rights and read by the
owner alone, and is an aggregate (BR-125): a metric over its month, never a row.

Each key metric is its value for the month of as_of against the month before, with the query
run behind it (the receipt's "how it was computed"); one that does not compile or run is left
out and named, never shown without its run. The summary is at most five sentences, from a
template, not a model: the numbers in it are the figures above. Nothing over a threshold still
makes a short digest saying so.
"""

from __future__ import annotations

import datetime
import decimal
import html
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog, inbox, monitor
from services.worker_agent import questions
from services.worker_agent.query.executor import run_query
from services.worker_agent.query.scope import can_ask_data_space
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import load

logger = logging.getLogger(__name__)

MAX_METRICS = 5  # UC-08-04: a digest is read in a minute
ALERT_DAYS = 7


@dataclass
class Kpi:
    code: str
    name: str
    unit: str | None
    value: decimal.Decimal | None = None
    previous: decimal.Decimal | None = None
    query_run_id: uuid.UUID | None = None
    missing: str | None = None

    @property
    def change(self) -> decimal.Decimal | None:
        if self.value is None or self.previous in (None, 0):
            return None
        assert self.previous is not None
        return (self.value - self.previous) / abs(self.previous) * 100


def _month(day: datetime.date) -> str:
    return f"{day:%Y-%m}"


def _number(value: Any) -> decimal.Decimal | None:
    try:
        return None if value is None else decimal.Decimal(str(value))
    except decimal.InvalidOperation:
        return None


def vn(value: decimal.Decimal | None, decimals: int = 2) -> str:
    if value is None:
        return "—"
    text = f"{value:,.{decimals}f}"
    return text.replace(",", " ").replace(".", ",").replace(" ", ".")


async def metric_ids(org: uuid.UUID, schedule: monitor.ReportSchedule) -> list[uuid.UUID]:
    """The digest's metrics: the latest list the api recorded for the schedule, else the owner's
    active watches in the Data Space."""
    async with scoped_session("svc_monitor", org, schedule.owner_id) as session:
        payload = await session.scalar(
            select(monitor.Outbox.payload)
            .where(
                monitor.Outbox.aggregate_id == schedule.id,
                monitor.Outbox.event_type.in_(
                    ("report_schedule.created", "report_schedule.updated")
                ),
            )
            .order_by(monitor.Outbox.created_at.desc())
            .limit(1)
        )
        chosen = (payload or {}).get("metric_ids") or []
        if not chosen:
            chosen = list(
                (
                    await session.scalars(
                        select(monitor.Monitor.metric_id)
                        .where(
                            monitor.Monitor.data_space_id == schedule.data_space_id,
                            monitor.Monitor.owner_id == schedule.owner_id,
                            monitor.Monitor.status == "active",
                            monitor.Monitor.deleted_at.is_(None),
                        )
                        .distinct()
                    )
                ).all()
            )
    out: list[uuid.UUID] = []
    for raw in chosen:
        value = uuid.UUID(str(raw))
        if value not in out:
            out.append(value)
    return out[:MAX_METRICS]


async def kpis(
    org: uuid.UUID, space: uuid.UUID, owner: uuid.UUID, ids: list[uuid.UUID], as_of: datetime.date
) -> list[Kpi]:
    """Each metric for the month of as_of and the month before, run in place as the owner."""
    start = (as_of.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
    # A period's end is not in it: the day after as_of, so a balance read on as_of is.
    window = c.Period(start, as_of + datetime.timedelta(days=1))
    out: list[Kpi] = []
    async with scoped_session("svc_qa", org, owner) as session:
        if not await can_ask_data_space(session, data_space_id=space, principal_id=owner):
            return []
        layer = await load(session, space)
        planned: list[tuple[Kpi, c.Compiled | None, catalog.Source | None, uuid.UUID]] = []
        for metric_id in ids:
            version = layer.versions.get(metric_id)
            if version is None:
                continue
            kpi = Kpi(version.metric_code, version.display_name, version.unit)
            try:
                time_dim = c.time_dimension_of(version, layer.layer)
                if time_dim is None:
                    raise c.CompileError("NO_DATA_FOR_PERIOD", "no time dimension")
                request = c.Request(version, group_by=[time_dim.code], period=window, grain="month")
                compiled = c.compile_metric(request, layer.layer, dialect="postgres")
                dataset = layer.datasets[compiled.dataset_ids[0]]
                source = (
                    questions._internal_workspace_source(space, org)
                    if dataset.schema_name == "workspace" and dataset.source_id is None
                    else await session.get(catalog.Source, dataset.source_id)
                )
                if source is None or source.status not in questions.QUERYABLE:
                    raise c.CompileError("TOOL_UNAVAILABLE", "source not queryable")
                compiled = c.compile_metric(
                    request, layer.layer, dialect=questions.dialect_for_source(source.kind)
                )
            except (c.CompileError, KeyError) as exc:
                kpi.missing = f"chưa tính được ({exc})"
                planned.append((kpi, None, None, version.id))
                continue
            planned.append((kpi, compiled, source, version.id))
    for kpi, ready, source, version_id in planned:
        if ready is not None and source is not None:
            outcome = await run_query(
                org_id=org,
                data_space_id=space,
                principal_id=owner,
                caller_kind="ad_hoc",
                caller_id=version_id,
                source=source,
                sql=ready.sql,
                secrets=questions.secrets(),
                metric_version_ids=[version_id],
            )
            if outcome.ok:
                months = {str(row[0])[:7]: _number(row[-1]) for row in outcome.rows}
                kpi.value, kpi.previous = months.get(_month(as_of)), months.get(_month(start))
                kpi.query_run_id = outcome.query_run_id
                if kpi.value is None:
                    kpi.missing = f"chưa có số tháng {as_of:%m/%Y}"
            else:
                kpi.missing = "truy vấn không chạy được"
        out.append(kpi)
    return out


async def alerts(
    org: uuid.UUID, space: uuid.UUID, owner: uuid.UUID, as_of: datetime.date
) -> list[str]:
    """What fired in the Data Space over the week before as_of."""
    since = datetime.datetime.combine(as_of, datetime.time(), datetime.UTC) - datetime.timedelta(
        days=ALERT_DAYS - 1
    )
    until = datetime.datetime.combine(as_of, datetime.time(), datetime.UTC) + datetime.timedelta(
        days=1
    )
    async with scoped_session("svc_monitor", org, owner) as session:
        rows = (
            await session.scalars(
                select(monitor.Alert.summary)
                .join(monitor.Monitor, monitor.Monitor.id == monitor.Alert.monitor_id)
                .where(
                    monitor.Monitor.data_space_id == space,
                    monitor.Alert.fired_at >= since,
                    monitor.Alert.fired_at < until,
                )
                .order_by(monitor.Alert.fired_at.desc())
            )
        ).all()
    return list(rows)


async def waiting(org: uuid.UUID, owner: uuid.UUID) -> int:
    """How many requests wait for the owner in "Cần bạn"."""
    async with scoped_session("svc_notify", org, owner) as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(inbox.HitlRequest)
                .where(
                    inbox.HitlRequest.assignee_id == owner,
                    inbox.HitlRequest.status.in_(("open", "reassigned")),
                )
            )
            or 0
        )


def summary(shown: list[Kpi], fired: list[str], pending: int, as_of: datetime.date) -> list[str]:
    """At most five sentences; each number in them is a figure of `shown`."""
    lines = [f"Số liệu đến ngày {as_of:%d/%m/%Y}, {len(shown)} chỉ số chính."]
    moved = sorted(
        (k for k in shown if k.change is not None),
        key=lambda k: abs(k.change or decimal.Decimal(0)),
        reverse=True,
    )
    for k in moved[:2]:
        assert k.change is not None
        way = "tăng" if k.change > 0 else "giảm"
        lines.append(f"{k.name} {way} {vn(abs(k.change), 1)}% so với tháng trước.")
    lines.append(
        f"Có {len(fired)} cảnh báo trong {ALERT_DAYS} ngày qua."
        if fired
        else "Không có thay đổi vượt ngưỡng trong kỳ."
    )
    if pending:
        lines.append(f"{pending} việc đang chờ bạn ở Cần bạn.")
    return lines[:5]


def render(
    title: str,
    as_of: datetime.date,
    sentences: list[str],
    shown: list[Kpi],
    dropped: list[Kpi],
    fired: list[str],
    link_base: str,
) -> bytes:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(k.name)}</td><td>{vn(k.value)}</td><td>{vn(k.previous)}</td>"
        f"<td>{'' if k.change is None else vn(k.change, 1) + '%'}</td>"
        f'<td><a href="{html.escape(link_base)}/query-runs/{k.query_run_id}">cách tính</a></td>'
        "</tr>"
        for k in shown
    )
    parts = [
        '<!doctype html><html lang="vi"><head><meta charset="utf-8">',
        f"<title>{html.escape(title)}</title></head><body><h1>{html.escape(title)}</h1>",
        *(f"<p>{html.escape(s)}</p>" for s in sentences),
        "<h2>Chỉ số chính</h2><table><thead><tr><th>Chỉ số</th>"
        f"<th>Tháng {as_of:%m/%Y}</th><th>Tháng trước</th><th>Thay đổi</th><th></th></tr></thead>"
        f"<tbody>{rows}</tbody></table>",
    ]
    if dropped:
        parts.append(
            "<p>Không đưa vào vì không truy được số: "
            + html.escape("; ".join(f"{k.name}: {k.missing}" for k in dropped))
            + "</p>"
        )
    if fired:
        parts.append(
            "<h2>Cảnh báo</h2><ul>"
            + "".join(f"<li>{html.escape(a)}</li>" for a in fired[:10])
            + "</ul>"
        )
    parts.append("</body></html>")
    return "".join(parts).encode()
