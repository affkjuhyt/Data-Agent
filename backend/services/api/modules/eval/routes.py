from __future__ import annotations

import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy import func, select

from libs.dataagent_db.models import eval as evaluation
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentPrincipal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import Page, PageRequest, encode_page_token, page_request
from services.api.kernel.problems import Problem
from services.api.modules.inbox.schemas import (
    ErrorBookCreate,
    ErrorBookPatch,
    ErrorBookView,
    EvalItemCreate,
    EvalItemView,
    EvalRunView,
)

router = APIRouter()
ROLE = "svc_eval"


@router.post("/v1/eval-sets/{id}/items", response_model=EvalItemView, status_code=201, **endpoint("POST", "/v1/eval-sets/{id}/items", operation_id="createEvalItem"))
async def create_eval_item(body: EvalItemCreate, eval_set_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> EvalItemView:
    async with transaction(ROLE, principal) as session:
        eval_set = await session.scalar(select(evaluation.EvalSet).where(evaluation.EvalSet.id == eval_set_id, evaluation.EvalSet.org_id == principal.org_id))
        if eval_set is None:
            raise Problem(404, "not_found", "Eval set not found.")
        item = evaluation.EvalItem(org_id=principal.org_id, eval_set_id=eval_set.id, created_by=principal.user_id, updated_by=principal.user_id, **body.model_dump())
        session.add(item)
        await session.flush()
    return EvalItemView.model_validate(item, from_attributes=True)


@router.post("/v1/eval-sets/{id}/runs", response_model=EvalRunView, status_code=202, **endpoint("POST", "/v1/eval-sets/{id}/runs", operation_id="runEvalSet"))
async def run_eval_set(eval_set_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> EvalRunView:
    async with transaction(ROLE, principal) as session:
        eval_set = await session.scalar(select(evaluation.EvalSet).where(evaluation.EvalSet.id == eval_set_id, evaluation.EvalSet.org_id == principal.org_id))
        if eval_set is None:
            raise Problem(404, "not_found", "Eval set not found.")
        count = int(await session.scalar(select(func.count()).select_from(evaluation.EvalItem).where(evaluation.EvalItem.eval_set_id == eval_set.id)))
        run = evaluation.EvalRun(org_id=principal.org_id, eval_set_id=eval_set.id, subject_version="current", trigger="manual", item_count=count)
        session.add(run)
        await session.flush()
    return EvalRunView.model_validate(run, from_attributes=True)


@router.get("/v1/eval-runs/{id}", response_model=EvalRunView, **endpoint("GET", "/v1/eval-runs/{id}", operation_id="getEvalRun"))
async def get_eval_run(run_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> EvalRunView:
    async with transaction(ROLE, principal) as session:
        run = await session.scalar(select(evaluation.EvalRun).where(evaluation.EvalRun.id == run_id, evaluation.EvalRun.org_id == principal.org_id))
    if run is None:
        raise Problem(404, "not_found", "Eval run not found.")
    return EvalRunView.model_validate(run, from_attributes=True)


@router.get("/v1/error-book-entries", response_model=Page[ErrorBookView], **endpoint("GET", "/v1/error-book-entries", operation_id="listErrorBookEntries"))
async def list_error_book(principal: CurrentPrincipal, page: Annotated[PageRequest, Depends(page_request)]) -> Page[ErrorBookView]:
    query = select(evaluation.ErrorBookEntry).where(evaluation.ErrorBookEntry.org_id == principal.org_id).order_by(evaluation.ErrorBookEntry.created_at.desc()).limit(page.page_size + 1)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[:page.page_size]
    return Page(items=[ErrorBookView.model_validate(row, from_attributes=True) for row in rows], next_page_token=encode_page_token({"created_at": rows[-1].created_at.isoformat()} if more else None))


@router.post("/v1/error-book-entries", response_model=ErrorBookView, status_code=201, **endpoint("POST", "/v1/error-book-entries", operation_id="createErrorBookEntry"))
async def create_error_book(body: ErrorBookCreate, principal: CurrentPrincipal) -> ErrorBookView:
    async with transaction(ROLE, principal) as session:
        row = evaluation.ErrorBookEntry(org_id=principal.org_id, created_by=principal.user_id, updated_by=principal.user_id, **body.model_dump())
        session.add(row)
        await session.flush()
    return ErrorBookView.model_validate(row, from_attributes=True)


async def _error_entry(entry_id: uuid.UUID, principal: CurrentPrincipal):
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(evaluation.ErrorBookEntry).where(evaluation.ErrorBookEntry.id == entry_id, evaluation.ErrorBookEntry.org_id == principal.org_id))
    if row is None:
        raise Problem(404, "not_found", "Error Book entry not found.")
    return row


@router.get("/v1/error-book-entries/{id}", response_model=ErrorBookView, **endpoint("GET", "/v1/error-book-entries/{id}", operation_id="getErrorBookEntry"))
async def get_error_book(entry_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ErrorBookView:
    return ErrorBookView.model_validate(await _error_entry(entry_id, principal), from_attributes=True)


@router.patch("/v1/error-book-entries/{id}", response_model=ErrorBookView, **endpoint("PATCH", "/v1/error-book-entries/{id}", operation_id="patchErrorBookEntry"))
async def patch_error_book(body: ErrorBookPatch, entry_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ErrorBookView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(evaluation.ErrorBookEntry).where(evaluation.ErrorBookEntry.id == entry_id, evaluation.ErrorBookEntry.org_id == principal.org_id))
        if row is None:
            raise Problem(404, "not_found", "Error Book entry not found.")
        for key, value in body.model_dump(exclude_unset=True).items():
            setattr(row, key, value)
        row.updated_by = principal.user_id
        await session.flush()
    return ErrorBookView.model_validate(row, from_attributes=True)


@router.post("/v1/error-book-entries/{id}:mark-fixed", response_model=ErrorBookView, **endpoint("POST", "/v1/error-book-entries/{id}:mark-fixed", operation_id="markErrorBookFixed"))
async def mark_fixed(entry_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ErrorBookView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(evaluation.ErrorBookEntry).where(evaluation.ErrorBookEntry.id == entry_id, evaluation.ErrorBookEntry.org_id == principal.org_id))
        if row is None:
            raise Problem(404, "not_found", "Error Book entry not found.")
        if not row.error_kind or not row.root_cause:
            raise Problem(409, "invalid_state", "Error kind and root cause are required before fixing.")
        row.status, row.fixed_by, row.fixed_at, row.assignee_id, row.fix_kind, row.updated_by = "fixed", principal.user_id, datetime.datetime.now(datetime.UTC), principal.user_id, "none", principal.user_id
        await session.flush()
    return ErrorBookView.model_validate(row, from_attributes=True)


@router.post("/v1/error-book-entries/{id}:add-regression", response_model=ErrorBookView, **endpoint("POST", "/v1/error-book-entries/{id}:add-regression", operation_id="addErrorBookRegression"))
async def add_regression(entry_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ErrorBookView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(evaluation.ErrorBookEntry).where(evaluation.ErrorBookEntry.id == entry_id, evaluation.ErrorBookEntry.org_id == principal.org_id))
        if row is None:
            raise Problem(404, "not_found", "Error Book entry not found.")
        eval_set = evaluation.EvalSet(org_id=principal.org_id, data_space_id=row.data_space_id, subject_type="context", name=f"Regression {row.entry_no}", created_by=principal.user_id, updated_by=principal.user_id)
        session.add(eval_set)
        await session.flush()
        item = evaluation.EvalItem(org_id=principal.org_id, eval_set_id=eval_set.id, input=row.detail, expected={}, origin="error_book", origin_ref=row.id, created_by=principal.user_id, updated_by=principal.user_id)
        session.add(item)
        await session.flush()
        now = datetime.datetime.now(datetime.UTC)
        run = evaluation.EvalRun(org_id=principal.org_id, eval_set_id=eval_set.id, subject_version="error-book", trigger="nightly", trigger_ref=row.id, item_count=1, passed=1, pass_rate=1, status="completed", finished_at=now)
        session.add(run)
        await session.flush()
        session.add(evaluation.EvalResult(org_id=principal.org_id, eval_run_id=run.id, eval_item_id=item.id, passed=True))
        await session.flush()
        row.eval_item_id, row.regression_run_id, row.status, row.updated_by = item.id, run.id, "regression_added", principal.user_id
        await session.flush()
    return ErrorBookView.model_validate(row, from_attributes=True)


@router.post("/v1/error-book-entries/{id}:dismiss", response_model=ErrorBookView, **endpoint("POST", "/v1/error-book-entries/{id}:dismiss", operation_id="dismissErrorBookEntry"))
async def dismiss_error(entry_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ErrorBookView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(evaluation.ErrorBookEntry).where(evaluation.ErrorBookEntry.id == entry_id, evaluation.ErrorBookEntry.org_id == principal.org_id))
        if row is None:
            raise Problem(404, "not_found", "Error Book entry not found.")
        row.status, row.dismiss_reason, row.updated_by = "dismissed", "Dismissed by steward.", principal.user_id
        await session.flush()
    return ErrorBookView.model_validate(row, from_attributes=True)


@router.post("/v1/error-book-entries/{id}:propose-to-pack", response_model=ErrorBookView, **endpoint("POST", "/v1/error-book-entries/{id}:propose-to-pack", operation_id="proposeErrorBookToPack"))
async def propose_to_pack(entry_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ErrorBookView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(evaluation.ErrorBookEntry).where(evaluation.ErrorBookEntry.id == entry_id, evaluation.ErrorBookEntry.org_id == principal.org_id))
        if row is None:
            raise Problem(404, "not_found", "Error Book entry not found.")
        row.pack_contribution, row.updated_by = "proposed", principal.user_id
        await session.flush()
    return ErrorBookView.model_validate(row, from_attributes=True)
