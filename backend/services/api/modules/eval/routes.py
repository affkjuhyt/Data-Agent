"""API-04 evaluation and Error Book routes (UC-07-06, UC-07-14).

The eval worker executes runs and moves entries to regression_added (BR-179); these routes
record requests and decisions, and each status change writes its event to eval.outbox in the
same transaction. The trigger of eval.error_book_entry numbers entries, guards the status
machine and emits eval.error_book.opened itself.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Query, Response
from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import context, org
from libs.dataagent_db.models import eval as evaluation
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Order,
    Page,
    PageRequest,
    decode_page_token,
    encode_page_token,
    invalid_page_token,
    keyset,
    next_page,
    order_by,
    page_request,
)
from services.api.kernel.problems import Problem
from services.api.modules.eval.schemas import (
    AddRegression,
    BaselineComparison,
    Dismiss,
    ErrorBookCreate,
    ErrorBookDetail,
    ErrorBookPatch,
    ErrorBookView,
    EvalFailure,
    EvalItemCreate,
    EvalItemView,
    EvalResultView,
    EvalRunAccepted,
    EvalRunCreate,
    EvalRunDetail,
    EvalRunView,
    EvalSetDetail,
    EvalSetView,
    MarkFixed,
    ProposeToPack,
)

router = APIRouter()
ROLE = "svc_eval"
POLL_AFTER_MS = 2_000
Entry = evaluation.ErrorBookEntry


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _event(
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> evaluation.Outbox:
    return evaluation.Outbox(
        id=uuid7(),
        org_id=principal.org_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in payload.items()},
    )


async def _eval_set(
    session: AsyncSession, eval_set_id: uuid.UUID, principal: Principal, data_space: uuid.UUID
) -> evaluation.EvalSet:
    eval_set = await session.scalar(
        select(evaluation.EvalSet).where(
            evaluation.EvalSet.id == eval_set_id,
            evaluation.EvalSet.org_id == principal.org_id,
            evaluation.EvalSet.data_space_id == data_space,
        )
    )
    if eval_set is None:
        raise Problem(404, "not_found", "Eval set not found.")
    if eval_set.status != "active":
        raise Problem(409, "invalid_state", "The eval set is archived.")
    return eval_set


# Eval sets (reads, UC-07-06, UC-13-04)

# The runs GET /v1/eval-sets/{id} lists; older ones stay readable at GET /v1/eval-runs/{id}.
RECENT_RUNS = 20


def _readable_sets(principal: Principal, data_space: uuid.UUID) -> tuple[Any, ...]:
    """The Data Space's sets and the organisation-wide ones (pack sets have no Data Space)."""
    return (
        evaluation.EvalSet.org_id == principal.org_id,
        or_(
            evaluation.EvalSet.data_space_id == data_space,
            evaluation.EvalSet.data_space_id.is_(None),
        ),
    )


async def _set_views(session: AsyncSession, rows: list[evaluation.EvalSet]) -> list[EvalSetView]:
    ids = [r.id for r in rows]
    if not ids:
        return []
    # eval_run has no Data Space: a run of an organisation-wide set (a pack's) ran on some Data
    # Space's data, maybe another's, so only runs of the caller's own Data Space's sets are shown.
    own = [r.id for r in rows if r.data_space_id is not None]
    counts = dict(
        (
            await session.execute(
                select(evaluation.EvalItem.eval_set_id, func.count())
                .where(
                    evaluation.EvalItem.eval_set_id.in_(ids),
                    evaluation.EvalItem.status == "active",
                )
                .group_by(evaluation.EvalItem.eval_set_id)
            )
        )
        .tuples()
        .all()
    )
    latest = {
        run.eval_set_id: run
        for run in (
            await session.scalars(
                select(evaluation.EvalRun)
                .where(evaluation.EvalRun.eval_set_id.in_(own))
                .order_by(
                    evaluation.EvalRun.eval_set_id,
                    evaluation.EvalRun.started_at.desc(),
                    evaluation.EvalRun.id.desc(),
                )
                .distinct(evaluation.EvalRun.eval_set_id)
            )
        ).all()
    }
    views = []
    for row in rows:
        view = EvalSetView.model_validate(row, from_attributes=True)
        view.item_count = int(counts.get(row.id, 0))
        run = latest.get(row.id)
        view.latest_run = EvalRunView.model_validate(run, from_attributes=True) if run else None
        views.append(view)
    return views


@router.get(
    "/v1/eval-sets",
    response_model=Page[EvalSetView],
    **endpoint("GET", "/v1/eval-sets", operation_id="listEvalSets"),
)
async def list_eval_sets(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(order_by("created_at", "updated_at", "name", "status", default="created_at desc")),
    ],
    subject_type: Annotated[str | None, Query(max_length=40)] = None,
    subject_id: uuid.UUID | None = None,
    status: Annotated[str | None, Query(max_length=40)] = None,
) -> Page[EvalSetView]:
    es = evaluation.EvalSet
    query = select(es).where(*_readable_sets(principal, data_space))
    for column, value in ((es.subject_type, subject_type), (es.subject_id, subject_id)):
        if value is not None:
            query = query.where(column == value)
    if status is not None:
        query = query.where(es.status == status)
    query = keyset(
        query,
        columns={
            "created_at": es.created_at,
            "updated_at": es.updated_at,
            "name": es.name,
            "status": es.status,
        },
        tiebreak=es.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
        rows, token = next_page(rows, order=order, page=page)
        items = await _set_views(session, rows)
    return Page(items=items, next_page_token=token)


@router.get(
    "/v1/eval-sets/{id}",
    response_model=EvalSetDetail,
    **endpoint("GET", "/v1/eval-sets/{id}", operation_id="getEvalSet"),
)
async def get_eval_set(
    eval_set_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> EvalSetDetail:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(evaluation.EvalSet).where(
                evaluation.EvalSet.id == eval_set_id, *_readable_sets(principal, data_space)
            )
        )
        if row is None:
            raise Problem(404, "not_found", "Eval set not found.")
        (view,) = await _set_views(session, [row])
        # Runs of an organisation-wide set are not told apart by Data Space (see _set_views).
        runs = (
            (
                await session.scalars(
                    select(evaluation.EvalRun)
                    .where(evaluation.EvalRun.eval_set_id == row.id)
                    .order_by(evaluation.EvalRun.started_at.desc(), evaluation.EvalRun.id.desc())
                    .limit(RECENT_RUNS)
                )
            ).all()
            if row.data_space_id is not None
            else []
        )
        judged = next((r for r in runs if r.status == "completed"), None)
        failures: list[EvalFailure] = []
        if judged is not None:
            missed = (
                await session.execute(
                    select(evaluation.EvalResult, evaluation.EvalItem)
                    .join(
                        evaluation.EvalItem,
                        evaluation.EvalItem.id == evaluation.EvalResult.eval_item_id,
                    )
                    .where(
                        evaluation.EvalResult.eval_run_id == judged.id,
                        evaluation.EvalResult.passed.is_(False),
                    )
                    .order_by(evaluation.EvalResult.created_at, evaluation.EvalResult.id)
                )
            ).all()
            failures = [
                EvalFailure(
                    item=EvalItemView.model_validate(item, from_attributes=True),
                    result=EvalResultView.model_validate(result, from_attributes=True),
                )
                for result, item in missed
            ]
        return EvalSetDetail(
            **view.model_dump(),
            runs=[EvalRunView.model_validate(r, from_attributes=True) for r in runs],
            failures_run_id=judged.id if judged else None,
            failures=failures,
        )


@router.post(
    "/v1/eval-sets/{id}/items",
    response_model=EvalItemView,
    status_code=201,
    **endpoint("POST", "/v1/eval-sets/{id}/items", operation_id="createEvalItem"),
)
async def create_eval_item(
    body: EvalItemCreate,
    eval_set_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> EvalItemView:
    async with transaction(ROLE, principal) as session:
        eval_set = await _eval_set(session, eval_set_id, principal, data_space)
        item = evaluation.EvalItem(
            id=uuid7(),
            org_id=principal.org_id,
            eval_set_id=eval_set.id,
            created_by=principal.user_id,
            updated_by=principal.user_id,
            **body.model_dump(),
        )
        session.add(item)
        await session.flush()
        await session.refresh(item)
        return EvalItemView.model_validate(item, from_attributes=True)


@router.post(
    "/v1/eval-sets/{id}/runs",
    response_model=EvalRunAccepted,
    status_code=202,
    **endpoint("POST", "/v1/eval-sets/{id}/runs", operation_id="runEvalSet"),
)
async def run_eval_set(
    eval_set_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
    body: Annotated[EvalRunCreate, Body()] = EvalRunCreate(),
) -> EvalRunAccepted:
    async with transaction(ROLE, principal) as session:
        eval_set = await _eval_set(session, eval_set_id, principal, data_space)
        count = await session.scalar(
            select(func.count())
            .select_from(evaluation.EvalItem)
            .where(
                evaluation.EvalItem.eval_set_id == eval_set.id,
                evaluation.EvalItem.status == "active",
            )
        )
        baseline = await session.scalar(
            select(evaluation.EvalRun.id)
            .where(
                evaluation.EvalRun.eval_set_id == eval_set.id,
                evaluation.EvalRun.status == "completed",
            )
            .order_by(evaluation.EvalRun.started_at.desc())
            .limit(1)
        )
        run = evaluation.EvalRun(
            id=uuid7(),
            org_id=principal.org_id,
            eval_set_id=eval_set.id,
            subject_version=body.subject_version,
            trigger="manual",
            item_count=int(count or 0),
            baseline_run_id=baseline,
            status="running",
        )
        session.add(run)
        # The eval worker picks the run up from this event; nothing executes in the api.
        session.add(
            _event(
                principal,
                "eval_run",
                run.id,
                "eval_run.requested",
                eval_run_id=run.id,
                eval_set_id=eval_set.id,
                data_space_id=data_space,
                subject_version=run.subject_version,
                trigger=run.trigger,
                status=run.status,
                requested_by=principal.user_id,
            )
        )
        await session.flush()
    response.headers["Location"] = f"/v1/eval-runs/{run.id}"
    return EvalRunAccepted(id=run.id, status="running", poll_after_ms=POLL_AFTER_MS)


@router.get(
    "/v1/eval-runs/{id}",
    response_model=EvalRunDetail,
    **endpoint("GET", "/v1/eval-runs/{id}", operation_id="getEvalRun"),
)
async def get_eval_run(
    run_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> EvalRunDetail:
    async with transaction(ROLE, principal) as session:
        run = await session.scalar(
            select(evaluation.EvalRun)
            .join(evaluation.EvalSet, evaluation.EvalSet.id == evaluation.EvalRun.eval_set_id)
            .where(
                evaluation.EvalRun.id == run_id,
                evaluation.EvalRun.org_id == principal.org_id,
                evaluation.EvalSet.data_space_id == data_space,
            )
        )
        if run is None:
            raise Problem(404, "not_found", "Eval run not found.")
        results = (
            await session.scalars(
                select(evaluation.EvalResult)
                .where(evaluation.EvalResult.eval_run_id == run.id)
                .order_by(evaluation.EvalResult.created_at, evaluation.EvalResult.id)
            )
        ).all()
        baseline = None
        if run.baseline_run_id is not None:
            base = await session.scalar(
                select(evaluation.EvalRun).where(evaluation.EvalRun.id == run.baseline_run_id)
            )
            if base is not None:
                baseline = BaselineComparison(
                    baseline_run_id=base.id,
                    passed=base.passed,
                    failed=base.failed,
                    pass_rate=base.pass_rate,
                    passed_delta=run.passed - base.passed,
                )
        return EvalRunDetail(
            **EvalRunView.model_validate(run, from_attributes=True).model_dump(),
            results=[EvalResultView.model_validate(r, from_attributes=True) for r in results],
            baseline=baseline,
        )


# Error Book (UC-07-14, BR-179)


@router.get(
    "/v1/error-book-entries",
    response_model=Page[ErrorBookView],
    **endpoint("GET", "/v1/error-book-entries", operation_id="listErrorBookEntries"),
)
async def list_error_book(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    status: str | None = None,
    error_kind: str | None = None,
    source_kind: str | None = None,
    data_product_id: uuid.UUID | None = None,
    metric_code: Annotated[str | None, Query(description="detail.metric_code of the entry")] = None,
    assignee_id: uuid.UUID | None = None,
    cursor: Annotated[
        str | None, Query(max_length=2048, description="Same as page_token (A3)")
    ] = None,
) -> Page[ErrorBookView]:
    query = select(Entry).where(Entry.org_id == principal.org_id, Entry.data_space_id == data_space)
    for column, value in (
        (Entry.status, status),
        (Entry.error_kind, error_kind),
        (Entry.source_kind, source_kind),
        (Entry.data_product_id, data_product_id),
        (Entry.assignee_id, assignee_id),
    ):
        if value is not None:
            query = query.where(column == value)
    if metric_code is not None:
        query = query.where(Entry.detail["metric_code"].astext == metric_code)
    position = decode_page_token(cursor) if cursor else page.cursor
    if position:
        try:
            after = (
                datetime.datetime.fromisoformat(position["created_at"]),
                uuid.UUID(position["id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(Entry.created_at, Entry.id) < after)
    query = query.order_by(Entry.created_at.desc(), Entry.id.desc()).limit(page.page_size + 1)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    token = (
        {"created_at": rows[-1].created_at.isoformat(), "id": str(rows[-1].id)} if more else None
    )
    return Page(
        items=[ErrorBookView.model_validate(row, from_attributes=True) for row in rows],
        next_page_token=encode_page_token(token),
    )


async def _default_assignee(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    data_product_id: uuid.UUID | None,
) -> uuid.UUID | None:
    """The data product's steward, else the Data Space's (D4.9 note on error_book_entry)."""
    if data_product_id is not None:
        product = await session.scalar(
            select(context.DataProduct).where(
                context.DataProduct.id == data_product_id,
                context.DataProduct.org_id == principal.org_id,
                context.DataProduct.data_space_id == data_space,
            )
        )
        if product is None:
            raise Problem(
                422,
                "validation_failed",
                "data_product_id is not a data product of this Data Space.",
            )
        return product.steward_id
    steward: uuid.UUID | None = await session.scalar(
        select(org.DataSpace.steward_id).where(
            org.DataSpace.id == data_space, org.DataSpace.org_id == principal.org_id
        )
    )
    return steward


_CREATE_ENTRY = endpoint("POST", "/v1/error-book-entries", operation_id="createErrorBookEntry")
_CREATE_ENTRY["responses"] = {
    **_CREATE_ENTRY["responses"],
    200: {"model": ErrorBookView, "description": "The entry of the same source already exists"},
}


@router.post(
    "/v1/error-book-entries", response_model=ErrorBookView, status_code=201, **_CREATE_ENTRY
)
async def create_error_book(
    body: ErrorBookCreate,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> ErrorBookView:
    if body.data_space_id != data_space:
        raise Problem(
            422, "validation_failed", "data_space_id must be the Data Space of X-Data-Space."
        )
    async with transaction(ROLE, principal) as session:
        # One error, one entry (BR-179): the same feedback, accuracy log or replay returns the entry.
        for column, value in (
            (Entry.feedback_id, body.feedback_id),
            (Entry.accuracy_log_id, body.accuracy_log_id),
            (Entry.replay_id, body.replay_id),
        ):
            if value is None:
                continue
            existing = await session.scalar(
                select(Entry).where(Entry.org_id == principal.org_id, column == value)
            )
            if existing is not None:
                if existing.data_space_id != data_space:
                    raise Problem(
                        409, "conflict", "This source already has an entry in another Data Space."
                    )
                response.status_code = 200
                return ErrorBookView.model_validate(existing, from_attributes=True)
        row = Entry(
            id=uuid7(),
            org_id=principal.org_id,
            # Placeholder: the BEFORE INSERT trigger numbers the entry EB-nnnn per organisation.
            entry_no="",
            assignee_id=await _default_assignee(
                session, principal, data_space, body.data_product_id
            ),
            created_by=principal.user_id,
            updated_by=principal.user_id,
            **body.model_dump(),
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        return ErrorBookView.model_validate(row, from_attributes=True)


async def _entry(
    session: AsyncSession,
    entry_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> evaluation.ErrorBookEntry:
    query = select(Entry).where(
        Entry.id == entry_id, Entry.org_id == principal.org_id, Entry.data_space_id == data_space
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Error Book entry not found.")
    return row


@router.get(
    "/v1/error-book-entries/{id}",
    response_model=ErrorBookDetail,
    **endpoint("GET", "/v1/error-book-entries/{id}", operation_id="getErrorBookEntry"),
)
async def get_error_book(
    entry_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> ErrorBookDetail:
    async with transaction(ROLE, principal) as session:
        row = await _entry(session, entry_id, principal, data_space)
        item = latest = None
        if row.eval_item_id is not None:
            item = await session.scalar(
                select(evaluation.EvalItem).where(evaluation.EvalItem.id == row.eval_item_id)
            )
            latest = await session.scalar(
                select(evaluation.EvalResult)
                .join(
                    evaluation.EvalRun, evaluation.EvalRun.id == evaluation.EvalResult.eval_run_id
                )
                .where(evaluation.EvalResult.eval_item_id == row.eval_item_id)
                .order_by(evaluation.EvalRun.started_at.desc())
                .limit(1)
            )
        return ErrorBookDetail(
            **ErrorBookView.model_validate(row, from_attributes=True).model_dump(),
            eval_item=EvalItemView.model_validate(item, from_attributes=True) if item else None,
            latest_result=EvalResultView.model_validate(latest, from_attributes=True)
            if latest
            else None,
        )


@router.patch(
    "/v1/error-book-entries/{id}",
    response_model=ErrorBookView,
    **endpoint("PATCH", "/v1/error-book-entries/{id}", operation_id="patchErrorBookEntry"),
)
async def patch_error_book(
    body: ErrorBookPatch,
    entry_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> ErrorBookView:
    async with transaction(ROLE, principal) as session:
        row = await _entry(session, entry_id, principal, data_space, lock=True)
        if row.status == "dismissed":
            raise Problem(409, "conflict", "The entry is dismissed.")
        for key, value in body.model_dump(exclude_unset=True).items():
            # title and severity are NOT NULL: an explicit null leaves them as they are.
            if value is None and key in ("title", "severity"):
                continue
            setattr(row, key, value)
        row.updated_by, row.updated_at = principal.user_id, _now()
        await session.flush()
        return ErrorBookView.model_validate(row, from_attributes=True)


async def _regression_set(
    session: AsyncSession, row: evaluation.ErrorBookEntry, eval_set_id: uuid.UUID | None
) -> evaluation.EvalSet | None:
    """The eval set the regression case goes to: the one named, else the data product's, else the
    Data Space's active context set."""
    if eval_set_id is None and row.data_product_id is not None:
        eval_set_id = await session.scalar(
            select(context.DataProduct.eval_set_id).where(
                context.DataProduct.id == row.data_product_id
            )
        )
    query = select(evaluation.EvalSet).where(
        evaluation.EvalSet.data_space_id == row.data_space_id, evaluation.EvalSet.status == "active"
    )
    if eval_set_id is not None:
        query = query.where(evaluation.EvalSet.id == eval_set_id)
    else:
        query = query.where(evaluation.EvalSet.subject_type == "context").order_by(
            evaluation.EvalSet.created_at
        )
    eval_set: evaluation.EvalSet | None = await session.scalar(query.limit(1))
    return eval_set


def _regression_item(
    principal: Principal,
    row: evaluation.ErrorBookEntry,
    eval_set: evaluation.EvalSet,
    case: dict[str, Any],
) -> evaluation.EvalItem:
    return evaluation.EvalItem(
        id=uuid7(),
        org_id=principal.org_id,
        eval_set_id=eval_set.id,
        origin="error_book",
        origin_ref=row.id,
        created_by=principal.user_id,
        updated_by=principal.user_id,
        **case,
    )


def _case_from_detail(row: evaluation.ErrorBookEntry) -> dict[str, Any] | None:
    """The masked question and expected answer the entry recorded (UC-07-14 step 5), if any."""
    detail = row.detail if isinstance(row.detail, dict) else {}
    if not isinstance(detail.get("input"), dict) or not isinstance(detail.get("expected"), dict):
        return None
    as_of = detail.get("as_of")
    return {
        "input": detail["input"],
        "expected": detail["expected"],
        "tolerance": detail.get("tolerance"),
        "as_of": datetime.date.fromisoformat(as_of) if isinstance(as_of, str) else None,
    }


@router.post(
    "/v1/error-book-entries/{id}:mark-fixed",
    response_model=ErrorBookView,
    **endpoint("POST", "/v1/error-book-entries/{id}:mark-fixed", operation_id="markErrorBookFixed"),
)
async def mark_fixed(
    body: MarkFixed,
    entry_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> ErrorBookView:
    async with transaction(ROLE, principal) as session:
        row = await _entry(session, entry_id, principal, data_space, lock=True)
        if row.status != "open":
            # open → fixed only; the trigger lets an unchanged status through, so fixed → fixed is ours.
            raise Problem(409, "invalid_state", f"The entry is {row.status}, not open.")
        now = _now()
        row.status, row.fix_kind, row.fix_proposal_id, row.fix_ref = (
            "fixed",
            body.fix_kind,
            body.fix_proposal_id,
            body.fix_ref,
        )
        row.fixed_by, row.fixed_at = principal.user_id, now
        row.assignee_id = row.assignee_id or principal.user_id
        note = (body.note or "").strip()
        if note:
            row.detail = {**(row.detail or {}), "fix_note": note}
        row.updated_by, row.updated_at = principal.user_id, now
        # The CHECKs refuse a fix without error kind, root cause or a fix reference (422, BR-179).
        await session.flush()
        # Step 5: the regression case is added at once when the entry carries it.
        case = _case_from_detail(row) if row.eval_item_id is None else None
        eval_set = await _regression_set(session, row, None) if case else None
        if case and eval_set:
            item = _regression_item(principal, row, eval_set, case)
            session.add(item)
            await session.flush()
            row.eval_item_id = item.id
        session.add(
            _event(
                principal,
                "error_book_entry",
                row.id,
                "error_book_entry.fixed",
                entry_id=row.id,
                entry_no=row.entry_no,
                data_space_id=row.data_space_id,
                status=row.status,
                error_kind=row.error_kind,
                fix_kind=row.fix_kind,
                fix_proposal_id=row.fix_proposal_id,
                eval_item_id=row.eval_item_id,
            )
        )
        await session.flush()
        return ErrorBookView.model_validate(row, from_attributes=True)


@router.post(
    "/v1/error-book-entries/{id}:add-regression",
    response_model=ErrorBookView,
    **endpoint(
        "POST", "/v1/error-book-entries/{id}:add-regression", operation_id="addErrorBookRegression"
    ),
)
async def add_regression(
    entry_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    body: Annotated[AddRegression, Body()] = AddRegression(),
) -> ErrorBookView:
    async with transaction(ROLE, principal) as session:
        row = await _entry(session, entry_id, principal, data_space, lock=True)
        if row.status != "fixed":
            raise Problem(
                409, "conflict", f"The entry is {row.status}; a regression case follows the fix."
            )
        if row.eval_item_id is not None:
            raise Problem(409, "conflict", "The entry already has a regression case.")
        recorded = _case_from_detail(row) or {}
        input_ = body.input if body.input is not None else recorded.get("input")
        expected = body.expected if body.expected is not None else recorded.get("expected")
        if input_ is None or expected is None:
            raise Problem(
                422,
                "validation_failed",
                "input and expected are required: the entry does not record them.",
            )
        eval_set = await _regression_set(session, row, body.eval_set_id)
        if eval_set is None:
            raise Problem(
                422, "validation_failed", "No active eval set of this Data Space takes the case."
            )
        case = {
            "input": input_,
            "expected": expected,
            "tolerance": body.tolerance
            if body.tolerance is not None
            else recorded.get("tolerance"),
            "as_of": body.as_of or recorded.get("as_of"),
        }
        item = _regression_item(principal, row, eval_set, case)
        session.add(item)
        await session.flush()
        # regression_added waits for the next deploy or nightly run to pass it (eval worker, BR-179).
        row.eval_item_id = item.id
        row.updated_by, row.updated_at = principal.user_id, _now()
        await session.flush()
        return ErrorBookView.model_validate(row, from_attributes=True)


@router.post(
    "/v1/error-book-entries/{id}:dismiss",
    response_model=ErrorBookView,
    **endpoint("POST", "/v1/error-book-entries/{id}:dismiss", operation_id="dismissErrorBookEntry"),
)
async def dismiss_error(
    body: Dismiss,
    entry_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> ErrorBookView:
    async with transaction(ROLE, principal) as session:
        row = await _entry(session, entry_id, principal, data_space, lock=True)
        if row.status != "open":
            raise Problem(
                409, "conflict", f"The entry is {row.status}; only an open entry is dismissed."
            )
        if body.duplicate_of_id is not None:
            if body.duplicate_of_id == row.id:
                raise Problem(422, "validation_failed", "An entry is not a duplicate of itself.")
            await _entry(session, body.duplicate_of_id, principal, data_space)
        row.status = "dismissed"
        row.dismiss_reason = (body.reason or "").strip() or None
        row.duplicate_of_id = body.duplicate_of_id
        row.updated_by, row.updated_at = principal.user_id, _now()
        session.add(
            _event(
                principal,
                "error_book_entry",
                row.id,
                "error_book_entry.dismissed",
                entry_id=row.id,
                entry_no=row.entry_no,
                data_space_id=row.data_space_id,
                status=row.status,
                duplicate_of_id=row.duplicate_of_id,
            )
        )
        await session.flush()
        return ErrorBookView.model_validate(row, from_attributes=True)


@router.post(
    "/v1/error-book-entries/{id}:propose-to-pack",
    response_model=ErrorBookView,
    **endpoint(
        "POST", "/v1/error-book-entries/{id}:propose-to-pack", operation_id="proposeErrorBookToPack"
    ),
)
async def propose_to_pack(
    body: ProposeToPack,
    entry_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> ErrorBookView:
    async with transaction(ROLE, principal) as session:
        row = await _entry(session, entry_id, principal, data_space, lock=True)
        if row.status not in ("fixed", "regression_added"):
            raise Problem(
                409, "conflict", f"The entry is {row.status}; propose a fix once it is fixed."
            )
        if row.pack_contribution in ("proposed", "accepted"):
            raise Problem(
                409, "conflict", f"The fix is already {row.pack_contribution} for the pack."
            )
        row.pack_contribution = "proposed"
        row.pack_proposal_ref = str(uuid7())
        row.updated_by, row.updated_at = principal.user_id, _now()
        # UC-09-05: only the fix and the regression case's shape leave the organisation; title,
        # detail and root cause stay here. The summary is written by the steward for the pack.
        session.add(
            _event(
                principal,
                "error_book_entry",
                row.id,
                "error_book_entry.proposed_to_pack",
                entry_id=row.id,
                pack_id=body.pack_id,
                pack_proposal_ref=row.pack_proposal_ref,
                pack_contribution=row.pack_contribution,
                error_kind=row.error_kind,
                fix_kind=row.fix_kind,
                eval_item_id=row.eval_item_id,
                summary=body.summary,
            )
        )
        await session.flush()
        return ErrorBookView.model_validate(row, from_attributes=True)
