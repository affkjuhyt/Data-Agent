from __future__ import annotations

import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy import func, select

from libs.dataagent_db.models import inbox
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import Page, PageRequest, encode_page_token, page_request
from services.api.kernel.problems import Problem
from services.api.modules.inbox.schemas import HitlResolve, HitlView, ProposalDecision, ProposalView

router = APIRouter()
ROLE = "svc_inbox"


@router.get("/v1/inbox", response_model=Page[HitlView], **endpoint("GET", "/v1/inbox", operation_id="listInbox"))
async def list_inbox(principal: CurrentPrincipal, data_space: CurrentDataSpace, page: Annotated[PageRequest, Depends(page_request)]) -> Page[HitlView]:
    query = select(inbox.HitlRequest).where(inbox.HitlRequest.org_id == principal.org_id, inbox.HitlRequest.data_space_id == data_space, inbox.HitlRequest.status == "open").order_by(inbox.HitlRequest.created_at.desc()).limit(page.page_size + 1)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[:page.page_size]
    return Page(items=[HitlView.model_validate(row, from_attributes=True) for row in rows], next_page_token=encode_page_token({"created_at": rows[-1].created_at.isoformat()} if more else None))


@router.get("/v1/hitl-requests/{id}", response_model=HitlView, **endpoint("GET", "/v1/hitl-requests/{id}", operation_id="getHitlRequest"))
async def get_hitl(request_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> HitlView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(inbox.HitlRequest).where(inbox.HitlRequest.id == request_id, inbox.HitlRequest.org_id == principal.org_id))
    if row is None:
        raise Problem(404, "not_found", "HITL request not found.")
    return HitlView.model_validate(row, from_attributes=True)


@router.post("/v1/hitl-requests/{id}:resolve", response_model=HitlView, **endpoint("POST", "/v1/hitl-requests/{id}:resolve", operation_id="resolveHitlRequest"))
async def resolve_hitl(body: HitlResolve, request_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> HitlView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(inbox.HitlRequest).where(inbox.HitlRequest.id == request_id, inbox.HitlRequest.org_id == principal.org_id))
        if row is None:
            raise Problem(404, "not_found", "HITL request not found.")
        row.status, row.response, row.remember_as_rule = body.status, body.response, body.remember_as_rule
        row.resolved_by, row.resolved_at, row.updated_by = principal.user_id, datetime.datetime.now(datetime.UTC), principal.user_id
        if body.assignee_id:
            row.assignee_id = body.assignee_id
        await session.flush()
    return HitlView.model_validate(row, from_attributes=True)


@router.get("/v1/proposals/{id}", response_model=ProposalView, **endpoint("GET", "/v1/proposals/{id}", operation_id="getProposal"))
async def get_proposal(proposal_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ProposalView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(inbox.Proposal).where(inbox.Proposal.id == proposal_id, inbox.Proposal.org_id == principal.org_id))
    if row is None:
        raise Problem(404, "not_found", "Proposal not found.")
    return ProposalView.model_validate(row, from_attributes=True)


@router.post("/v1/proposals/{id}:decide", response_model=ProposalView, **endpoint("POST", "/v1/proposals/{id}:decide", operation_id="decideProposal"))
async def decide_proposal(body: ProposalDecision, proposal_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ProposalView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(inbox.Proposal).where(inbox.Proposal.id == proposal_id, inbox.Proposal.org_id == principal.org_id))
        if row is None:
            raise Problem(404, "not_found", "Proposal not found.")
        if row.proposed_by == principal.user_id:
            raise Problem(409, "invalid_state", "The proposer cannot decide their own proposal.")
        if body.decision == "reject" and not body.note:
            raise Problem(400, "invalid_request", "A rejection requires a note.")
        row.status = {"accept": "accepted", "accept_with_edit": "accepted", "reject": "rejected", "request_changes": "pending_review"}[body.decision]
        if body.diff is not None:
            row.diff = body.diff
        review = inbox.ProposalReview(org_id=principal.org_id, proposal_id=row.id, reviewer_id=principal.user_id, decision=body.decision, note=body.note, reviewed_regressions=body.reviewed_regressions)
        session.add(review)
        row.updated_by = principal.user_id
        await session.flush()
    return ProposalView.model_validate(row, from_attributes=True)
