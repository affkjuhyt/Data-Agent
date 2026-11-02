"""The caller's organisation and its self-approval switch (org.organization.allow_self_approval).

Anyone in the organisation reads the switch: a creator needs to know whether they may approve
their own work. Only an admin turns it, and each change is audited with what it was and what it
became. The identity resolver reads the switch on every request, so the next approval already
follows it.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import org
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.problems import Problem
from services.api.modules.org.admin.common import audit, now
from services.api.modules.org.admin.schemas import OrganizationPatch, OrganizationView
from services.api.modules.org.identity import ROLE

router = APIRouter()

PATH = "/v1/admin/organization"


async def _organization(
    session: AsyncSession, principal: Principal, *, lock: bool = False
) -> org.Organization:
    query = select(org.Organization).where(org.Organization.id == principal.org_id)
    if lock:
        query = query.with_for_update()
    found = await session.scalar(query)
    if found is None:
        raise Problem(404, "not_found", "Organisation not found.")
    return found


@router.get(
    PATH,
    response_model=OrganizationView,
    **endpoint("GET", "/v1/admin/organization", operation_id="getOrganization"),
)
async def get_organization(principal: CurrentPrincipal) -> OrganizationView:
    async with transaction(ROLE, principal) as session:
        return OrganizationView.model_validate(await _organization(session, principal))


@router.patch(
    PATH,
    response_model=OrganizationView,
    **endpoint("PATCH", "/v1/admin/organization", operation_id="updateOrganization"),
)
async def update_organization(
    body: OrganizationPatch, principal: CurrentPrincipal
) -> OrganizationView:
    async with transaction(ROLE, principal) as session:
        organization = await _organization(session, principal, lock=True)
        was = organization.allow_self_approval
        # Setting the value it already has changes nothing, so there is nothing to audit.
        if body.allow_self_approval != was:
            organization.allow_self_approval = body.allow_self_approval
            organization.updated_at = now()
            organization.updated_by = principal.user_id
            audit(
                session,
                principal,
                action="organization.updated",
                object_kind="organization",
                object_id=organization.id,
                before={"allow_self_approval": was},
                after={"allow_self_approval": body.allow_self_approval},
            )
            await session.flush()
        return OrganizationView.model_validate(organization)
