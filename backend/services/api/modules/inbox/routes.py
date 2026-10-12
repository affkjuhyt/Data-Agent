"""API-04 "Cần bạn": HITL requests and proposal decisions (UC-07-03, UC-07-07, UC-07-08).

A request goes to a person, a user group, or a capability (assignee_capability, decided
12/10/2026): whoever holds it in the request's Data Space, or at the organisation level when the
request belongs to no space (an audit export, an emergency grant). The caller's inbox shows the
requests of the space they named plus the org-wide ones. Each step of an approval chain names the
capability its approver must hold, and no one decides two steps (BR-132).

Every status change writes its event to inbox.outbox in the same transaction (D1 rule 8);
the agent runtime, channel gateway and the service that owns a proposal's object act on
hitl.resolved and proposal.decided, never on a write from here into their schema.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import eval as evaluation
from libs.dataagent_db.models import inbox, org
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
from services.api.modules.inbox.schemas import (
    ApprovalStepView,
    ChangedResult,
    EvalRunSummary,
    HitlDetail,
    HitlResolve,
    HitlView,
    ProposalDecision,
    ProposalDetail,
    ProposalReviewView,
    ProposalView,
)

router = APIRouter()
ROLE = "svc_inbox"

# hitl_request states that still wait for a person; "reassigned" waits for the new assignee.
PENDING = ("open", "reassigned")
HITL_STATUS = {
    "answer": "answered",
    "approve": "approved",
    "reject": "rejected",
    "request_changes": "changes_requested",
    "reassign": "reassigned",
}
DECIDABLE_PROPOSAL = ("open", "pending_review")
ACCEPT = ("accept", "accept_with_edit")
PROPOSAL_STATUS = {
    "accept": "accepted",
    "accept_with_edit": "accepted",
    "reject": "rejected",
    # Back to the proposer, who revises and resubmits.
    "request_changes": "open",
}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _event(
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> inbox.Outbox:
    return inbox.Outbox(
        id=uuid7(),
        org_id=principal.org_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in payload.items()},
    )


def _my_groups(principal: Principal) -> Select[tuple[str]]:
    return (
        select(org.UserGroup.name)
        .join(org.UserGroupMember, org.UserGroupMember.group_id == org.UserGroup.id)
        .where(org.UserGroupMember.user_id == principal.user_id)
    )


async def org_capabilities(request: Request, principal: CurrentPrincipal) -> frozenset[str]:
    """The caller's capabilities at the organisation level, without the space roles that the
    X-Data-Space header adds: an org-wide request is decided on these alone."""
    identity = await request.app.state.identity_resolver.resolve(
        org_id=principal.org_id, external_id=principal.external_id, data_space=None
    )
    return frozenset(identity.capabilities) if identity is not None else frozenset()


OrgCapabilities = Annotated[frozenset[str], Depends(org_capabilities)]


def _in_scope(data_space: uuid.UUID) -> Any:
    """Requests of the named space, and the org-wide ones."""
    return or_(
        inbox.HitlRequest.data_space_id == data_space, inbox.HitlRequest.data_space_id.is_(None)
    )


def _assigned_to(principal: Principal, org_caps: frozenset[str]) -> Any:
    """The caller is the assignee, belongs to the assigned group, or holds the assigned capability
    where the request lives (its space, or the organisation for an org-wide one)."""
    hitl = inbox.HitlRequest
    return or_(
        hitl.assignee_id == principal.user_id,
        and_(hitl.assignee_id.is_(None), hitl.assignee_group.in_(_my_groups(principal))),
        and_(
            hitl.assignee_id.is_(None),
            hitl.assignee_group.is_(None),
            or_(
                and_(
                    hitl.data_space_id.is_not(None),
                    hitl.assignee_capability.in_(sorted(principal.capabilities)),
                ),
                and_(
                    hitl.data_space_id.is_(None),
                    hitl.assignee_capability.in_(sorted(org_caps)),
                ),
            ),
        ),
    )


def _capabilities_for(
    row: inbox.HitlRequest, principal: Principal, org_caps: frozenset[str]
) -> frozenset[str]:
    return principal.capabilities if row.data_space_id is not None else org_caps


@router.get(
    "/v1/inbox",
    response_model=Page[HitlView],
    **endpoint("GET", "/v1/inbox", operation_id="listInbox"),
)
async def list_inbox(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    org_caps: OrgCapabilities,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by(
                "created_at", "updated_at", "title", "kind", "status", default="created_at desc"
            )
        ),
    ],
    kind: str | None = None,
    risk: str | None = None,
    status: str | None = None,
    assignee: Annotated[
        str | None,
        Query(
            description="'me' (default: assigned to the caller or a group of theirs) or a user id"
        ),
    ] = None,
) -> Page[HitlView]:
    hitl = inbox.HitlRequest
    query = select(hitl).where(hitl.org_id == principal.org_id, _in_scope(data_space))
    query = query.where(hitl.status == status) if status else query.where(hitl.status.in_(PENDING))
    if kind:
        query = query.where(hitl.kind == kind)
    if risk:
        query = query.where(hitl.risk == risk)
    # An org-wide request (an audit export with its filters, an emergency grant) is shown only to
    # those it concerns, whatever `assignee` asks for; a space's requests follow the space.
    query = query.where(
        or_(
            hitl.data_space_id.is_not(None),
            _assigned_to(principal, org_caps),
            hitl.created_by == principal.user_id,
        )
    )
    if assignee in (None, "me"):
        query = query.where(_assigned_to(principal, org_caps))
    else:
        try:
            query = query.where(hitl.assignee_id == uuid.UUID(assignee))
        except ValueError as exc:
            raise Problem(422, "validation_failed", "assignee is 'me' or a user id.") from exc
    query = keyset(
        query,
        columns={
            "created_at": hitl.created_at,
            "updated_at": hitl.updated_at,
            "title": hitl.title,
            "kind": hitl.kind,
            "status": hitl.status,
        },
        tiebreak=hitl.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(
        items=[HitlView.model_validate(row, from_attributes=True) for row in rows],
        next_page_token=token,
    )


async def _hitl(
    session: AsyncSession,
    request_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    org_caps: frozenset[str],
    *,
    lock: bool = False,
) -> inbox.HitlRequest:
    query = select(inbox.HitlRequest).where(
        inbox.HitlRequest.id == request_id,
        inbox.HitlRequest.org_id == principal.org_id,
        _in_scope(data_space),
    )
    if lock:
        # E2 of UC-07-07: two answers at once, only the first is recorded.
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None or not await _may_see(session, row, principal, org_caps):
        # The same answer for one that exists but is not the caller's to see.
        raise Problem(404, "not_found", "HITL request not found.")
    return row


async def _may_see(
    session: AsyncSession,
    row: inbox.HitlRequest,
    principal: Principal,
    org_caps: frozenset[str],
) -> bool:
    """A space's request is visible to the space; an org-wide one only to whom it concerns: its
    requester, its assignee, and whoever holds or held a step of its chain."""
    if row.data_space_id is not None or row.created_by == principal.user_id:
        return True
    if await _is_assignee(session, row, principal, org_caps):
        return True
    return any(
        s.approver_id == principal.user_id
        or (s.approver_id is None and s.approver_role in org_caps)
        for s in await _steps(session, row.id)
    )


async def _steps(session: AsyncSession, request_id: uuid.UUID) -> list[inbox.HitlApprovalStep]:
    return list(
        (
            await session.scalars(
                select(inbox.HitlApprovalStep)
                .where(inbox.HitlApprovalStep.hitl_request_id == request_id)
                .order_by(inbox.HitlApprovalStep.step)
            )
        ).all()
    )


def _detail(row: inbox.HitlRequest, steps: list[inbox.HitlApprovalStep]) -> HitlDetail:
    return HitlDetail(
        **HitlView.model_validate(row, from_attributes=True).model_dump(),
        approval_steps=[ApprovalStepView.model_validate(s, from_attributes=True) for s in steps],
    )


@router.get(
    "/v1/hitl-requests/{id}",
    response_model=HitlDetail,
    **endpoint("GET", "/v1/hitl-requests/{id}", operation_id="getHitlRequest"),
)
async def get_hitl(
    request_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    org_caps: OrgCapabilities,
) -> HitlDetail:
    async with transaction(ROLE, principal) as session:
        row = await _hitl(session, request_id, principal, data_space, org_caps)
        steps = await _steps(session, row.id)
    return _detail(row, steps)


async def _is_assignee(
    session: AsyncSession,
    row: inbox.HitlRequest,
    principal: Principal,
    org_caps: frozenset[str],
) -> bool:
    if row.assignee_id is not None:
        return row.assignee_id == principal.user_id
    if row.assignee_group is not None:
        return bool(
            await session.scalar(
                select(
                    _my_groups(principal).where(org.UserGroup.name == row.assignee_group).exists()
                )
            )
        )
    return row.assignee_capability in _capabilities_for(row, principal, org_caps)


def _check_option(row: inbox.HitlRequest, option: str | None) -> None:
    """The chosen option must be one the request offered (options: strings or {id|key|code})."""
    if option is None or not row.options:
        return
    offered: set[str] = set()
    for item in row.options:
        if isinstance(item, dict):
            offered.update(str(item[k]) for k in ("id", "key", "code") if k in item)
        else:
            offered.add(str(item))
    if option not in offered:
        raise Problem(422, "validation_failed", "option is not one of the request's options.")


async def _decide_step(
    session: AsyncSession,
    row: inbox.HitlRequest,
    steps: list[inbox.HitlApprovalStep],
    body: HitlResolve,
    principal: Principal,
    org_caps: frozenset[str],
) -> bool:
    """Records the caller's decision on the current step of a chain; True when the chain is done."""
    if body.decision not in ("approve", "reject"):
        raise Problem(
            422, "validation_failed", "A step of an approval chain is approved or rejected."
        )
    current = next(s for s in steps if s.decision is None)
    if current.approver_id is not None and current.approver_id != principal.user_id:
        raise Problem(403, "forbidden", "This approval step is assigned to someone else.")
    if any(s.approver_id == principal.user_id for s in steps if s.decision is not None):
        # BR-132 / BR-62: each step of a chain is a different person.
        raise Problem(
            422,
            "business_rule_violated",
            "Each approval step must be decided by a different person (BR-132).",
        )
    if current.approver_id is None and current.approver_role not in _capabilities_for(
        row, principal, org_caps
    ):
        raise Problem(
            403, "forbidden", f"This step is decided by someone holding {current.approver_role}."
        )
    current.approver_id = principal.user_id
    current.decision = body.decision
    current.note = body.note
    current.decided_at = _now()
    remaining = [s for s in steps if s.decision is None]
    if body.decision == "approve" and remaining:
        # The next step opens now (UC-07-07 5a); its approver, when named, becomes the assignee.
        if remaining[0].approver_id is not None:
            row.assignee_id = remaining[0].approver_id
        elif row.assignee_capability is not None:
            # A chain routed by capability moves to the next step's holders.
            row.assignee_capability = remaining[0].approver_role
        session.add(
            _event(
                principal,
                "hitl_request",
                row.id,
                "hitl.step_decided",
                hitl_request_id=row.id,
                data_space_id=row.data_space_id,
                step=current.step,
                decision=body.decision,
                next_step=remaining[0].step,
                next_approver_id=remaining[0].approver_id,
            )
        )
        return False
    return True


@router.post(
    "/v1/hitl-requests/{id}:resolve",
    response_model=HitlDetail,
    **endpoint("POST", "/v1/hitl-requests/{id}:resolve", operation_id="resolveHitlRequest"),
)
async def resolve_hitl(
    body: HitlResolve,
    request_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    org_caps: OrgCapabilities,
) -> HitlDetail:
    note = (body.note or "").strip() or None
    async with transaction(ROLE, principal) as session:
        row = await _hitl(session, request_id, principal, data_space, org_caps, lock=True)
        if row.status not in PENDING:
            raise Problem(409, "invalid_state", f"The request is already {row.status}.")
        steps = await _steps(session, row.id)
        chained = any(s.decision is None for s in steps)
        # In a chain the step's named approver may decide even when the request names another assignee.
        step_approver = (
            chained
            and next(s for s in steps if s.decision is None).approver_id == principal.user_id
        )
        if not step_approver and not await _is_assignee(session, row, principal, org_caps):
            raise Problem(403, "forbidden", "Only the assignee can resolve this request.")
        excluded = (row.context or {}).get("excluded_approver_ids") or []
        if body.decision != "reassign" and str(principal.user_id) in excluded:
            # Named by the module that asked (the filer, the closer of the period).
            raise Problem(403, "forbidden", "You may not decide this request.")
        if body.decision == "approve" and row.created_by == principal.user_id:
            # BR-62: whoever raised the change does not approve it.
            raise Problem(403, "forbidden", "The requester cannot approve their own request.")
        _check_option(row, body.option)
        now = _now()
        row.updated_by = principal.user_id
        row.updated_at = now
        if body.decision == "reassign":
            target = await session.scalar(
                select(org.AppUser.id).where(
                    org.AppUser.id == body.reassign_to,
                    org.AppUser.org_id == principal.org_id,
                    org.AppUser.status == "active",
                )
            )
            if target is None:
                raise Problem(422, "validation_failed", "reassign_to is not an active user.")
            row.status, row.assignee_id = "reassigned", target
            row.response = {"note": note} if note else row.response
            session.add(
                _event(
                    principal,
                    "hitl_request",
                    row.id,
                    "hitl.reassigned",
                    hitl_request_id=row.id,
                    data_space_id=row.data_space_id,
                    status=row.status,
                    assignee_id=target,
                )
            )
            await session.flush()
            return _detail(row, steps)
        if chained and not await _decide_step(session, row, steps, body, principal, org_caps):
            await session.flush()
            return _detail(row, steps)
        row.status = HITL_STATUS[body.decision]
        row.response = {"decision": body.decision, "option": body.option, "note": note}
        row.remember_as_rule = body.remember_as_rule
        row.resolved_by, row.resolved_at = principal.user_id, now
        # The agent runtime resumes the run; with remember_as_rule the governance service drafts
        # the HITL-origin policy (UC-07-08) from this request.
        session.add(
            _event(
                principal,
                "hitl_request",
                row.id,
                "hitl.resolved",
                hitl_request_id=row.id,
                data_space_id=row.data_space_id,
                kind=row.kind,
                status=row.status,
                decision=body.decision,
                option=body.option,
                remember_as_rule=body.remember_as_rule,
                source_service=row.source_service,
                source_ref=row.source_ref,
                resolved_by=principal.user_id,
            )
        )
        await session.flush()
        return _detail(row, steps)


async def _proposal(
    session: AsyncSession,
    proposal_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> inbox.Proposal:
    query = select(inbox.Proposal).where(
        inbox.Proposal.id == proposal_id,
        inbox.Proposal.org_id == principal.org_id,
        inbox.Proposal.data_space_id == data_space,
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Proposal not found.")
    return row


async def _proposal_detail(session: AsyncSession, row: inbox.Proposal) -> ProposalDetail:
    run = changed = None
    if row.eval_run_id is not None:
        run = await session.scalar(
            select(evaluation.EvalRun).where(evaluation.EvalRun.id == row.eval_run_id)
        )
        changed = (
            await session.scalars(
                select(evaluation.EvalResult).where(
                    evaluation.EvalResult.eval_run_id == row.eval_run_id,
                    evaluation.EvalResult.changed_from_baseline.is_(True),
                )
            )
        ).all()
    reviews = (
        await session.scalars(
            select(inbox.ProposalReview)
            .where(inbox.ProposalReview.proposal_id == row.id)
            .order_by(inbox.ProposalReview.step, inbox.ProposalReview.decided_at)
        )
    ).all()
    return ProposalDetail(
        **ProposalView.model_validate(row, from_attributes=True).model_dump(),
        eval_run=EvalRunSummary.model_validate(run, from_attributes=True) if run else None,
        changed_results=[
            ChangedResult.model_validate(r, from_attributes=True) for r in changed or []
        ],
        reviews=[ProposalReviewView.model_validate(r, from_attributes=True) for r in reviews],
    )


@router.get(
    "/v1/proposals/{id}",
    response_model=ProposalDetail,
    **endpoint("GET", "/v1/proposals/{id}", operation_id="getProposal"),
)
async def get_proposal(
    proposal_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> ProposalDetail:
    async with transaction(ROLE, principal) as session:
        row = await _proposal(session, proposal_id, principal, data_space)
        return await _proposal_detail(session, row)


@router.post(
    "/v1/proposals/{id}:decide",
    response_model=ProposalDetail,
    **endpoint("POST", "/v1/proposals/{id}:decide", operation_id="decideProposal"),
)
async def decide_proposal(
    body: ProposalDecision,
    proposal_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> ProposalDetail:
    # A blank note is no note: the CHECK on proposal_review then refuses a reject without one (4b).
    note = (body.note or "").strip() or None
    accepting = body.decision in ACCEPT
    async with transaction(ROLE, principal) as session:
        row = await _proposal(session, proposal_id, principal, data_space, lock=True)
        if row.status not in DECIDABLE_PROPOSAL or row.expires_at <= _now():
            raise Problem(
                409, "invalid_state", f"The proposal is {row.status} and cannot be decided."
            )
        if row.proposed_by == principal.user_id:
            raise Problem(403, "forbidden", "The proposer cannot review their own proposal.")
        if accepting and row.eval_run_id is not None and not body.reviewed_regressions:
            run = await session.scalar(
                select(evaluation.EvalRun).where(evaluation.EvalRun.id == row.eval_run_id)
            )
            if run is not None and run.regressions > 0:
                raise Problem(
                    422,
                    "business_rule_violated",
                    f"{run.regressions} evaluation item(s) changed result; review each before accepting (BR-28).",
                )
        reviews = list(
            (
                await session.scalars(
                    select(inbox.ProposalReview)
                    .where(inbox.ProposalReview.proposal_id == row.id)
                    .order_by(inbox.ProposalReview.decided_at, inbox.ProposalReview.step)
                )
            ).all()
        )
        # The chain so far: the plain accepts since the last request_changes or edit. An edit
        # (accept_with_edit) is new content nobody else has seen, so it restarts the chain, and the
        # person who wrote it may not accept it: they are its author now (BR-62, BR-119).
        accepted_before: list[inbox.ProposalReview] = []
        editors: set[uuid.UUID] = set()
        for review in reviews:
            if review.decision == "accept":
                accepted_before = [*accepted_before, review]
            else:
                accepted_before = []
                if review.decision == "accept_with_edit":
                    editors.add(review.reviewer_id)
                elif review.decision == "request_changes":
                    editors = set()
        if principal.user_id in editors:
            raise Problem(
                403, "forbidden", "Whoever edited the proposal cannot also accept the edit."
            )
        chain_step = len(accepted_before) + 1
        if chain_step > 1:
            # BR-119 second step: the controller (AC06), another person than the first reviewer.
            if "compliance" not in principal.capabilities:
                raise Problem(
                    403, "forbidden", "The second approval step needs the compliance capability."
                )
            if any(r.reviewer_id == principal.user_id for r in accepted_before):
                raise Problem(
                    422,
                    "business_rule_violated",
                    "The second approval step must be another person than the first (BR-119).",
                )
        session.add(
            inbox.ProposalReview(
                id=uuid7(),
                org_id=principal.org_id,
                proposal_id=row.id,
                # Numbered over every review, so a reviewer who asked for changes can decide the
                # resubmitted proposal without hitting UNIQUE (proposal_id, step, reviewer_id).
                step=len(reviews) + 1,
                reviewer_id=principal.user_id,
                decision=body.decision,
                note=note,
                reviewed_regressions=body.reviewed_regressions,
            )
        )
        if body.edited_diff is not None:
            # 4a: the owning service re-runs the evaluation on the edited diff before it publishes.
            row.diff = body.edited_diff
        row.updated_by, row.updated_at = principal.user_id, _now()
        if body.decision == "accept_with_edit":
            # The edited diff waits for another reviewer; nothing is published on an edit.
            row.status = "pending_review"
            event_type = "proposal.step_decided"
        elif accepting and row.requires_compliance and chain_step == 1:
            # BR-119: the change waits for the controller's step; nothing is published yet.
            row.status = "pending_review"
            event_type = "proposal.step_decided"
        else:
            row.status = PROPOSAL_STATUS[body.decision]
            event_type = "proposal.decided"
        session.add(
            _event(
                principal,
                "proposal",
                row.id,
                event_type,
                proposal_id=row.id,
                data_space_id=row.data_space_id,
                kind=row.kind,
                target_service=row.target_service,
                target_ref=row.target_ref,
                status=row.status,
                decision=body.decision,
                step=chain_step,
                edited=body.edited_diff is not None,
                reviewer_id=principal.user_id,
            )
        )
        await session.flush()
        return await _proposal_detail(session, row)
