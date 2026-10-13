"""Access requests (UC-11-07, SD-18): a user asks for a capability in a Data Space; its steward
approves, narrows or rejects. The HITL item for the steward is the inbox module's: it builds it
from access_request.created (D1.2), and the requester hears back through access_request.<status>.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import org
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal
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
from services.api.modules.org.admin.common import audit, emit, now, org_only
from services.api.modules.org.admin.schemas import (
    AccessDecision,
    AccessRequestCreate,
    AccessRequestView,
)
from services.api.modules.org.identity import ROLE

router = APIRouter()

# BR-142: a grant through a request lasts at most 90 days from the day it is sent.
MAX_DAYS = 90
# The A1.2 capability that each requestable capability is granted as; seeing aggregates is what
# asking gives.
GRANTED_AS = {
    "view_aggregate": "ask",
    "ask": "ask",
    "view_detail": "view_detail",
    "run_agent": "run_agent",
}
STATUS = {"approve": "approved", "narrow": "narrowed", "reject": "rejected"}
# What a request for each capability may be narrowed to (UC-11-07 step 4). Narrowing never
# widens: a view_aggregate request "narrowed" to view_detail would skip the AC06 approval (E1).
NARROWER = {
    "view_aggregate": frozenset({"view_aggregate", "ask"}),
    "ask": frozenset({"ask", "view_aggregate"}),
    "view_detail": frozenset({"view_detail", "view_aggregate", "ask"}),
    "run_agent": frozenset({"run_agent", "view_aggregate", "ask"}),
}


@router.get(
    "/v1/access-requests",
    response_model=Page[AccessRequestView],
    **endpoint("GET", "/v1/access-requests", operation_id="listAccessRequests"),
)
async def list_access_requests(
    principal: CurrentPrincipal,
    page: PageRequest = Depends(page_request),
    order: Order = Depends(order_by("created_at", "status", default="created_at desc")),
    status: str | None = Query(default=None, max_length=30),
    data_space_id: uuid.UUID | None = Query(default=None),
) -> Page[AccessRequestView]:
    """List requests visible to the requester or to a Data Space steward."""
    row = org.AccessRequest
    query = select(row).where(row.org_id == principal.org_id)
    if data_space_id is not None:
        query = query.where(row.data_space_id == data_space_id)
    if "steward" not in principal.capabilities:
        query = query.where(row.requester_id == principal.user_id)
    if status is not None:
        query = query.where(row.status == status)
    query = keyset(
        query,
        columns={"created_at": row.created_at, "status": row.status},
        tiebreak=row.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(
        items=[AccessRequestView.model_validate(item) for item in rows],
        next_page_token=token,
    )


@router.get(
    "/v1/access-requests/{id}",
    response_model=AccessRequestView,
    **endpoint("GET", "/v1/access-requests/{id}", operation_id="getAccessRequest"),
)
async def get_access_request(
    request_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
) -> AccessRequestView:
    row = org.AccessRequest
    async with transaction(ROLE, principal) as session:
        request = await session.scalar(
            select(row).where(
                row.id == request_id,
                row.org_id == principal.org_id,
            )
        )
    if request is None:
        raise Problem(404, "not_found", "Access request not found.")
    if request.requester_id != principal.user_id and "steward" not in principal.capabilities:
        raise Problem(404, "not_found", "Access request not found.")
    return AccessRequestView.model_validate(request)


@router.post(
    "/v1/access-requests",
    status_code=201,
    response_model=AccessRequestView,
    **endpoint("POST", "/v1/access-requests", operation_id="createAccessRequest"),
)
async def create_access_request(
    body: AccessRequestCreate, principal: CurrentPrincipal
) -> AccessRequestView:
    today = now().date()
    if body.requested_until <= today:
        raise Problem(422, "validation_failed", "requested_until is a future date.")
    if body.requested_until > today + datetime.timedelta(days=MAX_DAYS):
        raise Problem(
            422, "business_rule_violated", "BR-142: access through a request lasts at most 90 days."
        )
    async with transaction(ROLE, principal) as session:
        space = await session.scalar(
            select(org.DataSpace).where(
                org.DataSpace.id == body.data_space_id,
                org.DataSpace.deleted_at.is_(None),
                org.DataSpace.status != "archived",
            )
        )
        if space is None:
            raise Problem(404, "not_found", "Data Space not found.")
        pending = await session.scalar(
            select(org.AccessRequest.id).where(
                org.AccessRequest.requester_id == principal.user_id,
                org.AccessRequest.data_space_id == space.id,
                org.AccessRequest.capability == body.capability,
                org.AccessRequest.status == "pending",
            )
        )
        if pending is not None:
            raise Problem(409, "conflict", "The same request is already waiting for a decision.")
        row = org.AccessRequest(
            id=uuid7(),
            org_id=principal.org_id,
            requester_id=principal.user_id,
            data_space_id=space.id,
            capability=body.capability,
            reason=body.reason,
            requested_until=body.requested_until,
            # UC-11-07 E1: detail data may hold personal data, so AC06 decides too.
            needs_compliance=body.capability == "view_detail",
            status="pending",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        emit(
            session,
            principal,
            "access_request",
            row.id,
            "access_request.created",
            access_request_id=row.id,
            data_space_id=space.id,
            requester_id=principal.user_id,
            capability=row.capability,
            needs_compliance=row.needs_compliance,
            steward_id=space.steward_id,
            backup_steward_id=space.backup_steward_id,
            status=row.status,
        )
        await session.flush()
        await session.refresh(row)
        return AccessRequestView.model_validate(row)


async def _role_for(session: AsyncSession, capability: str) -> org.Role:
    """The smallest Data Space role granting `capability`, so a request never brings more than
    it asked for."""
    wanted = GRANTED_AS[capability]
    roles = [
        r
        for r in (
            await session.scalars(
                select(org.Role).where(literal(wanted) == func.any(org.Role.capabilities))
            )
        ).all()
        if not org_only(r)
    ]
    if not roles:
        raise Problem(422, "business_rule_violated", f"No Data Space role grants {wanted}.")
    return min(roles, key=lambda r: (len(r.capabilities), r.code))


@router.post(
    "/v1/access-requests/{id}:decide",
    response_model=AccessRequestView,
    **endpoint("POST", "/v1/access-requests/{id}:decide", operation_id="decideAccessRequest"),
)
async def decide_access_request(
    request_id: Annotated[uuid.UUID, Path(alias="id")],
    body: AccessDecision,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> AccessRequestView:
    if body.decision == "narrow" and body.granted_scope is None:
        raise Problem(422, "validation_failed", "narrow needs granted_scope.")
    if body.decision != "narrow" and body.granted_scope is not None:
        raise Problem(422, "validation_failed", "granted_scope goes with narrow only.")
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(org.AccessRequest)
            .where(
                org.AccessRequest.id == request_id, org.AccessRequest.data_space_id == data_space
            )
            .with_for_update()
        )
        if row is None:
            raise Problem(404, "not_found", "Access request not found.")
        if row.requester_id == principal.user_id:
            raise Problem(
                403, "forbidden", "A request is decided by someone other than its requester."
            )
        if row.status != "pending":
            raise Problem(409, "invalid_state", f"The request is already {row.status}.")
        granting = body.decision != "reject"
        if granting and row.needs_compliance and "compliance" not in principal.capabilities:
            # UC-11-07 E1: no member is added until AC06 has approved as well.
            raise Problem(403, "forbidden", "This request needs the approval of compliance (AC06).")
        stamp = now()
        row.status = STATUS[body.decision]
        row.approver_id = principal.user_id
        row.decided_at = stamp
        row.decision_note = body.note
        row.updated_at = stamp
        row.updated_by = principal.user_id
        member_id: uuid.UUID | None = None
        if granting:
            scope = body.granted_scope
            capability = (scope.capability if scope else None) or row.capability
            if capability not in NARROWER[row.capability]:
                raise Problem(
                    422,
                    "business_rule_violated",
                    f"A request for {row.capability} cannot be narrowed to {capability}.",
                )
            row_scope = scope.row_scope if scope else None
            if scope is not None:
                row.granted_scope = scope.model_dump(exclude_none=True)
            role = await _role_for(session, capability)
            # AC-11-07-01: the grant lasts the period asked for, counted from the approval
            # (a request decided late still gets its 30 days); BR-142 caps it at 90.
            asked = (row.requested_until - row.created_at.date()).days
            valid_until = stamp + datetime.timedelta(days=min(max(asked, 1), MAX_DAYS))
            member = await session.scalar(
                select(org.DataSpaceMember).where(
                    org.DataSpaceMember.data_space_id == row.data_space_id,
                    org.DataSpaceMember.principal_type == "user",
                    org.DataSpaceMember.principal_id == row.requester_id,
                    org.DataSpaceMember.role_id == role.id,
                )
            )
            if member is None:
                member = org.DataSpaceMember(
                    id=uuid7(),
                    org_id=principal.org_id,
                    data_space_id=row.data_space_id,
                    principal_type="user",
                    principal_id=row.requester_id,
                    role_id=role.id,
                    created_by=principal.user_id,
                )
                session.add(member)
            if member.source in (None, "access_request"):
                member.source = "access_request"
                member.source_ref = row.id
                member.row_scope = row_scope
                member.valid_until = valid_until
                member.updated_by = principal.user_id
            await session.flush()
            member_id = member.id
            row.member_id = member.id
            emit(
                session,
                principal,
                "data_space_member",
                member.id,
                "data_space_member.created",
                data_space_id=row.data_space_id,
                member_id=member.id,
                principal_type="user",
                principal_id=row.requester_id,
                role_id=role.id,
                valid_until=member.valid_until,
            )
        await session.flush()
        emit(
            session,
            principal,
            "access_request",
            row.id,
            f"access_request.{row.status}",
            access_request_id=row.id,
            data_space_id=row.data_space_id,
            requester_id=row.requester_id,
            member_id=member_id,
            status=row.status,
        )
        audit(
            session,
            principal,
            action=f"access_request.{row.status}",
            object_kind="access_request",
            object_id=row.id,
            after={
                "status": row.status,
                "member_id": member_id,
                "granted_scope": row.granted_scope,
            },
        )
        await session.flush()
        await session.refresh(row)
        return AccessRequestView.model_validate(row)
