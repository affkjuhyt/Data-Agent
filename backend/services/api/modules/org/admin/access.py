"""Access requests (UC-11-07, SD-18): a user asks for a capability in a Data Space; its steward
approves, narrows or rejects. The HITL item for the steward is the inbox module's: it builds it
from access_request.created (D1.2), and the requester hears back through access_request.<status>.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Path
from sqlalchemy import func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import org
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal
from services.api.kernel.db import transaction
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
            row_scope = scope.row_scope if scope else None
            if scope is not None:
                row.granted_scope = scope.model_dump(exclude_none=True)
            role = await _role_for(session, capability)
            # Valid through the last requested day (BR-142); the expiry job revokes it after.
            valid_until = datetime.datetime.combine(
                row.requested_until + datetime.timedelta(days=1), datetime.time(), datetime.UTC
            )
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
