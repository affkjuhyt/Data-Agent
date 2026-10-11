"""The caller's own profile (GET /v1/me, UC-11-01)."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from libs.dataagent_db.models import org
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentPrincipal
from services.api.kernel.db import transaction
from services.api.kernel.problems import Problem
from services.api.modules.org.identity import ROLE, my_groups, still_valid
from services.api.modules.org.schemas import Me, RoleGrant, RoleRef

router = APIRouter()


@router.get("/v1/me", **endpoint("GET", "/v1/me", operation_id="getMe"))
async def get_me(principal: CurrentPrincipal) -> Me:
    async with transaction(ROLE, principal) as session:
        user = await session.get(org.AppUser, principal.user_id)
        organization = await session.get(org.Organization, principal.org_id)
        if user is None or organization is None:
            # Resolved a moment ago in the same organisation; gone only if the
            # account was removed in between.
            raise Problem(403, "forbidden", "No active account in this organisation.")
        direct = await session.execute(
            select(org.UserRole, org.Role)
            .join(org.Role, org.Role.id == org.UserRole.role_id)
            .where(org.UserRole.user_id == user.id, still_valid(org.UserRole.valid_until))
        )
        by_group = await session.execute(
            select(org.GroupMapping, org.Role, org.UserGroup)
            .join(org.Role, org.Role.id == org.GroupMapping.role_id)
            .join(org.UserGroup, org.UserGroup.id == org.GroupMapping.group_id)
            .where(
                org.GroupMapping.group_id.in_(my_groups(user.id)),
                org.GroupMapping.data_space_id.is_(None),
                org.GroupMapping.status == "active",
            )
        )
        grants = [
            RoleGrant(
                role=RoleRef(id=role.id, code=role.code, name=role.name),
                source=grant.source,
                valid_until=grant.valid_until,
            )
            for grant, role in direct.tuples()
        ] + [
            RoleGrant(
                role=RoleRef(id=role.id, code=role.code, name=role.name),
                source="group_mapping",
                group_name=group.name,
            )
            for _, role, group in by_group.tuples()
        ]
    grants.sort(key=lambda g: (g.role.code, g.source, g.group_name or ""))
    return Me(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        department=user.department,
        kind=user.kind,
        default_data_space_id=user.default_data_space_id,
        locale=organization.locale,
        time_zone=organization.time_zone,
        roles=grants,
        capabilities=sorted(principal.capabilities),
    )
