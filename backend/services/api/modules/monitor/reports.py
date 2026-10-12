"""Scheduled reports, their runs and the leader digest (UC-02-04, UC-08-03, UC-08-04).

The schedule is registered with Temporal and every run executed by the report agent: the
endpoints write report_schedule / report_run and the event that worker acts on (A1.6, D1 rule 8).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Query, Response
from sqlalchemy import delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import context, monitor, org, workspace
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
from services.api.modules.monitor import rules
from services.api.modules.monitor.schemas import (
    Accepted,
    DigestPut,
    RecipientIn,
    RecipientView,
    ReportCreate,
    ReportPatch,
    ReportRunView,
    ReportScheduleDetail,
    ReportScheduleView,
    RunNow,
)
from services.api.modules.monitor.support import (
    POLL_AFTER_MS,
    ROLE,
    check_work_calendar,
    emit,
    invalid_field,
    now,
)

router = APIRouter()

ReportId = Annotated[uuid.UUID, Path(alias="id")]
_NOW = RunNow()

# UC-08-04: the digest goes out at 7:30 on Mondays unless the reader picks another time.
DIGEST_CRON = "30 7 * * 1"
DIGEST_TITLE = "Bản tin lãnh đạo"
DIGEST_AS_OF = "end_of_previous_day"


async def _recipients(
    session: AsyncSession, schedule_id: uuid.UUID
) -> list[monitor.ReportRecipient]:
    return list(
        (
            await session.scalars(
                select(monitor.ReportRecipient)
                .where(monitor.ReportRecipient.report_schedule_id == schedule_id)
                .order_by(monitor.ReportRecipient.created_at, monitor.ReportRecipient.id)
            )
        ).all()
    )


async def _view(session: AsyncSession, row: monitor.ReportSchedule) -> ReportScheduleView:
    view = ReportScheduleView.model_validate(row, from_attributes=True)
    view.recipients = [
        RecipientView.model_validate(r, from_attributes=True)
        for r in await _recipients(session, row.id)
    ]
    return view


async def _permission_group(
    session: AsyncSession, principal: Principal, data_space: uuid.UUID, recipient: RecipientIn
) -> str:
    """BR-34: the recipient's grants in the Data Space; an unknown or inactive one is refused."""
    member = org.DataSpaceMember
    if recipient.principal_type == "user":
        known = await session.scalar(
            select(org.AppUser.id).where(
                org.AppUser.id == recipient.principal_id,
                org.AppUser.org_id == principal.org_id,
                org.AppUser.status == "active",
            )
        )
        # A user's grants: their own membership and their groups' memberships.
        holders = select(org.UserGroupMember.group_id).where(
            org.UserGroupMember.user_id == recipient.principal_id
        )
        who = or_(
            member.principal_id == recipient.principal_id,
            member.principal_id.in_(holders),
        )
    else:
        known = await session.scalar(
            select(org.UserGroup.id).where(
                org.UserGroup.id == recipient.principal_id,
                org.UserGroup.org_id == principal.org_id,
            )
        )
        who = member.principal_id == recipient.principal_id
    if known is None:
        raise invalid_field("recipients", f"Recipient {recipient.principal_id} is not known.")
    grants = (
        await session.execute(
            select(member.role_id, member.row_scope).where(
                member.org_id == principal.org_id, member.data_space_id == data_space, who
            )
        )
    ).all()
    return rules.permission_group((g[0], g[1]) for g in grants)


async def _replace_recipients(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    schedule_id: uuid.UUID,
    recipients: list[RecipientIn],
) -> None:
    await session.execute(
        delete(monitor.ReportRecipient).where(
            monitor.ReportRecipient.report_schedule_id == schedule_id
        )
    )
    for recipient in recipients:
        session.add(
            monitor.ReportRecipient(
                id=uuid7(),
                org_id=principal.org_id,
                report_schedule_id=schedule_id,
                principal_type=recipient.principal_type,
                principal_id=recipient.principal_id,
                channels=list(dict.fromkeys(recipient.channels)),
                permission_group=await _permission_group(session, principal, data_space, recipient),
            )
        )


async def _check_workspace(
    session: AsyncSession, principal: Principal, data_space: uuid.UUID, workspace_id: uuid.UUID
) -> None:
    found = await session.scalar(
        select(workspace.Workspace.id).where(
            workspace.Workspace.id == workspace_id,
            workspace.Workspace.org_id == principal.org_id,
            workspace.Workspace.data_space_id == data_space,
            workspace.Workspace.deleted_at.is_(None),
        )
    )
    if found is None:
        raise invalid_field("workspace_id", "No such session in this Data Space.")


async def _schedule(
    session: AsyncSession,
    schedule_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> monitor.ReportSchedule:
    query = select(monitor.ReportSchedule).where(
        monitor.ReportSchedule.id == schedule_id,
        monitor.ReportSchedule.org_id == principal.org_id,
        monitor.ReportSchedule.data_space_id == data_space,
        monitor.ReportSchedule.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Report schedule not found.")
    return row


def _owned(row: monitor.ReportSchedule, principal: Principal) -> None:
    if row.owner_id != principal.user_id:
        raise Problem(403, "forbidden", "Only the report's owner can change or run it.")


def _schedule_event(
    session: AsyncSession,
    principal: Principal,
    row: monitor.ReportSchedule,
    event_type: str,
    **extra: Any,
) -> None:
    emit(
        session,
        principal,
        "report_schedule",
        row.id,
        event_type,
        report_schedule_id=row.id,
        data_space_id=row.data_space_id,
        kind=row.kind,
        status=row.status,
        **extra,
    )


def _mine_or_received(principal: Principal) -> Any:
    """The schedules the caller owns or receives, directly or through a group: a report's
    recipients and outputs are not for every member of the Data Space (BR-34)."""
    recipient = monitor.ReportRecipient
    my_groups = select(org.UserGroupMember.group_id).where(
        org.UserGroupMember.user_id == principal.user_id
    )
    received = exists().where(
        recipient.report_schedule_id == monitor.ReportSchedule.id,
        or_(
            (recipient.principal_type == "user") & (recipient.principal_id == principal.user_id),
            (recipient.principal_type == "group") & recipient.principal_id.in_(my_groups),
        ),
    )
    return or_(monitor.ReportSchedule.owner_id == principal.user_id, received)


@router.get(
    "/v1/reports",
    response_model=Page[ReportScheduleView],
    **endpoint("GET", "/v1/reports", operation_id="listReportSchedules"),
)
async def list_reports(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(order_by("created_at", "updated_at", "title", "status", default="created_at desc")),
    ],
    status: Annotated[str | None, Query(max_length=40)] = None,
    kind: Annotated[str | None, Query(max_length=40)] = None,
) -> Page[ReportScheduleView]:
    rs = monitor.ReportSchedule
    query = select(rs).where(
        rs.org_id == principal.org_id,
        rs.data_space_id == data_space,
        rs.deleted_at.is_(None),
        _mine_or_received(principal),
    )
    for column, value in ((rs.status, status), (rs.kind, kind)):
        if value is not None:
            query = query.where(column == value)
    query = keyset(
        query,
        columns={
            "created_at": rs.created_at,
            "updated_at": rs.updated_at,
            "title": rs.title,
            "status": rs.status,
        },
        tiebreak=rs.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
        rows, token = next_page(rows, order=order, page=page)
        items = [await _view(session, r) for r in rows]
    return Page(items=items, next_page_token=token)


async def _permission_groups(
    session: AsyncSession, principal: Principal, schedule_id: uuid.UUID
) -> set[str]:
    """The permission groups the caller receives the schedule's outputs as."""
    my_groups = select(org.UserGroupMember.group_id).where(
        org.UserGroupMember.user_id == principal.user_id
    )
    return set(
        (
            await session.scalars(
                select(monitor.ReportRecipient.permission_group).where(
                    monitor.ReportRecipient.report_schedule_id == schedule_id,
                    or_(
                        (monitor.ReportRecipient.principal_type == "user")
                        & (monitor.ReportRecipient.principal_id == principal.user_id),
                        (monitor.ReportRecipient.principal_type == "group")
                        & monitor.ReportRecipient.principal_id.in_(my_groups),
                    ),
                )
            )
        ).all()
    )


@router.get(
    "/v1/reports/{id}",
    response_model=ReportScheduleDetail,
    **endpoint("GET", "/v1/reports/{id}", operation_id="getReportSchedule"),
)
async def get_report(
    schedule_id: ReportId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ReportScheduleDetail:
    async with transaction(ROLE, principal) as session:
        row = await _schedule(session, schedule_id, principal, data_space)
        owner = row.owner_id == principal.user_id
        groups = set() if owner else await _permission_groups(session, principal, row.id)
        if not owner and not groups:
            # Neither the owner nor a recipient: the schedule is not theirs to see (BR-34).
            raise Problem(404, "not_found", "Report schedule not found.")
        view = ReportScheduleDetail(**(await _view(session, row)).model_dump())
        run = await session.scalar(
            select(monitor.ReportRun)
            .where(monitor.ReportRun.report_schedule_id == row.id)
            .order_by(monitor.ReportRun.created_at.desc(), monitor.ReportRun.id.desc())
            .limit(1)
        )
    if run is not None:
        view.latest_run = ReportRunView.model_validate(
            {
                **{c: getattr(run, c) for c in ReportRunView.model_fields if c != "outputs"},
                "outputs": rules.visible_outputs(run.outputs, owner=owner, groups=groups),
            }
        )
    return view


@router.post(
    "/v1/reports",
    status_code=201,
    response_model=ReportScheduleView,
    **endpoint("POST", "/v1/reports", operation_id="createReportSchedule"),
)
async def create_report(
    body: ReportCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ReportScheduleView:
    if body.kind == "report" and body.workspace_id is None:
        # A3: a report is built from a session; only the digest has none.
        raise invalid_field("workspace_id", "A report needs workspace_id.")
    async with transaction(ROLE, principal) as session:
        if body.workspace_id is not None:
            await _check_workspace(session, principal, data_space, body.workspace_id)
        await check_work_calendar(session, principal, body.work_calendar_id)
        row = monitor.ReportSchedule(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            kind=body.kind,
            workspace_id=body.workspace_id,
            owner_id=principal.user_id,
            title=body.title,
            cron=body.cron,
            work_calendar_id=body.work_calendar_id,
            cutoff_time=body.cutoff_time,
            as_of_rule=body.as_of_rule,
            formats=list(dict.fromkeys(body.formats)),
            require_publish_check=body.require_publish_check,
            status="active",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await _replace_recipients(session, principal, data_space, row.id, body.recipients)
        _schedule_event(session, principal, row, "report_schedule.created", cron=row.cron)
        await session.flush()
        await session.refresh(row)
        return await _view(session, row)


@router.patch(
    "/v1/reports/{id}",
    response_model=ReportScheduleView,
    **endpoint("PATCH", "/v1/reports/{id}", operation_id="updateReportSchedule"),
)
async def update_report(
    body: ReportPatch,
    schedule_id: ReportId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> ReportScheduleView:
    changes = body.model_dump(exclude_unset=True, exclude={"recipients"})
    for field in ("title", "cron", "as_of_rule", "formats", "status"):
        if field in changes and changes[field] is None:
            raise invalid_field(field, f"{field} cannot be null.")
    async with transaction(ROLE, principal) as session:
        row = await _schedule(session, schedule_id, principal, data_space, lock=True)
        _owned(row, principal)
        if "work_calendar_id" in changes:
            await check_work_calendar(session, principal, changes["work_calendar_id"])
        previous = row.status
        for field, value in changes.items():
            setattr(row, field, value)
        if body.recipients is not None:
            await _replace_recipients(session, principal, data_space, row.id, body.recipients)
        row.updated_by, row.updated_at = principal.user_id, now()
        changed = sorted([*changes, *(["recipients"] if body.recipients is not None else [])])
        _schedule_event(
            session,
            principal,
            row,
            "report_schedule.updated",
            previous_status=previous,
            changed=changed,
            cron=row.cron,
        )
        await session.flush()
        await session.refresh(row)
        return await _view(session, row)


@router.post(
    "/v1/reports/{id}:pause",
    response_model=ReportScheduleView,
    **endpoint("POST", "/v1/reports/{id}:pause", operation_id="pauseReportSchedule"),
)
async def pause_report(
    schedule_id: ReportId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ReportScheduleView:
    async with transaction(ROLE, principal) as session:
        row = await _schedule(session, schedule_id, principal, data_space, lock=True)
        _owned(row, principal)
        if row.status == "paused":
            raise Problem(409, "invalid_state", "The report is already paused.")
        previous, row.status = row.status, "paused"
        row.updated_by, row.updated_at = principal.user_id, now()
        _schedule_event(session, principal, row, "report_schedule.paused", previous_status=previous)
        await session.flush()
        await session.refresh(row)
        return await _view(session, row)


@router.post(
    "/v1/reports/{id}:run-now",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/reports/{id}:run-now", operation_id="runReportNow"),
)
async def run_report_now(
    schedule_id: ReportId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
    body: Annotated[RunNow, Body()] = _NOW,
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        row = await _schedule(session, schedule_id, principal, data_space)
        _owned(row, principal)
        # UC-08-03 5a: as_of is the day the owner picks, today by default.
        as_of = body.as_of or now().date()
        run = monitor.ReportRun(
            id=uuid7(),
            org_id=principal.org_id,
            report_schedule_id=row.id,
            kind=row.kind,
            as_of=as_of,
            trigger="run_now",
            status="running",
        )
        session.add(run)
        await session.flush()
        emit(
            session,
            principal,
            "report_run",
            run.id,
            "report_run.requested",
            report_run_id=run.id,
            report_schedule_id=row.id,
            data_space_id=row.data_space_id,
            kind=run.kind,
            as_of=as_of,
            trigger=run.trigger,
            status=run.status,
            requested_by=principal.user_id,
        )
    response.headers["Location"] = f"/v1/report-runs/{run.id}"
    return Accepted(id=run.id, status=run.status, poll_after_ms=POLL_AFTER_MS)


@router.get(
    "/v1/report-runs/{id}",
    response_model=ReportRunView,
    **endpoint("GET", "/v1/report-runs/{id}", operation_id="getReportRun"),
)
async def get_report_run(
    run_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> ReportRunView:
    async with transaction(ROLE, principal) as session:
        found = (
            await session.execute(
                select(monitor.ReportRun, monitor.ReportSchedule.owner_id)
                .join(
                    monitor.ReportSchedule,
                    monitor.ReportSchedule.id == monitor.ReportRun.report_schedule_id,
                )
                .where(
                    monitor.ReportRun.id == run_id,
                    monitor.ReportRun.org_id == principal.org_id,
                    monitor.ReportSchedule.data_space_id == data_space,
                )
            )
        ).first()
        if found is None:
            raise Problem(404, "not_found", "Report run not found.")
        run, owner_id = found
        owner = owner_id == principal.user_id
        groups: set[str] = set()
        if not owner:
            my_groups = select(org.UserGroupMember.group_id).where(
                org.UserGroupMember.user_id == principal.user_id
            )
            groups = set(
                (
                    await session.scalars(
                        select(monitor.ReportRecipient.permission_group).where(
                            monitor.ReportRecipient.report_schedule_id == run.report_schedule_id,
                            or_(
                                (monitor.ReportRecipient.principal_type == "user")
                                & (monitor.ReportRecipient.principal_id == principal.user_id),
                                (monitor.ReportRecipient.principal_type == "group")
                                & monitor.ReportRecipient.principal_id.in_(my_groups),
                            ),
                        )
                    )
                ).all()
            )
            if not groups:
                # Neither the owner nor a recipient: the run is not theirs to see (BR-34).
                raise Problem(404, "not_found", "Report run not found.")
    view = ReportRunView.model_validate(
        {
            **{c: getattr(run, c) for c in ReportRunView.model_fields if c != "outputs"},
            "outputs": rules.visible_outputs(run.outputs, owner=owner, groups=groups),
        }
    )
    return view


@router.put(
    "/v1/me/digest",
    response_model=ReportScheduleView,
    **endpoint("PUT", "/v1/me/digest", operation_id="putMyDigest"),
)
async def put_digest(
    body: DigestPut, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ReportScheduleView:
    # report_schedule.data_space_id is NOT NULL, so the digest is one per reader and Data Space.
    status = "active" if body.enabled else "paused"
    async with transaction(ROLE, principal) as session:
        if body.metrics:
            known = set(
                (
                    await session.scalars(
                        select(context.Metric.id).where(
                            context.Metric.id.in_(body.metrics),
                            context.Metric.org_id == principal.org_id,
                            context.Metric.data_space_id == data_space,
                            context.Metric.status == "active",
                        )
                    )
                ).all()
            )
            if set(body.metrics) - known:
                raise invalid_field("metrics", "Every metric must be published in the Data Space.")
        row = await session.scalar(
            select(monitor.ReportSchedule)
            .where(
                monitor.ReportSchedule.org_id == principal.org_id,
                monitor.ReportSchedule.data_space_id == data_space,
                monitor.ReportSchedule.owner_id == principal.user_id,
                monitor.ReportSchedule.kind == "digest",
                monitor.ReportSchedule.deleted_at.is_(None),
            )
            .order_by(monitor.ReportSchedule.created_at)
            .limit(1)
            .with_for_update()
        )
        created = row is None
        if row is None:
            row = monitor.ReportSchedule(
                id=uuid7(),
                org_id=principal.org_id,
                data_space_id=data_space,
                kind="digest",
                owner_id=principal.user_id,
                title=DIGEST_TITLE,
                cron=body.schedule or DIGEST_CRON,
                as_of_rule=DIGEST_AS_OF,
                formats=["html"],
                require_publish_check=True,
                status=status,
                created_by=principal.user_id,
                updated_by=principal.user_id,
            )
            session.add(row)
            await session.flush()
            await _replace_recipients(
                session,
                principal,
                data_space,
                row.id,
                [
                    RecipientIn(
                        principal_type="user", principal_id=principal.user_id, channels=["in_app"]
                    )
                ],
            )
        else:
            row.status = status
            if body.schedule is not None:
                row.cron = body.schedule
            row.updated_by, row.updated_at = principal.user_id, now()
        _schedule_event(
            session,
            principal,
            row,
            "report_schedule.created" if created else "report_schedule.updated",
            cron=row.cron,
            # No column holds the digest's metric list yet; the report agent takes it from here.
            metric_ids=body.metrics,
        )
        await session.flush()
        await session.refresh(row)
        return await _view(session, row)
