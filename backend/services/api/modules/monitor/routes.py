"""A2 group "Theo dõi, báo cáo, thông báo", schema monitor: monitors and alerts (UC-08-01, UC-08-02).

Evaluating a rule, running a backtest and sending an alert are the monitor agent's work
(worker-agent, SD-13): the endpoints record what the person asked for and write the event to
monitor.outbox in the same transaction (D1 rule 8). Attaching a monitor to the Data Space's
monitor agent (agent.agent_def, agent.agent_trigger) is schema agent's write, so it follows
`monitor.created` too. Reports live in reports.py, external calendars in calendars.py.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import context, monitor
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Order,
    Page,
    PageRequest,
    keyset,
    next_page,
    order_by,
    page_request,
)
from services.api.kernel.problems import Problem
from services.api.modules.monitor import calendars, reports, rules
from services.api.modules.monitor.schemas import (
    AlertAssign,
    AlertClose,
    AlertReopen,
    AlertView,
    BacktestRequest,
    MonitorCreate,
    MonitorPatch,
    MonitorView,
)
from services.api.modules.monitor.support import (
    ROLE,
    active_user,
    emit,
    in_data_space,
    invalid_field,
    now,
)

router = APIRouter()

MonitorId = Annotated[uuid.UUID, Path(alias="id")]
AlertId = Annotated[uuid.UUID, Path(alias="id")]

_NO_BACKTEST = BacktestRequest()
_NO_NOTE = AlertClose()


def _view(row: monitor.Monitor) -> MonitorView:
    view = MonitorView.model_validate(row, from_attributes=True)
    view.noise_warning = rules.noise_warning(row.backtest)
    return view


async def _monitor(
    session: AsyncSession,
    monitor_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> monitor.Monitor:
    query = select(monitor.Monitor).where(
        monitor.Monitor.id == monitor_id,
        monitor.Monitor.org_id == principal.org_id,
        monitor.Monitor.data_space_id == data_space,
        monitor.Monitor.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Monitor not found.")
    return row


def _owned(row: monitor.Monitor, principal: Principal) -> None:
    if row.owner_id != principal.user_id:
        raise Problem(403, "forbidden", "Only the monitor's owner can change it.")


async def _published_metric(
    session: AsyncSession, principal: Principal, data_space: uuid.UUID, metric_id: uuid.UUID
) -> None:
    # UC-08-01 precondition: the metric is published (active) in this Data Space.
    found = await session.scalar(
        select(context.Metric.id).where(
            context.Metric.id == metric_id,
            context.Metric.org_id == principal.org_id,
            context.Metric.data_space_id == data_space,
            context.Metric.status == "active",
        )
    )
    if found is None:
        raise invalid_field("metric_id", "No published metric with this id in the Data Space.")


@router.get(
    "/v1/monitors",
    response_model=Page[MonitorView],
    **endpoint("GET", "/v1/monitors", operation_id="listMonitors"),
)
async def list_monitors(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by("created_at", "updated_at", "status", "rule_kind", default="created_at desc")
        ),
    ],
    status: Annotated[str | None, Query(max_length=40)] = None,
    metric_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
) -> Page[MonitorView]:
    m = monitor.Monitor
    query = select(m).where(
        m.org_id == principal.org_id, m.data_space_id == data_space, m.deleted_at.is_(None)
    )
    for column, value in ((m.status, status), (m.metric_id, metric_id), (m.owner_id, owner_id)):
        if value is not None:
            query = query.where(column == value)
    query = keyset(
        query,
        columns={
            "created_at": m.created_at,
            "updated_at": m.updated_at,
            "status": m.status,
            "rule_kind": m.rule_kind,
        },
        tiebreak=m.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    rows, token = next_page(rows, order=order, page=page)
    return Page(items=[_view(r) for r in rows], next_page_token=token)


@router.post(
    "/v1/monitors",
    status_code=201,
    response_model=MonitorView,
    **endpoint("POST", "/v1/monitors", operation_id="createMonitor"),
)
async def create_monitor(
    body: MonitorCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> MonitorView:
    at = now()
    async with transaction(ROLE, principal) as session:
        await _published_metric(session, principal, data_space, body.metric_id)
        row = monitor.Monitor(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            owner_id=principal.user_id,
            metric_id=body.metric_id,
            scope_filter=body.scope_filter,
            rule_kind=body.rule_kind,
            rule=body.rule,
            schedule_cron=body.schedule_cron,
            notify_mode=body.notify_mode,
            recipients=body.recipients,
            # UC-08-01 step 3: the monitor agent backtests the rule on the last 12 periods and
            # writes the result here; the response then carries BR-32's noise warning.
            backtest=rules.backtest_request(rules.BACKTEST_PERIODS, at),
            status="active",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        emit(
            session,
            principal,
            "monitor",
            row.id,
            "monitor.created",
            monitor_id=row.id,
            data_space_id=data_space,
            metric_id=row.metric_id,
            rule_kind=row.rule_kind,
            status=row.status,
            backtest_periods=rules.BACKTEST_PERIODS,
        )
        await session.refresh(row)
        return _view(row)


@router.patch(
    "/v1/monitors/{id}",
    response_model=MonitorView,
    **endpoint("PATCH", "/v1/monitors/{id}", operation_id="updateMonitor"),
)
async def update_monitor(
    body: MonitorPatch,
    monitor_id: MonitorId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> MonitorView:
    changes = body.model_dump(exclude_unset=True)
    for field in ("scope_filter", "rule", "schedule_cron", "notify_mode", "recipients", "status"):
        if field in changes and changes[field] is None:
            raise invalid_field(field, f"{field} cannot be null.")
    at = now()
    async with transaction(ROLE, principal) as session:
        row = await _monitor(session, monitor_id, principal, data_space, lock=True)
        _owned(row, principal)
        previous = row.status
        for field, value in changes.items():
            setattr(row, field, value)
        if "rule" in changes or "scope_filter" in changes:
            # A changed rule invalidates the old backtest (BR-32 is judged on the rule saved).
            row.backtest = rules.backtest_request(rules.BACKTEST_PERIODS, at)
        if row.status == "active":
            row.paused_reason = None
        row.updated_by, row.updated_at = principal.user_id, at
        emit(
            session,
            principal,
            "monitor",
            row.id,
            "monitor.updated",
            monitor_id=row.id,
            data_space_id=data_space,
            changed=sorted(changes),
            previous_status=previous,
            status=row.status,
        )
        await session.flush()
        await session.refresh(row)
        return _view(row)


@router.post(
    "/v1/monitors/{id}:backtest",
    response_model=MonitorView,
    **endpoint("POST", "/v1/monitors/{id}:backtest", operation_id="backtestMonitor"),
)
async def backtest_monitor(
    monitor_id: MonitorId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    body: Annotated[BacktestRequest, Body()] = _NO_BACKTEST,
) -> MonitorView:
    async with transaction(ROLE, principal) as session:
        row = await _monitor(session, monitor_id, principal, data_space, lock=True)
        row.backtest = rules.backtest_request(body.periods, now())
        row.updated_by, row.updated_at = principal.user_id, now()
        emit(
            session,
            principal,
            "monitor",
            row.id,
            "monitor.backtest_requested",
            monitor_id=row.id,
            data_space_id=data_space,
            periods=body.periods,
        )
        await session.flush()
        await session.refresh(row)
        return _view(row)


# ---------------------------------------------------------------- alerts (UC-08-02)


@router.get(
    "/v1/alerts",
    response_model=Page[AlertView],
    **endpoint("GET", "/v1/alerts", operation_id="listAlerts"),
)
async def list_alerts(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by(
                "fired_at",
                "created_at",
                "updated_at",
                "status",
                "kind",
                "occurrences",
                default="fired_at desc",
            )
        ),
    ],
    status: str | None = None,
    monitor_id: uuid.UUID | None = None,
    from_: Annotated[datetime.datetime | None, Query(alias="from")] = None,
    to: datetime.datetime | None = None,
) -> Page[AlertView]:
    alert = monitor.Alert
    query = (
        select(alert)
        .join(monitor.Monitor, monitor.Monitor.id == alert.monitor_id)
        .where(alert.org_id == principal.org_id, monitor.Monitor.data_space_id == data_space)
    )
    if status:
        query = query.where(alert.status == status)
    if monitor_id:
        query = query.where(alert.monitor_id == monitor_id)
    if from_:
        query = query.where(alert.fired_at >= from_)
    if to:
        query = query.where(alert.fired_at < to)
    query = keyset(
        query,
        columns={
            "fired_at": alert.fired_at,
            "created_at": alert.created_at,
            "updated_at": alert.updated_at,
            "status": alert.status,
            "kind": alert.kind,
            "occurrences": alert.occurrences,
        },
        tiebreak=alert.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    rows, token = next_page(rows, order=order, page=page)
    return Page(
        items=[AlertView.model_validate(r, from_attributes=True) for r in rows],
        next_page_token=token,
    )


async def _alert(
    session: AsyncSession, alert_id: uuid.UUID, principal: Principal, data_space: uuid.UUID
) -> monitor.Alert:
    row = await session.scalar(
        select(monitor.Alert)
        .join(monitor.Monitor, monitor.Monitor.id == monitor.Alert.monitor_id)
        .where(
            monitor.Alert.id == alert_id,
            monitor.Alert.org_id == principal.org_id,
            monitor.Monitor.data_space_id == data_space,
        )
        .with_for_update(of=monitor.Alert)
    )
    if row is None:
        raise Problem(404, "not_found", "Alert not found.")
    return row


def _alert_event(
    session: AsyncSession,
    principal: Principal,
    row: monitor.Alert,
    event_type: str,
    previous: str,
    data_space: uuid.UUID,
) -> None:
    # Audit records every acknowledgement and closing from these events (BR-124).
    emit(
        session,
        principal,
        "alert",
        row.id,
        event_type,
        alert_id=row.id,
        monitor_id=row.monitor_id,
        data_space_id=data_space,
        previous_status=previous,
        status=row.status,
        assigned_to=row.assigned_to,
        actor_id=principal.user_id,
    )


@router.post(
    "/v1/alerts/{id}:ack",
    response_model=AlertView,
    **endpoint("POST", "/v1/alerts/{id}:ack", operation_id="ackAlert"),
)
async def ack_alert(
    alert_id: AlertId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> AlertView:
    async with transaction(ROLE, principal) as session:
        row = await _alert(session, alert_id, principal, data_space)
        if row.status != "open":
            raise Problem(
                409,
                "invalid_state",
                f"The alert is {row.status}; only an open one is acknowledged.",
            )
        previous, row.status = row.status, "ack"
        row.updated_by, row.updated_at = principal.user_id, now()
        _alert_event(session, principal, row, "alert.acknowledged", previous, data_space)
        await session.flush()
        return AlertView.model_validate(row, from_attributes=True)


@router.post(
    "/v1/alerts/{id}:assign",
    response_model=AlertView,
    **endpoint("POST", "/v1/alerts/{id}:assign", operation_id="assignAlert"),
)
async def assign_alert(
    body: AlertAssign, alert_id: AlertId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> AlertView:
    async with transaction(ROLE, principal) as session:
        row = await _alert(session, alert_id, principal, data_space)
        if row.status == "closed":
            raise Problem(409, "invalid_state", "The alert is closed.")
        if not await active_user(session, principal, body.assignee_id):
            raise invalid_field("assignee_id", "assignee_id is not an active user.")
        # The assignee is notified with the alert's content: they must be able to open the space.
        if not await in_data_space(session, principal, body.assignee_id, data_space):
            raise invalid_field("assignee_id", "assignee_id is not a member of the Data Space.")
        previous = row.status
        row.status, row.assigned_to = "assigned", body.assignee_id
        row.updated_by, row.updated_at = principal.user_id, now()
        _alert_event(session, principal, row, "alert.assigned", previous, data_space)
        await session.flush()
        return AlertView.model_validate(row, from_attributes=True)


@router.post(
    "/v1/alerts/{id}:close",
    response_model=AlertView,
    **endpoint("POST", "/v1/alerts/{id}:close", operation_id="closeAlert"),
)
async def close_alert(
    alert_id: AlertId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    body: Annotated[AlertClose, Body()] = _NO_NOTE,
) -> AlertView:
    note = (body.close_note or "").strip() or None
    async with transaction(ROLE, principal) as session:
        row = await _alert(session, alert_id, principal, data_space)
        if row.status == "closed":
            raise Problem(409, "invalid_state", "The alert is already closed.")
        if note is None and rules.close_needs_note(row.value, row.threshold_value):
            raise Problem(
                422,
                "business_rule_violated",
                "The value is more than twice the threshold; closing needs a note (BR-124).",
            )
        previous = row.status
        at = now()
        # The table's CHECK refuses a close without a note after more than 2 occurrences (BR-124).
        row.status, row.close_note, row.closed_at = "closed", note, at
        row.updated_by, row.updated_at = principal.user_id, at
        _alert_event(session, principal, row, "alert.closed", previous, data_space)
        await session.flush()
        return AlertView.model_validate(row, from_attributes=True)


@router.post(
    "/v1/alerts/{id}:reopen",
    response_model=AlertView,
    **endpoint("POST", "/v1/alerts/{id}:reopen", operation_id="reopenAlert"),
)
async def reopen_alert(
    body: AlertReopen, alert_id: AlertId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> AlertView:
    reason = body.reason.strip()
    if not reason:
        raise invalid_field("reason", "reason is required.")
    async with transaction(ROLE, principal) as session:
        row = await _alert(session, alert_id, principal, data_space)
        if row.status != "closed":
            raise Problem(
                409, "invalid_state", f"The alert is {row.status}; only a closed one is reopened."
            )
        # One alert per rule and period is open at a time (unique monitor_id, dedup_key while not
        # closed): if the monitor has fired it again since, that alert is the one to work on.
        again = await session.scalar(
            select(monitor.Alert.id).where(
                monitor.Alert.monitor_id == row.monitor_id,
                monitor.Alert.dedup_key == row.dedup_key,
                monitor.Alert.status != "closed",
            )
        )
        if again is not None:
            raise Problem(
                409, "invalid_state", f"Alert {again} of the same rule and period is open."
            )
        previous, note = row.status, row.close_note
        at = now()
        # The close note belonged to the closing; it is kept in the audit record below.
        row.status, row.close_note, row.closed_at = "open", None, None
        row.updated_by, row.updated_at = principal.user_id, at
        _alert_event(session, principal, row, "alert.reopened", previous, data_space)
        # The reason is the audit record's (A1.8), not part of the domain event.
        emit(
            session,
            principal,
            "alert",
            row.id,
            "audit.requested",
            action="alert.reopen",
            object_kind="alert",
            object_id=row.id,
            actor_id=principal.user_id,
            client_id=principal.client_id,
            before={"status": previous, "close_note": note},
            after={"status": row.status, "reason": reason},
        )
        await session.flush()
        return AlertView.model_validate(row, from_attributes=True)


router.include_router(reports.router)
router.include_router(calendars.router)
