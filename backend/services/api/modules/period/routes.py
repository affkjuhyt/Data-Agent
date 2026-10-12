"""A2 group "Kỳ chốt" (P10): period close, reconciliation, filing, retroactive changes, comparison
(UC-10-02 to UC-10-05).

The api never snapshots, hashes or recomputes: :close moves the period to closing and the close
workflow (Temporal) extracts, hashes, reconciles and closes it; :publish records the filing and
the workflow freezes the numbers into published_result (BR-42). Every status change writes its
event to period.outbox in the same transaction (D1 rule 8); the inbox module opens the HITL
requests named in period.exception_requested.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy import func, literal, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import context, org
from libs.dataagent_db.models import period as pd
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Order,
    Page,
    PageRequest,
    encode_page_token,
    invalid_page_token,
    keyset,
    next_page,
    order_by,
    page_request,
)
from services.api.kernel.problems import FieldError, Problem
from services.api.modules.inbox.public import EVENT_TYPE as HITL_REQUESTED
from services.api.modules.inbox.public import hitl_requested
from services.api.modules.period.schemas import (
    Accepted,
    ApprovalStepPlan,
    CompareItem,
    CompareResult,
    CompareSide,
    DiffRow,
    ExceptionRequest,
    HitlRequestRef,
    PeriodCloseDetail,
    PeriodCloseView,
    PeriodVersion,
    PublishRequest,
    ReconciliationView,
    RetroChangeView,
    RetroDecision,
    SnapshotFileView,
)

router = APIRouter()
ROLE = "svc_period"

PathId = Annotated[uuid.UUID, Path(alias="id")]
POLL_AFTER_MS = 5_000
# A closed or filed period's numbers are frozen (BR-41, BR-42): writing to it is 423.
LOCKED = ("closed", "filed", "retro_detected")
# BR-132 / UC-10-02 5b: an exception needs the period's steward and then the controller (AC06).
EXCEPTION_STEPS = [
    ApprovalStepPlan(step=1, approver_role="steward"),
    ApprovalStepPlan(step=2, approver_role="compliance"),
]


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _emit(
    session: AsyncSession,
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    session.add(
        pd.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=_json(payload),
        )
    )


def _json(value: Any) -> Any:
    """The payload as JSON, at any depth: ids, dates and amounts as strings (amounts stay exact,
    which jsonable_encoder's float would not keep)."""
    if isinstance(value, uuid.UUID | datetime.date | decimal.Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json(v) for v in value]
    return value


def _invalid(field: str, detail: str) -> Problem:
    return Problem(
        422,
        "validation_failed",
        detail,
        errors=[FieldError(field=field, code="invalid", message=detail)],
    )


async def _period(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    period_id: uuid.UUID,
    *,
    lock: bool = False,
) -> pd.PeriodClose:
    query = select(pd.PeriodClose).where(
        pd.PeriodClose.id == period_id,
        pd.PeriodClose.org_id == principal.org_id,
        pd.PeriodClose.data_space_id == data_space,
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Period not found.")
    return row


# --- periods -------------------------------------------------------------------------------------


@router.get(
    "/v1/periods",
    response_model=Page[PeriodCloseView],
    **endpoint("GET", "/v1/periods", operation_id="listPeriods"),
)
async def list_periods(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    status: Literal["open", "closing", "close_failed", "closed", "filed", "retro_detected"]
    | None = None,
    from_: Annotated[datetime.date | None, Query(alias="from")] = None,
    to: datetime.date | None = None,
) -> Page[PeriodCloseView]:
    p = pd.PeriodClose
    query = select(p).where(p.org_id == principal.org_id, p.data_space_id == data_space)
    if status:
        query = query.where(p.status == status)
    if from_:
        query = query.where(p.business_date >= from_)
    if to:
        query = query.where(p.business_date <= to)
    if page.cursor:
        try:
            after = (
                datetime.date.fromisoformat(page.cursor["business_date"]),
                int(page.cursor["version"]),
                uuid.UUID(page.cursor["id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(p.business_date, p.version, p.id) < after)
    query = query.order_by(p.business_date.desc(), p.version.desc(), p.id.desc()).limit(
        page.page_size + 1
    )
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    last = rows[-1] if more else None
    token = (
        {
            "business_date": last.business_date.isoformat(),
            "version": last.version,
            "id": str(last.id),
        }
        if last
        else None
    )
    return Page(
        items=[PeriodCloseView.model_validate(r) for r in rows],
        next_page_token=encode_page_token(token),
    )


# Registered before /v1/periods/{id}, which would otherwise take "compare" for an id.
@router.get(
    "/v1/periods/compare",
    response_model=CompareResult,
    **endpoint("GET", "/v1/periods/compare", operation_id="comparePeriods"),
)
async def compare_periods(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    a: uuid.UUID,
    b: uuid.UUID,
    metric: str | None = None,
) -> CompareResult:
    """Compares the frozen numbers of two versions of the Data Space's periods (UC-10-05).

    A version made from a retroactive change has no published_result of its own until it is
    filed; its numbers are then the frozen ones of the version it came from, with the slices the
    retro check recomputed (retro_change.current_value). Comparing against live data or two
    points in time needs the query engine and is not served here."""
    if a == b:
        raise _invalid("b", "Pick two different versions.")
    async with transaction(ROLE, principal) as session:
        side_a = await _period(session, principal, data_space, a)
        side_b = await _period(session, principal, data_space, b)
        values_a, from_a = await _side_values(session, side_a)
        values_b, from_b = await _side_values(session, side_b)
        versions = {v for (v, _) in (*values_a, *values_b)}
        metrics: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
        if versions:
            for version_id, metric_id, code in (
                await session.execute(
                    select(context.MetricVersion.id, context.Metric.id, context.Metric.code)
                    .join(context.Metric, context.Metric.id == context.MetricVersion.metric_id)
                    .where(context.MetricVersion.id.in_(versions))
                )
            ).all():
                metrics[version_id] = (metric_id, code)
        retro_slices = {
            (version_id, _freeze(key))
            for version_id, key in (
                await session.execute(
                    select(pd.PublishedResult.metric_version_id, pd.PublishedResult.dimension_key)
                    .join(
                        pd.RetroChange,
                        pd.RetroChange.published_result_id == pd.PublishedResult.id,
                    )
                    .where(pd.RetroChange.new_period_close_id == side_b.id)
                )
            ).all()
        }
    items = _diff(values_a, values_b, metrics, retro_slices)
    if metric:
        items = [i for i in items if metric in (i.metric_code, str(i.metric_id))]
    return CompareResult(
        a=CompareSide(
            period_close_id=side_a.id,
            period_label=side_a.period_label,
            version=side_a.version,
            status=side_a.status,
            values_from=from_a,
        ),
        b=CompareSide(
            period_close_id=side_b.id,
            period_label=side_b.period_label,
            version=side_b.version,
            status=side_b.status,
            values_from=from_b,
        ),
        rows_id=side_b.id if retro_slices else None,
        items=items,
    )


def _freeze(dimension_key: Any) -> str:
    """dimension_key as a stable dict key."""
    if isinstance(dimension_key, dict):
        return repr(sorted(dimension_key.items()))
    return repr(dimension_key)


async def _side_values(
    session: AsyncSession, period: pd.PeriodClose
) -> tuple[dict[tuple[uuid.UUID, Any], decimal.Decimal], Literal["published", "retro"]]:
    """(metric_version_id, dimension_key) → value of one side; keys keep the raw dimension_key."""
    published = (
        await session.scalars(
            select(pd.PublishedResult).where(pd.PublishedResult.period_close_id == period.id)
        )
    ).all()
    if published:
        return {(r.metric_version_id, _Key(r.dimension_key)): r.value for r in published}, (
            "published"
        )
    origin = (
        await session.execute(
            select(pd.RetroChange, pd.PublishedResult)
            .join(pd.PublishedResult, pd.PublishedResult.id == pd.RetroChange.published_result_id)
            .where(pd.RetroChange.new_period_close_id == period.id)
        )
    ).all()
    if not origin:
        raise Problem(
            409,
            "invalid_state",
            f"Version {period.version} of {period.period_label} has no frozen numbers to compare.",
        )
    base = origin[0][0].period_close_id
    values = {
        (r.metric_version_id, _Key(r.dimension_key)): r.value
        for r in (
            await session.scalars(
                select(pd.PublishedResult).where(pd.PublishedResult.period_close_id == base)
            )
        ).all()
    }
    for change, result in origin:
        values[(result.metric_version_id, _Key(result.dimension_key))] = change.current_value
    return values, "retro"


class _Key:
    """A JSON dimension_key usable as part of a dict key."""

    __slots__ = ("raw", "frozen")

    def __init__(self, raw: Any) -> None:
        self.raw = raw
        self.frozen = _freeze(raw)

    def __hash__(self) -> int:
        return hash(self.frozen)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Key) and other.frozen == self.frozen


def _diff(
    values_a: dict[tuple[uuid.UUID, Any], decimal.Decimal],
    values_b: dict[tuple[uuid.UUID, Any], decimal.Decimal],
    metrics: dict[uuid.UUID, tuple[uuid.UUID, str]],
    retro: set[tuple[uuid.UUID, str]],
) -> list[CompareItem]:
    """Pairs the two sides by metric (not metric version) and slice; only differences are kept."""

    def by_metric(
        values: dict[tuple[uuid.UUID, Any], decimal.Decimal],
    ) -> dict[tuple[Any, str], tuple[uuid.UUID, Any, decimal.Decimal]]:
        out = {}
        for (version_id, key), value in values.items():
            metric_id = metrics.get(version_id, (version_id, ""))[0]
            out[(metric_id, key.frozen)] = (version_id, key.raw, value)
        return out

    left, right = by_metric(values_a), by_metric(values_b)
    items = []
    for slot in sorted(set(left) | set(right), key=str):
        a_entry, b_entry = left.get(slot), right.get(slot)
        version_a = a_entry[0] if a_entry else None
        version_b = b_entry[0] if b_entry else None
        value_a = a_entry[2] if a_entry else None
        value_b = b_entry[2] if b_entry else None
        if (
            value_a is not None
            and value_b is not None
            and value_a == value_b
            and version_a == version_b
        ):
            continue
        cause: Literal["retro", "definition", "data", "added", "removed"]
        if a_entry is None:
            cause = "added"
        elif b_entry is None:
            cause = "removed"
        elif version_a != version_b:
            cause = "definition"
        elif (version_b, slot[1]) in retro:
            cause = "retro"
        else:
            cause = "data"
        known = metrics.get(version_b or version_a or uuid.UUID(int=0))
        items.append(
            CompareItem(
                metric_id=known[0] if known else None,
                metric_code=known[1] if known else None,
                dimension_key=(b_entry or a_entry or (None, None, None))[1],
                a_metric_version_id=version_a,
                b_metric_version_id=version_b,
                a_value=value_a,
                b_value=value_b,
                delta=(value_b - value_a) if value_a is not None and value_b is not None else None,
                cause=cause,
            )
        )
    return items


@router.get(
    "/v1/periods/compare/{id}/rows",
    response_model=Page[DiffRow],
    **endpoint("GET", "/v1/periods/compare/{id}/rows", operation_id="getComparisonRows"),
)
async def comparison_rows(
    period_id: PathId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
) -> Page[DiffRow]:
    """The slices whose numbers changed in the version `id` (the compare result's rows_id),
    one per retroactive change. The source records behind a slice stay in the object store
    (retro_change.cause_rows_ref) and are read by the comparison worker, not by the api."""
    rc, pr = pd.RetroChange, pd.PublishedResult
    query = (
        select(rc, pr)
        .join(pr, pr.id == rc.published_result_id)
        .where(rc.new_period_close_id == period_id, rc.org_id == principal.org_id)
    )
    if page.cursor:
        try:
            after = uuid.UUID(page.cursor["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(rc.id > after)
    query = query.order_by(rc.id).limit(page.page_size + 1)
    async with transaction(ROLE, principal) as session:
        await _period(session, principal, data_space, period_id)
        rows = list((await session.execute(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    return Page(
        items=[
            DiffRow(
                retro_change_id=change.id,
                period_close_id=change.period_close_id,
                metric_version_id=result.metric_version_id,
                dimension_key=result.dimension_key,
                published_value=result.value,
                current_value=change.current_value,
                delta=change.delta,
                below_materiality=change.below_materiality,
                detected_at=change.detected_at,
            )
            for change, result in rows
        ],
        next_page_token=encode_page_token({"id": str(rows[-1][0].id)} if more else None),
    )


@router.get(
    "/v1/periods/{id}",
    response_model=PeriodCloseDetail,
    **endpoint("GET", "/v1/periods/{id}", operation_id="getPeriod"),
)
async def get_period(
    period_id: PathId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> PeriodCloseDetail:
    async with transaction(ROLE, principal) as session:
        row = await _period(session, principal, data_space, period_id)
        versions = (
            await session.scalars(
                select(pd.PeriodClose)
                .where(
                    pd.PeriodClose.org_id == principal.org_id,
                    pd.PeriodClose.data_space_id == data_space,
                    pd.PeriodClose.period_label == row.period_label,
                )
                .order_by(pd.PeriodClose.version)
            )
        ).all()
        files = (
            await session.scalars(
                select(pd.PeriodSnapshotFile)
                .where(pd.PeriodSnapshotFile.period_close_id == row.id)
                .order_by(pd.PeriodSnapshotFile.written_at, pd.PeriodSnapshotFile.id)
            )
        ).all()
        counts = (
            await session.execute(
                select(pd.PeriodReconciliation.status, func.count())
                .where(pd.PeriodReconciliation.period_close_id == row.id)
                .group_by(pd.PeriodReconciliation.status)
            )
        ).all()
        return PeriodCloseDetail(
            **PeriodCloseView.model_validate(row).model_dump(),
            versions=[PeriodVersion.model_validate(v) for v in versions],
            snapshot_files=[SnapshotFileView.model_validate(f) for f in files],
            reconciliation={status: int(n) for status, n in counts},
        )


@router.post(
    "/v1/periods/{id}:close",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/periods/{id}:close", operation_id="closePeriod"),
)
async def close_period(
    period_id: PathId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        row = await _period(session, principal, data_space, period_id, lock=True)
        if row.status in LOCKED:
            raise Problem(423, "period_locked", f"The period is already {row.status}.")
        if row.status == "closing":
            raise Problem(409, "invalid_state", "The period is being closed.")
        other = await session.scalar(
            select(pd.PeriodClose.period_label).where(
                pd.PeriodClose.org_id == principal.org_id,
                pd.PeriodClose.data_space_id == data_space,
                pd.PeriodClose.status == "closing",
                pd.PeriodClose.id != row.id,
            )
        )
        if other is not None:
            # UC-10-02 precondition, AC-10-02-02: one close at a time per Data Space; the partial
            # unique index on status 'closing' holds the race this check cannot.
            raise Problem(409, "invalid_state", f"Period {other} of this Data Space is closing.")
        pending = await session.scalar(
            select(func.count()).where(
                pd.PeriodReconciliation.period_close_id == row.id,
                pd.PeriodReconciliation.status == "exception_pending",
            )
        )
        if pending:
            # UC-10-02 5b: the exception decides; the period locks once both approvals are in.
            raise Problem(
                409, "invalid_state", "An exception of this period waits for its approvals."
            )
        previous = row.status
        row.status = "closing"
        # closed_by is whoever pressed Close (BR-132 keeps them off the exception approvals).
        row.closed_by = principal.user_id
        row.updated_by, row.updated_at = principal.user_id, _now()
        _emit(
            session,
            principal,
            "period_close",
            row.id,
            "period.close_requested",
            period_close_id=row.id,
            data_space_id=data_space,
            period_calendar_id=row.period_calendar_id,
            period_label=row.period_label,
            business_date=row.business_date,
            version=row.version,
            status=row.status,
            previous_status=previous,
            closed_by=principal.user_id,
        )
    response.headers["Location"] = f"/v1/periods/{row.id}"
    return Accepted(id=row.id, status="closing", poll_after_ms=POLL_AFTER_MS)


@router.get(
    "/v1/periods/{id}/reconciliation",
    response_model=Page[ReconciliationView],
    **endpoint("GET", "/v1/periods/{id}/reconciliation", operation_id="listPeriodReconciliation"),
)
async def list_reconciliation(
    period_id: PathId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order, Depends(order_by("item_code", "status", "created_at", default="item_code asc"))
    ],
) -> Page[ReconciliationView]:
    r = pd.PeriodReconciliation
    query = select(r).where(r.period_close_id == period_id, r.org_id == principal.org_id)
    query = keyset(
        query,
        columns={"item_code": r.item_code, "status": r.status, "created_at": r.created_at},
        tiebreak=r.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        await _period(session, principal, data_space, period_id)
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(
        items=[ReconciliationView.model_validate(row) for row in rows], next_page_token=token
    )


@router.post(
    "/v1/periods/{id}/exceptions",
    status_code=202,
    response_model=HitlRequestRef,
    **endpoint("POST", "/v1/periods/{id}/exceptions", operation_id="requestPeriodException"),
)
async def request_exception(
    body: ExceptionRequest,
    period_id: PathId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> HitlRequestRef:
    async with transaction(ROLE, principal) as session:
        period = await _period(session, principal, data_space, period_id, lock=True)
        if period.status in LOCKED:
            raise Problem(423, "period_locked", f"The period is already {period.status}.")
        if period.status != "close_failed":
            raise Problem(
                409, "invalid_state", "Exceptions are asked for after a close failed (UC-10-02 5a)."
            )
        item = await session.scalar(
            select(pd.PeriodReconciliation)
            .where(
                pd.PeriodReconciliation.period_close_id == period.id,
                pd.PeriodReconciliation.item_code == body.item_code,
            )
            .with_for_update()
        )
        if item is None:
            raise Problem(404, "not_found", "Reconciliation item not found.")
        if item.status != "mismatched":
            raise Problem(409, "invalid_state", f"The item is {item.status}, not mismatched.")
        # The inbox module creates the hitl_request under this id from the event below; the
        # reconciliation row points at it now so the exception is never asked twice.
        hitl_id = uuid7()
        item.status = "exception_pending"
        item.hitl_request_id = hitl_id
        excluded = sorted({u for u in (period.closed_by, principal.user_id) if u}, key=str)
        _emit(
            session,
            principal,
            "period_reconciliation",
            item.id,
            "period.exception_requested",
            period_close_id=period.id,
            period_reconciliation_id=item.id,
            hitl_request_id=hitl_id,
            data_space_id=data_space,
            item_code=item.item_code,
            delta=item.delta,
            tolerance=item.tolerance,
            status=item.status,
            risk="high",
            requested_by=principal.user_id,
            approval_steps=[s.model_dump() for s in EXCEPTION_STEPS],
            excluded_approver_ids=[str(u) for u in excluded],
            # The approvers decide on this justification; it is the request itself.
            reason=body.reason.strip(),
        )
        _emit(
            session,
            principal,
            "period_reconciliation",
            item.id,
            HITL_REQUESTED,
            **hitl_requested(
                hitl_request_id=hitl_id,
                data_space_id=data_space,
                kind="period_exception",
                risk="high",
                title=f"Ngoại lệ đối soát {item.item_code}",
                source_service="period",
                source_ref=item.id,
                created_by=principal.user_id,
                context={
                    "period_close_id": str(period.id),
                    "item_code": item.item_code,
                    "delta": str(item.delta),
                    "tolerance": str(item.tolerance),
                    "reason": body.reason.strip(),
                },
                approval_steps=[s.approver_role for s in EXCEPTION_STEPS],
                excluded_approver_ids=excluded,
            ),
        )
    response.headers["Location"] = f"/v1/hitl-requests/{hitl_id}"
    return HitlRequestRef(
        id=hitl_id,
        data_space_id=data_space,
        kind="period_exception",
        risk="high",
        # What the inbox opens it as (governance.hitl_request.status).
        status="open",
        source_service="period",
        source_ref=item.id,
        approval_steps=EXCEPTION_STEPS,
        excluded_approver_ids=excluded,
    )


async def _holds_compliance(session: AsyncSession, principal: Principal, user: uuid.UUID) -> bool:
    """The user is active and holds `compliance` through an org-wide role still valid (AC06)."""
    found = await session.scalar(
        select(org.UserRole.id)
        .join(org.Role, org.Role.id == org.UserRole.role_id)
        .join(org.AppUser, org.AppUser.id == org.UserRole.user_id)
        .where(
            org.UserRole.org_id == principal.org_id,
            org.UserRole.user_id == user,
            org.AppUser.status == "active",
            # role.capabilities is the generic ARRAY type, which has no contains().
            literal("compliance") == func.any(org.Role.capabilities),
            or_(org.UserRole.valid_until.is_(None), org.UserRole.valid_until > _now()),
        )
        .limit(1)
    )
    return found is not None


@router.post(
    "/v1/periods/{id}:publish",
    response_model=PeriodCloseView,
    **endpoint("POST", "/v1/periods/{id}:publish", operation_id="publishPeriod"),
)
async def publish_period(
    body: PublishRequest,
    period_id: PathId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> PeriodCloseView:
    seen: set[uuid.UUID] = set()
    for i, override in enumerate(body.overrides):
        if override.metric_version_id in seen:
            raise _invalid(f"overrides[{i}].metric_version_id", "One override per metric.")
        seen.add(override.metric_version_id)
        if not override.override_reason.strip():
            raise Problem(
                422, "business_rule_violated", "A manual number needs its reason (BR-133)."
            )
    async with transaction(ROLE, principal) as session:
        row = await _period(session, principal, data_space, period_id, lock=True)
        if row.status != "closed":
            # UC-10-03 precondition, AC-10-03-04: only a closed period is filed, and only once.
            raise Problem(409, "invalid_state", f"The period is {row.status}, not closed.")
        if row.filed_report_code is not None:
            raise Problem(409, "invalid_state", "The filing of this period is being recorded.")
        if row.period_calendar_id is not None:
            codes = await session.scalar(
                select(pd.PeriodCalendar.report_codes).where(
                    pd.PeriodCalendar.id == row.period_calendar_id
                )
            )
            if codes and body.report_code not in codes:
                raise _invalid("report_code", "The report is not one this period files.")
        if body.overrides:
            certified = set(
                (
                    await session.scalars(
                        select(context.MetricVersion.id)
                        .join(context.Metric, context.Metric.id == context.MetricVersion.metric_id)
                        .where(
                            context.MetricVersion.id.in_(seen),
                            context.MetricVersion.org_id == principal.org_id,
                            # A metric of another Data Space is not one this period reports.
                            context.Metric.data_space_id == data_space,
                            context.MetricVersion.status == "certified",
                        )
                    )
                ).all()
            )
            uncertified = seen - certified
            if uncertified:
                # UC-10-03 E1: a number is frozen only under a certified definition.
                raise Problem(
                    422,
                    "business_rule_violated",
                    "No certified definition for metric version(s) "
                    + ", ".join(sorted(str(u) for u in uncertified))
                    + " (UC-10-03 E1).",
                )
        for i, override in enumerate(body.overrides):
            approver = override.approver_id
            if approver == principal.user_id or not await _holds_compliance(
                session, principal, approver
            ):
                # AC-10-03-06: the filer does not approve their own manual number.
                raise Problem(
                    422,
                    "business_rule_violated",
                    f"overrides[{i}]: approver_id must be another person holding compliance "
                    "(BR-133).",
                )
        approval_ids = {o.metric_version_id: uuid7() for o in body.overrides}
        row.filed_report_code = body.report_code
        row.filed_at = body.filed_at
        row.updated_by, row.updated_at = principal.user_id, _now()
        # The workflow computes each metric of the report on the snapshot, writes published_result
        # (insert-only, BR-42), moves the period to filed and emits period.published.
        _emit(
            session,
            principal,
            "period_close",
            row.id,
            "period.publish_requested",
            period_close_id=row.id,
            data_space_id=data_space,
            status=row.status,
            report_code=body.report_code,
            filed_at=body.filed_at,
            requested_by=principal.user_id,
            overrides=[
                {
                    "metric_version_id": str(o.metric_version_id),
                    "reported_value": str(o.reported_value),
                    "override_reason": o.override_reason.strip(),
                    # Frozen only once this approval is decided: published_result.approved_by
                    # is the hitl request's resolved_by, never a value from the filer (BR-133).
                    "approval_id": str(approval_ids[o.metric_version_id]),
                }
                for o in body.overrides
            ],
        )
        for o in body.overrides:
            # The inbox opens a period_exception request for the named controller from this
            # event, with this id; a decision by anyone else, or the filer, does not count.
            _emit(
                session,
                principal,
                "period_close",
                row.id,
                "period.override_approval_requested",
                period_close_id=row.id,
                data_space_id=data_space,
                hitl_request_id=approval_ids[o.metric_version_id],
                hitl_kind="period_exception",
                assignee_id=o.approver_id,
                excluded_approver_ids=[principal.user_id],
                metric_version_id=o.metric_version_id,
                reported_value=str(o.reported_value),
                requested_by=principal.user_id,
            )
            _emit(
                session,
                principal,
                "period_close",
                row.id,
                HITL_REQUESTED,
                **hitl_requested(
                    hitl_request_id=approval_ids[o.metric_version_id],
                    data_space_id=data_space,
                    kind="period_exception",
                    risk="high",
                    title="Duyệt số nộp khác số tính",
                    source_service="period",
                    source_ref=row.id,
                    created_by=principal.user_id,
                    context={
                        "period_close_id": str(row.id),
                        "metric_version_id": str(o.metric_version_id),
                        "reported_value": str(o.reported_value),
                        "override_reason": o.override_reason.strip(),
                    },
                    assignee_id=o.approver_id,
                    excluded_approver_ids=[principal.user_id],
                ),
            )
        await session.flush()
        return PeriodCloseView.model_validate(row)


# --- retroactive changes -------------------------------------------------------------------------


@router.get(
    "/v1/periods/{id}/retro-changes",
    response_model=Page[RetroChangeView],
    **endpoint("GET", "/v1/periods/{id}/retro-changes", operation_id="listRetroChanges"),
)
async def list_retro_changes(
    period_id: PathId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[Order, Depends(order_by("detected_at", default="detected_at desc"))],
) -> Page[RetroChangeView]:
    rc = pd.RetroChange
    query = select(rc).where(rc.period_close_id == period_id, rc.org_id == principal.org_id)
    query = keyset(
        query, columns={"detected_at": rc.detected_at}, tiebreak=rc.id, order=order, page=page
    )
    async with transaction(ROLE, principal) as session:
        await _period(session, principal, data_space, period_id)
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[RetroChangeView.model_validate(r) for r in rows], next_page_token=token)


@router.post(
    "/v1/retro-changes/{id}:decide",
    response_model=RetroChangeView,
    **endpoint("POST", "/v1/retro-changes/{id}:decide", operation_id="decideRetroChange"),
)
async def decide_retro_change(
    body: RetroDecision,
    change_id: PathId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> RetroChangeView:
    note = body.decision_note.strip()
    if not note:
        raise _invalid("decision_note", "A decision carries its reason.")
    async with transaction(ROLE, principal) as session:
        change = await session.scalar(
            select(pd.RetroChange)
            .where(pd.RetroChange.id == change_id, pd.RetroChange.org_id == principal.org_id)
            .with_for_update()
        )
        if change is None:
            raise Problem(404, "not_found", "Retroactive change not found.")
        period = await _period(session, principal, data_space, change.period_close_id, lock=True)
        if change.decision is not None:
            raise Problem(409, "invalid_state", f"Already decided: {change.decision}.")
        stamp = _now()
        change.decision, change.decision_note = body.decision, note
        new_version: pd.PeriodClose | None = None
        if body.decision == "new_version":
            # One version + 1 per period, whichever of its changes asks first (UC-10-04 5a).
            existing = await session.scalar(
                select(pd.RetroChange.new_period_close_id).where(
                    pd.RetroChange.period_close_id == period.id,
                    pd.RetroChange.new_period_close_id.is_not(None),
                )
            )
            if existing is not None:
                change.new_period_close_id = existing
            else:
                closing = await session.scalar(
                    select(pd.PeriodClose.period_label).where(
                        pd.PeriodClose.org_id == principal.org_id,
                        pd.PeriodClose.data_space_id == data_space,
                        pd.PeriodClose.status == "closing",
                    )
                )
                if closing is not None:
                    raise Problem(
                        409, "invalid_state", f"Period {closing} of this Data Space is closing."
                    )
                latest = await session.scalar(
                    select(func.max(pd.PeriodClose.version)).where(
                        pd.PeriodClose.org_id == principal.org_id,
                        pd.PeriodClose.data_space_id == data_space,
                        pd.PeriodClose.period_label == period.period_label,
                    )
                )
                # BR-43: version 1 is never touched; the new version is snapshotted from current
                # data by the close workflow, as a close of its own.
                new_version = pd.PeriodClose(
                    id=uuid7(),
                    org_id=principal.org_id,
                    data_space_id=data_space,
                    period_calendar_id=period.period_calendar_id,
                    period_label=period.period_label,
                    business_date=period.business_date,
                    version=int(latest or period.version) + 1,
                    status="closing",
                    closed_by=principal.user_id,
                    created_by=principal.user_id,
                    updated_by=principal.user_id,
                    created_at=stamp,
                    updated_at=stamp,
                )
                session.add(new_version)
                await session.flush()
                change.new_period_close_id = new_version.id
        # BR-134: a material change is always reported to the controller, whatever the decision.
        notify_compliance = not change.below_materiality and not change.compliance_notified
        if notify_compliance:
            change.compliance_notified = True
        _emit(
            session,
            principal,
            "retro_change",
            change.id,
            "retro_change.decided",
            retro_change_id=change.id,
            period_close_id=period.id,
            data_space_id=data_space,
            decision=change.decision,
            new_period_close_id=change.new_period_close_id,
            notify_compliance=notify_compliance,
            decided_by=principal.user_id,
        )
        if new_version is not None:
            _emit(
                session,
                principal,
                "period_close",
                new_version.id,
                "period.close_requested",
                period_close_id=new_version.id,
                data_space_id=data_space,
                period_calendar_id=new_version.period_calendar_id,
                period_label=new_version.period_label,
                business_date=new_version.business_date,
                version=new_version.version,
                status=new_version.status,
                previous_status=None,
                closed_by=principal.user_id,
                retro_of_period_close_id=period.id,
            )
        await session.flush()
        return RetroChangeView.model_validate(change)
