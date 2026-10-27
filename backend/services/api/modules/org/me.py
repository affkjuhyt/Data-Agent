"""The caller's own profile (GET /v1/me, UC-11-01)."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Response
from sqlalchemy import select

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import agent, org
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal
from services.api.kernel.db import transaction
from services.api.kernel.problems import Problem
from services.api.modules.org.identity import ROLE, memberships, my_groups, user_role_in_force
from services.api.modules.org.public import open_agent_account
from services.api.modules.org.schemas import (
    AgentTemplateChoice,
    DataSpacePreferencePut,
    DataSpacePreferenceView,
    Me,
    OnboardingAgentAccepted,
    OnboardingAgentRequest,
    OnboardingStep,
    OnboardingView,
    RoleGrant,
    RoleRef,
)

router = APIRouter()

_TEMPLATES = (
    AgentTemplateChoice(
        ref="analysis",
        title="Phân tích biến động",
        description="Tìm nguyên nhân và bằng chứng cho các thay đổi đáng chú ý.",
        badge={"label": "Đề xuất cho bạn", "recommended": True},
    ),
    AgentTemplateChoice(
        ref="reconcile",
        title="Đối soát số liệu",
        description="So sánh các nguồn và đưa ra danh sách chênh lệch cần xem.",
        badge={"label": "Phổ biến", "recommended": False},
    ),
    AgentTemplateChoice(
        ref="report",
        title="Báo cáo định kỳ",
        description="Tóm tắt chỉ số và điểm cần chú ý cho nhóm của bạn.",
        badge={"label": "Nên có", "recommended": False},
    ),
)


def _onboarding(user: org.AppUser) -> OnboardingView:
    attrs = user.attributes if isinstance(user.attributes, dict) else {}
    saved = attrs.get("onboarding") if isinstance(attrs.get("onboarding"), dict) else {}
    state = str(saved.get("state", "pending"))
    return OnboardingView(
        state=state,
        steps=[
            OnboardingStep(key="domain", label="Bối cảnh", state="done"),
            OnboardingStep(key="sources", label="Nguồn dữ liệu", state="done"),
            OnboardingStep(key="first_agent", label="Agent đầu tiên", state="current"),
            OnboardingStep(key="invite", label="Mời đồng nghiệp", state="todo"),
        ],
        context_text="Gói dữ liệu của bạn đã sẵn sàng để bắt đầu.",
        templates=list(_TEMPLATES),
    )


async def _set_onboarding_state(principal: CurrentPrincipal, state: str) -> OnboardingView:
    async with transaction(ROLE, principal) as session:
        user = await session.get(org.AppUser, principal.user_id, with_for_update=True)
        if user is None:
            raise Problem(403, "forbidden", "No active account in this organisation.")
        attrs = dict(user.attributes) if isinstance(user.attributes, dict) else {}
        attrs["onboarding"] = {
            "state": state,
            "updated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }
        user.attributes = attrs
        await session.flush()
        return _onboarding(user)


@router.get(
    "/v1/me/onboarding",
    response_model=OnboardingView,
    **endpoint("GET", "/v1/me/onboarding", operation_id="getMyOnboarding"),
)
async def get_onboarding(principal: CurrentPrincipal) -> OnboardingView:
    async with transaction(ROLE, principal) as session:
        user = await session.get(org.AppUser, principal.user_id)
        if user is None:
            raise Problem(403, "forbidden", "No active account in this organisation.")
        return _onboarding(user)


@router.post(
    "/v1/me/onboarding:skip",
    response_model=OnboardingView,
    **endpoint("POST", "/v1/me/onboarding:skip", operation_id="skipMyOnboarding"),
)
async def skip_onboarding(principal: CurrentPrincipal) -> OnboardingView:
    return await _set_onboarding_state(principal, "skipped")


@router.post(
    "/v1/me/onboarding:complete",
    response_model=OnboardingView,
    **endpoint("POST", "/v1/me/onboarding:complete", operation_id="completeMyOnboarding"),
)
async def complete_onboarding(principal: CurrentPrincipal) -> OnboardingView:
    return await _set_onboarding_state(principal, "done")


@router.post(
    "/v1/me/onboarding:try-agent",
    status_code=202,
    response_model=OnboardingAgentAccepted,
    **endpoint("POST", "/v1/me/onboarding:try-agent", operation_id="tryMyOnboardingAgent"),
)
async def try_onboarding_agent(
    body: OnboardingAgentRequest,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> OnboardingAgentAccepted:
    template = next((item for item in _TEMPLATES if item.ref == body.template_ref), None)
    if template is None:
        raise Problem(422, "invalid_request", "Unknown onboarding agent template.")
    kind = {"analysis": "analysis", "reconcile": "reconcile", "report": "report"}[template.ref]
    async with transaction("svc_agent", principal) as session:
        owner = agent.AgentDef(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            code=f"onboarding-{template.ref}-{str(uuid7())[-8:]}",
            name=template.title,
            kind=kind,
            owner_user_id=principal.user_id,
            account_user_id=uuid7(),
            status="testing",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(owner)
        await session.flush()
        session.add(
            agent.AgentConfig(
                id=uuid7(),
                org_id=principal.org_id,
                agent_id=owner.id,
                version=1,
                goal=template.description,
                data_scope={},
                skill_ids=[],
                autonomy="propose_approve",
                checkpoints=["before_write_derived", "send_external"],
                denied_actions=["modify_source_data", "send_external"],
                limits={},
                model_by_task={},
                status="testing",
            )
        )
        owner.current_config_version = 1
        run = agent.AgentRun(
            id=uuid7(),
            org_id=principal.org_id,
            agent_id=owner.id,
            config_version=1,
            data_space_id=data_space,
            trigger_kind="test",
            initiated_by=principal.user_id,
            is_test=True,
        )
        session.add(run)
        session.add(
            agent.Outbox(
                id=uuid7(),
                org_id=principal.org_id,
                aggregate_type="agent_run",
                aggregate_id=run.id,
                event_type="run.status_changed",
                payload={
                    "run_id": str(run.id),
                    "agent_id": str(owner.id),
                    "config_version": 1,
                    "is_test": True,
                    "previous_status": None,
                    "status": run.status,
                },
            )
        )
        await session.flush()
        started = (owner.id, run.id)
    await open_agent_account(
        principal,
        account_id=owner.account_user_id,
        agent_id=owner.id,
        data_space_id=data_space,
        code=owner.code,
        name=owner.name,
    )
    response.headers["Location"] = f"/v1/runs/{started[1]}"
    return OnboardingAgentAccepted(agent_id=started[0], run_id=started[1])


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
            .where(org.UserRole.user_id == user.id, user_role_in_force())
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


@router.put(
    "/v1/me/data-space-preference",
    response_model=DataSpacePreferenceView,
    **endpoint(
        "PUT",
        "/v1/me/data-space-preference",
        operation_id="putMyDataSpacePreference",
    ),
)
async def put_data_space_preference(
    body: DataSpacePreferencePut, principal: CurrentPrincipal
) -> DataSpacePreferenceView:
    async with transaction(ROLE, principal) as session:
        user = await session.get(org.AppUser, principal.user_id, with_for_update=True)
        if user is None:
            raise Problem(403, "forbidden", "No active account in this organisation.")
        if not body.open_last_used:
            user.default_data_space_id = None
        elif body.data_space_id is not None:
            mine = memberships(principal.user_id).subquery()
            membership = await session.scalar(
                select(org.DataSpace.id)
                .where(
                    org.DataSpace.id.in_(select(mine.c.data_space_id)),
                )
                .where(
                    org.DataSpace.id == body.data_space_id,
                    org.DataSpace.org_id == principal.org_id,
                    org.DataSpace.deleted_at.is_(None),
                    org.DataSpace.status != "archived",
                )
            )
            if membership is None:
                raise Problem(404, "not_found", "Data Space not found for this user.")
            user.default_data_space_id = body.data_space_id
        user.updated_at = datetime.datetime.now(datetime.UTC)
        await session.flush()
        selected = user.default_data_space_id
    return DataSpacePreferenceView(open_last_used=selected is not None, data_space_id=selected)
