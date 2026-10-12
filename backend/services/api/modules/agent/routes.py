"""API-05 Agent group (P13, UC-13-01..08): agents, configurations, triggers, deployments, runs and
memories, as tab 4 A3 describes them.

Runs are executed by worker-agent: the endpoints that start, pause, resume, cancel or re-plan a
run record the request, check the state machine and write `run.status_changed` (tab 1 §9.3) to
agent.outbox in the same transaction; the worker acts on the event at the next step boundary.
Writes that belong to another schema (the agent's org.app_user account, the approval request in
inbox, the eval run, the new qa.plan version) are events too (D1.2).
"""

from __future__ import annotations

import datetime
import re
import uuid
from collections.abc import AsyncIterator, Iterable
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Query, Response
from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import agent, org, qa
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
from services.api.kernel.problems import FieldError, Problem
from services.api.kernel.sse import Event, event_stream, last_event_id
from services.api.modules.agent import rules
from services.api.modules.agent.schemas import (
    AgentCreate,
    AgentKind,
    AgentSummary,
    AgentView,
    ConfigCreate,
    ConfigDraft,
    ConfigView,
    DeploymentCreate,
    DeploymentPatch,
    DeploymentRollback,
    DeploymentView,
    DraftRequest,
    EvalRunCreate,
    MemoryCreate,
    MemoryKind,
    MemoryPatch,
    MemoryScope,
    MemoryStatus,
    MemoryView,
    PlanPut,
    PlanView,
    RunAccepted,
    RunCancel,
    RunStatus,
    RunSummary,
    RunView,
    StepView,
    ToolIn,
    ToolView,
    TrialRunCreate,
    TriggersPut,
    TriggerView,
)

router = APIRouter()
ROLE = "svc_agent"
POLL_AFTER_MS = 1_000

AgentId = Annotated[uuid.UUID, Path(alias="id")]

# The bodies whose fields are all optional may be left out; these stand in for them.
_NO_TRIAL = TrialRunCreate()
_NO_EVAL = EvalRunCreate()
_NO_CANCEL = RunCancel()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _emit(
    session: AsyncSession,
    principal: Principal,
    table: str,
    row_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    """One agent.outbox row in the caller's transaction (D1 rule 8): ids and states only."""
    session.add(
        agent.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=table,
            aggregate_id=row_id,
            event_type=event_type,
            payload={
                k: str(v) if isinstance(v, uuid.UUID | datetime.date) else v
                for k, v in payload.items()
            },
        )
    )


async def _agent(
    session: AsyncSession,
    agent_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> agent.AgentDef:
    query = select(agent.AgentDef).where(
        agent.AgentDef.id == agent_id,
        agent.AgentDef.org_id == principal.org_id,
        agent.AgentDef.data_space_id == data_space,
        agent.AgentDef.deleted_at.is_(None),
    )
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise Problem(404, "not_found", "Agent not found.")
    return row


async def _is_steward(session: AsyncSession, principal: Principal, data_space: uuid.UUID) -> bool:
    """The person in charge of the Data Space: its steward or the backup steward (org, read-only)."""
    row = (
        await session.execute(
            select(org.DataSpace.steward_id, org.DataSpace.backup_steward_id).where(
                org.DataSpace.id == data_space, org.DataSpace.org_id == principal.org_id
            )
        )
    ).first()
    return row is not None and principal.user_id in (row[0], row[1])


async def _owned_agent(
    session: AsyncSession, agent_id: uuid.UUID, principal: Principal, data_space: uuid.UUID
) -> agent.AgentDef:
    """An agent the caller may change: BR-147 makes its owner answerable for it, and the Data Space
    steward decides for it (UC-13-01); create_agent alone does not open someone else's agent."""
    row = await _agent(session, agent_id, principal, data_space, lock=True)
    await _require_owner(session, row, principal, data_space)
    return row


async def _require_owner(
    session: AsyncSession, row: agent.AgentDef, principal: Principal, data_space: uuid.UUID
) -> None:
    if row.owner_user_id != principal.user_id and not await _is_steward(
        session, principal, data_space
    ):
        raise Problem(
            403,
            "forbidden",
            "Only the agent owner or the Data Space steward can change this agent.",
        )


async def _run(
    session: AsyncSession,
    run_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> agent.AgentRun:
    query = select(agent.AgentRun).where(
        agent.AgentRun.id == run_id,
        agent.AgentRun.org_id == principal.org_id,
        agent.AgentRun.data_space_id == data_space,
    )
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise Problem(404, "not_found", "Agent run not found.")
    return row


async def _config(
    session: AsyncSession, agent_id: uuid.UUID, version: int | None
) -> agent.AgentConfig | None:
    query = select(agent.AgentConfig).where(agent.AgentConfig.agent_id == agent_id)
    if version is not None:
        query = query.where(agent.AgentConfig.version == version)
    row: agent.AgentConfig | None = await session.scalar(
        query.order_by(agent.AgentConfig.version.desc()).limit(1)
    )
    return row


async def _config_view(session: AsyncSession, row: agent.AgentConfig) -> ConfigView:
    tools = (
        await session.scalars(
            select(agent.AgentTool)
            .where(agent.AgentTool.agent_config_id == row.id)
            .order_by(agent.AgentTool.tool)
        )
    ).all()
    view = ConfigView.model_validate(row)
    view.tools = [ToolView.model_validate(t) for t in tools]
    return view


async def _add_config(
    session: AsyncSession,
    principal: Principal,
    owner: agent.AgentDef,
    body: ConfigCreate,
) -> agent.AgentConfig:
    """BR-69: every save is a new version; the caller holds the agent row locked, so two saves
    cannot take the same number."""
    rules.check_limits(body.limits)
    rules.check_tools(body.autonomy, [t.model_dump() for t in body.tools])
    version = (
        int(
            await session.scalar(
                select(func.coalesce(func.max(agent.AgentConfig.version), 0)).where(
                    agent.AgentConfig.agent_id == owner.id
                )
            )
        )
        + 1
    )
    row = agent.AgentConfig(
        id=uuid7(),
        org_id=principal.org_id,
        agent_id=owner.id,
        version=version,
        **body.model_dump(exclude={"tools"}),
    )
    session.add(row)
    await session.flush()
    session.add_all(
        agent.AgentTool(
            id=uuid7(), org_id=principal.org_id, agent_config_id=row.id, **t.model_dump()
        )
        for t in body.tools
    )
    # A draft agent serves its newest version; one already running moves to a new version only
    # through a deployment (UC-13-02 step 4, AC-13-02-01).
    if owner.status in ("draft", "testing"):
        owner.current_config_version = version
    await session.flush()
    await session.refresh(row)
    _emit(
        session,
        principal,
        "agent_config",
        row.id,
        "agent_config.created",
        agent_id=owner.id,
        config_version=version,
        status=row.status,
    )
    return row


async def _active_deployment(
    session: AsyncSession, agent_id: uuid.UUID
) -> agent.AgentDeployment | None:
    row: agent.AgentDeployment | None = await session.scalar(
        select(agent.AgentDeployment).where(
            agent.AgentDeployment.agent_id == agent_id,
            agent.AgentDeployment.status.in_(("pending_approval", "rolling")),
        )
    )
    return row


async def _agent_view(session: AsyncSession, row: agent.AgentDef) -> AgentView:
    view = AgentView.model_validate(row)
    counts = await _run_counts(session, [row.id])
    view.active_runs, view.waiting_runs = counts.get(row.id, (0, 0))
    if row.current_config_version is not None:
        current = await _config(session, row.id, row.current_config_version)
        view.current_config = await _config_view(session, current) if current else None
    deployment = await _active_deployment(session, row.id)
    view.active_deployment = DeploymentView.model_validate(deployment) if deployment else None
    return view


_RUNNING = ("queued", "planning", "running")


async def _run_counts(
    session: AsyncSession, agent_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int]]:
    """Per agent: runs in progress and runs waiting for a person (A3 of GET /v1/agents)."""
    rows = (
        await session.execute(
            select(
                agent.AgentRun.agent_id,
                func.count().filter(agent.AgentRun.status.in_(_RUNNING)),
                func.count().filter(agent.AgentRun.status == "waiting_human"),
            )
            .where(agent.AgentRun.agent_id.in_(agent_ids))
            .group_by(agent.AgentRun.agent_id)
        )
    ).all()
    return {r[0]: (int(r[1]), int(r[2])) for r in rows}


def _accepted(response: Response, location: str, row_id: uuid.UUID, status: str) -> RunAccepted:
    response.headers["Location"] = location
    return RunAccepted(id=row_id, status=status, poll_after_ms=POLL_AFTER_MS)


# ---------------------------------------------------------------- agents (UC-13-01)


@router.get(
    "/v1/agents",
    response_model=Page[AgentSummary],
    **endpoint("GET", "/v1/agents", operation_id="listAgents"),
)
async def list_agents(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by(
                "created_at",
                "updated_at",
                "name",
                "code",
                "kind",
                "status",
                default="created_at desc",
            )
        ),
    ],
    status: Annotated[str | None, Query(max_length=50)] = None,
    kind: Annotated[AgentKind | None, Query()] = None,
    owner: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[AgentSummary]:
    query = select(agent.AgentDef).where(
        agent.AgentDef.org_id == principal.org_id,
        agent.AgentDef.data_space_id == data_space,
        agent.AgentDef.deleted_at.is_(None),
    )
    if status:
        query = query.where(agent.AgentDef.status == status)
    if kind:
        query = query.where(agent.AgentDef.kind == kind)
    if owner:
        query = query.where(agent.AgentDef.owner_user_id == owner)
    query = keyset(
        query,
        columns={
            "created_at": agent.AgentDef.created_at,
            "updated_at": agent.AgentDef.updated_at,
            "name": agent.AgentDef.name,
            "code": agent.AgentDef.code,
            "kind": agent.AgentDef.kind,
            "status": agent.AgentDef.status,
        },
        tiebreak=agent.AgentDef.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        served, token = next_page(
            list((await session.scalars(query)).all()), order=order, page=page
        )
        items = [AgentSummary.model_validate(r) for r in served]
        counts = await _run_counts(session, [r.id for r in served])
        for item in items:
            item.active_runs, item.waiting_runs = counts.get(item.id, (0, 0))
    return Page(items=items, next_page_token=token)


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n")


@router.post(
    "/v1/agents:draft-from-text",
    response_model=ConfigDraft,
    **endpoint("POST", "/v1/agents:draft-from-text", operation_id="draftAgentFromText"),
)
async def draft_agent(
    body: DraftRequest, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ConfigDraft:
    """A proposal to edit, nothing saved (UC-13-01 step 2). The model-written proposal (scope,
    skills, clarifying questions of E1) belongs to Agent runtime; until it serves this, the draft
    is the safe skeleton: the description's first sentence as goal, read-only tools, approval
    before any write and the BR-170 ceilings."""
    goal = _SENTENCE_END.split(body.description.strip(), maxsplit=1)[0][:500]
    return ConfigDraft(
        goal=goal,
        data_scope={},
        skill_ids=[],
        autonomy="propose_approve",
        checkpoints=["before_write_derived", "send_external"],
        denied_actions=["modify_source_data", "send_external"],
        limits=dict(rules.LIMIT_CEILINGS),
        model_by_task={},
        tools=[ToolIn(tool="query", permission_level="read")],
        template_ref=body.template_ref,
        out_of_scope=[],
    )


@router.post(
    "/v1/agents",
    status_code=201,
    response_model=AgentView,
    **endpoint("POST", "/v1/agents", operation_id="createAgent"),
)
async def create_agent(
    body: AgentCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> AgentView:
    async with transaction(ROLE, principal) as session:
        row = agent.AgentDef(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            code=body.code,
            name=body.name,
            kind=body.kind,
            # BR-147: the owner is always the real person who creates the agent.
            owner_user_id=principal.user_id,
            # The agent's own account (org.app_user kind = agent) is org's to create: the id is
            # reserved here and org creates the user from the agent.created event.
            account_user_id=uuid7(),
            template_ref=body.template_ref,
            monthly_budget=body.monthly_budget,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        if body.config is not None:
            await _add_config(session, principal, row, body.config)
        # code and name label the account org creates; they are not free text.
        _emit(
            session,
            principal,
            "agent_def",
            row.id,
            "agent.created",
            agent_id=row.id,
            account_user_id=row.account_user_id,
            owner_user_id=row.owner_user_id,
            data_space_id=data_space,
            code=row.code,
            name=row.name,
            status=row.status,
        )
        await session.flush()
        return await _agent_view(session, row)


@router.get(
    "/v1/agents/{id}",
    response_model=AgentView,
    **endpoint("GET", "/v1/agents/{id}", operation_id="getAgent"),
)
async def get_agent(
    agent_id: AgentId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> AgentView:
    async with transaction(ROLE, principal) as session:
        return await _agent_view(session, await _agent(session, agent_id, principal, data_space))


# ---------------------------------------------------------------- configs (UC-13-02)


@router.get(
    "/v1/agents/{id}/configs",
    response_model=Page[ConfigView],
    **endpoint("GET", "/v1/agents/{id}/configs", operation_id="listAgentConfigs"),
)
async def list_configs(
    agent_id: AgentId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(order_by("created_at", "version", "status", default="created_at desc")),
    ],
) -> Page[ConfigView]:
    query = keyset(
        select(agent.AgentConfig).where(agent.AgentConfig.agent_id == agent_id),
        columns={
            "created_at": agent.AgentConfig.created_at,
            "version": agent.AgentConfig.version,
            "status": agent.AgentConfig.status,
        },
        tiebreak=agent.AgentConfig.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        await _agent(session, agent_id, principal, data_space)
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
        items = [await _config_view(session, r) for r in rows]
    return Page(items=items, next_page_token=token)


@router.post(
    "/v1/agents/{id}/configs",
    status_code=201,
    response_model=ConfigView,
    **endpoint("POST", "/v1/agents/{id}/configs", operation_id="createAgentConfig"),
)
async def create_config(
    body: ConfigCreate, agent_id: AgentId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ConfigView:
    async with transaction(ROLE, principal) as session:
        owner = await _owned_agent(session, agent_id, principal, data_space)
        if owner.status == "retired":
            raise Problem(409, "invalid_state", "A retired agent takes no new configuration.")
        row = await _add_config(session, principal, owner, body)
        return await _config_view(session, row)


# ---------------------------------------------------------------- triggers (UC-13-03)


def _trigger_view(row: agent.AgentTrigger, now: datetime.datetime) -> TriggerView:
    view = TriggerView.model_validate(row)
    if row.kind == "schedule" and row.cron:
        view.next_runs = rules.next_runs(row.cron, now)
        fires = rules.daily_fires(row.cron)
        if fires > rules.DAILY_FIRE_WARNING:
            view.warning = (
                f"Fires up to {fires} times a day; check the monthly budget (UC-13-03 E1)."
            )
    return view


@router.put(
    "/v1/agents/{id}/triggers",
    response_model=Page[TriggerView],
    **endpoint("PUT", "/v1/agents/{id}/triggers", operation_id="setAgentTriggers"),
)
async def set_triggers(
    body: TriggersPut, agent_id: AgentId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> Page[TriggerView]:
    for i, trigger in enumerate(body.triggers):
        if trigger.cron is None:
            continue
        try:
            rules.parse_cron(trigger.cron)
        except ValueError as exc:
            raise Problem(
                422,
                "validation_failed",
                "cron is not a five-field cron expression.",
                errors=[
                    FieldError(field=f"triggers.{i}.cron", code="invalid", message="Bad cron.")
                ],
            ) from exc
    async with transaction(ROLE, principal) as session:
        owner = await _owned_agent(session, agent_id, principal, data_space)
        # PUT replaces the whole set; the trigger worker re-registers schedules and consumers
        # from the event.
        await session.execute(
            delete(agent.AgentTrigger).where(agent.AgentTrigger.agent_id == owner.id)
        )
        rows = [
            agent.AgentTrigger(
                id=uuid7(),
                org_id=principal.org_id,
                agent_id=owner.id,
                created_by=principal.user_id,
                updated_by=principal.user_id,
                **t.model_dump(),
            )
            for t in body.triggers
        ]
        session.add_all(rows)
        await session.flush()
        for row in rows:
            await session.refresh(row)
        _emit(
            session,
            principal,
            "agent_def",
            owner.id,
            "agent_trigger.replaced",
            agent_id=owner.id,
            trigger_ids=[str(r.id) for r in rows],
            kinds=[r.kind for r in rows],
        )
        now = _now()
        return Page(items=[_trigger_view(r, now) for r in rows])


# ---------------------------------------------------------------- test and eval runs (UC-13-04)


async def _serving_config(
    session: AsyncSession, owner: agent.AgentDef, version: int | None
) -> agent.AgentConfig:
    if owner.status == "retired":
        raise Problem(409, "invalid_state", "The agent is retired.")
    wanted = version if version is not None else owner.current_config_version
    row = await _config(session, owner.id, wanted)
    if row is None and version is not None:
        raise Problem(
            422,
            "validation_failed",
            f"The agent has no configuration version {version}.",
            errors=[
                FieldError(field="config_version", code="not_found", message="No such version.")
            ],
        )
    if row is None:
        raise Problem(409, "invalid_state", "The agent has no configuration yet.")
    return row


@router.post(
    "/v1/agents/{id}/test-runs",
    status_code=202,
    response_model=RunAccepted,
    **endpoint("POST", "/v1/agents/{id}/test-runs", operation_id="createAgentTestRun"),
)
async def create_test_run(
    agent_id: AgentId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
    # Both fields are optional, so is the body.
    body: Annotated[TrialRunCreate, Body()] = _NO_TRIAL,
) -> RunAccepted:
    async with transaction(ROLE, principal) as session:
        owner = await _owned_agent(session, agent_id, principal, data_space)
        config = await _serving_config(session, owner, body.config_version)
        # is_test: worker-agent runs every write and send tool simulated (UC-13-04 step 1).
        run = agent.AgentRun(
            id=uuid7(),
            org_id=principal.org_id,
            agent_id=owner.id,
            config_version=config.version,
            data_space_id=data_space,
            trigger_kind="test",
            initiated_by=principal.user_id,
            is_test=True,
            as_of=body.as_of,
        )
        session.add(run)
        await session.flush()
        await session.refresh(run)
        _emit(
            session,
            principal,
            "agent_run",
            run.id,
            "run.status_changed",
            run_id=run.id,
            agent_id=owner.id,
            config_version=run.config_version,
            is_test=True,
            previous_status=None,
            status=run.status,
        )
        return _accepted(response, f"/v1/runs/{run.id}", run.id, run.status)


@router.post(
    "/v1/agents/{id}/eval-runs",
    status_code=202,
    response_model=RunAccepted,
    **endpoint("POST", "/v1/agents/{id}/eval-runs", operation_id="createAgentEvalRun"),
)
async def create_eval_run(
    agent_id: AgentId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
    body: Annotated[EvalRunCreate, Body()] = _NO_EVAL,
) -> RunAccepted:
    async with transaction(ROLE, principal) as session:
        owner = await _owned_agent(session, agent_id, principal, data_space)
        config = await _serving_config(session, owner, body.config_version)
        # eval.eval_run is the eval module's: its id is reserved here, recorded on the config, and
        # eval creates and scores the run from the event.
        eval_run_id = uuid7()
        previous = config.status
        config.eval_run_id = eval_run_id
        if config.status == "draft":
            config.status = "testing"
        _emit(
            session,
            principal,
            "agent_config",
            config.id,
            "agent_config.eval_requested",
            agent_id=owner.id,
            config_version=config.version,
            eval_run_id=eval_run_id,
            previous_status=previous,
            status=config.status,
        )
        return _accepted(response, f"/v1/eval-runs/{eval_run_id}", eval_run_id, "queued")


# ---------------------------------------------------------------- deployments (UC-13-05)


async def _serve_version(session: AsyncSession, owner: agent.AgentDef, version: int) -> None:
    """New runs use `version`; runs already going keep theirs (BR-69, UC-13-05 E1)."""
    owner.current_config_version = version
    if owner.status in ("draft", "testing"):
        owner.status = "running"
    configs = (
        await session.scalars(
            select(agent.AgentConfig).where(agent.AgentConfig.agent_id == owner.id)
        )
    ).all()
    for config in configs:
        # "deployed" needs the eval run that passed it (CHECK); a version never evaluated is
        # served all the same, it just keeps its status.
        if config.version == version and config.eval_run_id is not None:
            config.status = "deployed"
        elif config.version != version and config.status == "deployed":
            config.status = "superseded"


async def _deployment(
    session: AsyncSession, deployment_id: uuid.UUID, principal: Principal, data_space: uuid.UUID
) -> tuple[agent.AgentDeployment, agent.AgentDef]:
    row = await session.scalar(
        select(agent.AgentDeployment)
        .where(
            agent.AgentDeployment.id == deployment_id,
            agent.AgentDeployment.org_id == principal.org_id,
        )
        .with_for_update()
    )
    if row is None:
        raise Problem(404, "not_found", "Deployment not found.")
    try:
        owner = await _agent(session, row.agent_id, principal, data_space, lock=True)
    except Problem as exc:
        raise Problem(404, "not_found", "Deployment not found.") from exc
    await _require_owner(session, owner, principal, data_space)
    return row, owner


@router.post(
    "/v1/agents/{id}/deployments",
    status_code=201,
    response_model=DeploymentView,
    **endpoint("POST", "/v1/agents/{id}/deployments", operation_id="createDeployment"),
)
async def create_deployment(
    body: DeploymentCreate,
    agent_id: AgentId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> DeploymentView:
    async with transaction(ROLE, principal) as session:
        owner = await _owned_agent(session, agent_id, principal, data_space)
        if owner.status == "retired":
            raise Problem(409, "invalid_state", "The agent is retired.")
        config = await _config(session, owner.id, body.config_version)
        if config is None:
            raise Problem(
                422,
                "validation_failed",
                f"The agent has no configuration version {body.config_version}.",
                errors=[
                    FieldError(field="config_version", code="not_found", message="No such version.")
                ],
            )
        # UC-13-05 precondition: only a version that passed its eval set goes live.
        if config.status != "passed":
            raise Problem(
                409,
                "invalid_state",
                f"Configuration version {config.version} is {config.status}, not passed.",
            )
        # The partial unique index (one deployment in progress per agent) answers 409 conflict.
        row = agent.AgentDeployment(
            id=uuid7(),
            org_id=principal.org_id,
            agent_id=owner.id,
            config_version=config.version,
            previous_version=owner.current_config_version,
            traffic_pct=body.traffic_pct,
            gates={**rules.DEFAULT_GATES, **body.gates},
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        # Inbox opens the approval request of UC-13-05 step 2 from this event.
        _emit(
            session,
            principal,
            "agent_deployment",
            row.id,
            "agent_deployment.created",
            deployment_id=row.id,
            agent_id=owner.id,
            config_version=row.config_version,
            previous_version=row.previous_version,
            traffic_pct=row.traffic_pct,
            status=row.status,
        )
        return DeploymentView.model_validate(row)


@router.patch(
    "/v1/deployments/{id}",
    response_model=DeploymentView,
    **endpoint("PATCH", "/v1/deployments/{id}", operation_id="patchDeployment"),
)
async def patch_deployment(
    body: DeploymentPatch,
    deployment_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> DeploymentView:
    async with transaction(ROLE, principal) as session:
        row, owner = await _deployment(session, deployment_id, principal, data_space)
        # Traffic moves only once the deployment is approved and rolling (UC-13-05 steps 3-4).
        if row.status != "rolling":
            raise Problem(
                409,
                "invalid_state",
                f"A deployment in status {row.status} takes no traffic change.",
            )
        previous = row.traffic_pct
        row.traffic_pct = body.traffic_pct
        row.updated_by, row.updated_at = principal.user_id, _now()
        if body.traffic_pct == 100:
            row.status, row.finished_at = "completed", _now()
            await _serve_version(session, owner, row.config_version)
            _emit(
                session,
                principal,
                "agent_deployment",
                row.id,
                "agent.deployed",
                deployment_id=row.id,
                agent_id=owner.id,
                config_version=row.config_version,
                previous_version=row.previous_version,
                status=row.status,
            )
        else:
            _emit(
                session,
                principal,
                "agent_deployment",
                row.id,
                "agent_deployment.traffic_changed",
                deployment_id=row.id,
                agent_id=owner.id,
                config_version=row.config_version,
                previous_traffic_pct=previous,
                traffic_pct=row.traffic_pct,
                status=row.status,
            )
        await session.flush()
        return DeploymentView.model_validate(row)


@router.post(
    "/v1/deployments/{id}:rollback",
    response_model=DeploymentView,
    **endpoint("POST", "/v1/deployments/{id}:rollback", operation_id="rollbackDeployment"),
)
async def rollback_deployment(
    body: DeploymentRollback,
    deployment_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> DeploymentView:
    async with transaction(ROLE, principal) as session:
        row, owner = await _deployment(session, deployment_id, principal, data_space)
        if row.status not in ("rolling", "completed"):
            raise Problem(
                409, "invalid_state", f"A deployment in status {row.status} cannot be rolled back."
            )
        # 5a: any older version the owner picks, else the one this deployment replaced.
        target = body.to_version if body.to_version is not None else row.previous_version
        if target is None:
            raise Problem(409, "invalid_state", "There is no earlier version to roll back to.")
        # Only a version that served before may come back: one that went through its eval and
        # approval (deployed, then superseded) or the one this deployment replaced. A draft or a
        # failed version would otherwise go live without the UC-13-05 gates.
        config = await _config(session, owner.id, target)
        served_before = config is not None and (
            config.status in ("deployed", "superseded") or target == row.previous_version
        )
        if target >= row.config_version or not served_before:
            raise Problem(
                422,
                "business_rule_violated",
                f"Version {target} is not an earlier version that served this agent (UC-13-05 5a).",
                errors=[FieldError(field="to_version", code="invalid", message="Bad version.")],
            )
        row.status, row.rollback_reason, row.finished_at = "rolled_back", body.reason, _now()
        row.updated_by, row.updated_at = principal.user_id, _now()
        await _serve_version(session, owner, target)
        _emit(
            session,
            principal,
            "agent_deployment",
            row.id,
            "agent.rolled_back",
            deployment_id=row.id,
            agent_id=owner.id,
            config_version=row.config_version,
            to_version=target,
            status=row.status,
        )
        await session.flush()
        return DeploymentView.model_validate(row)


# ---------------------------------------------------------------- runs (UC-13-06, UC-13-08)


# /v1/runs and /v1/runs/{id}/children list the same rows, so they sort on the same fields.
_RUN_ORDER = ("created_at", "status", "cost", "tokens")


def _run_keyset(query: Select[Any], order: Order, page: PageRequest) -> Select[Any]:
    run = agent.AgentRun
    return keyset(
        query,
        columns={
            "created_at": run.created_at,
            "status": run.status,
            "cost": run.cost,
            "tokens": run.tokens,
        },
        tiebreak=run.id,
        order=order,
        page=page,
    )


@router.get(
    "/v1/runs",
    response_model=Page[RunSummary],
    **endpoint("GET", "/v1/runs", operation_id="listRuns"),
)
async def list_runs(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(order_by(*_RUN_ORDER, default="created_at desc")),
    ],
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[RunStatus | None, Query()] = None,
    from_: Annotated[datetime.datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime.datetime | None, Query()] = None,
    parent_run_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[RunSummary]:
    query = select(agent.AgentRun).where(
        agent.AgentRun.org_id == principal.org_id, agent.AgentRun.data_space_id == data_space
    )
    if agent_id:
        query = query.where(agent.AgentRun.agent_id == agent_id)
    if status:
        query = query.where(agent.AgentRun.status == status)
    if from_:
        query = query.where(agent.AgentRun.created_at >= from_)
    if to:
        query = query.where(agent.AgentRun.created_at < to)
    if parent_run_id:
        query = query.where(agent.AgentRun.parent_run_id == parent_run_id)
    query = _run_keyset(query, order, page)
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[RunSummary.model_validate(r) for r in rows], next_page_token=token)


async def _steps(session: AsyncSession, run_id: uuid.UUID) -> list[agent.AgentStep]:
    return list(
        (
            await session.scalars(
                select(agent.AgentStep)
                .where(agent.AgentStep.run_id == run_id)
                .order_by(agent.AgentStep.seq, agent.AgentStep.attempt)
            )
        ).all()
    )


@router.get(
    "/v1/runs/{id}",
    response_model=RunView,
    **endpoint("GET", "/v1/runs/{id}", operation_id="getRun"),
)
async def get_run(
    run_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    view: Annotated[str, Query(pattern="^(simple|technical)$")] = "simple",
) -> RunView:
    async with transaction(ROLE, principal) as session:
        row = await _run(session, run_id, principal, data_space)
        out = RunView.model_validate(row)
        # simple: the narrative only; technical: the steps as well (SCR-21 "Xem ky thuat").
        if view == "technical":
            out.steps = [StepView.model_validate(s) for s in await _steps(session, row.id)]
    return out


def _run_events(row: agent.AgentRun, steps: Iterable[agent.AgentStep]) -> list[Event]:
    """The run so far as A1.6 events: one id per event, in order, so Last-Event-ID resumes."""
    events: list[tuple[str, dict[str, Any]]] = []
    for step in steps:
        ref = {"run_id": str(row.id), "seq": step.seq, "kind": step.kind, "name": step.name}
        events.append(("step_started", {**ref, "started_at": step.started_at.isoformat()}))
        if step.kind == "checkpoint":
            events.append(("checkpoint", {**ref, "status": step.status}))
        events.append(("step_finished", {**ref, "status": step.status, "summary": step.summary}))
    for entry in row.narrative or []:
        events.append(("narrative", {"run_id": str(row.id), "entry": entry}))
    events.append(("cost", {"run_id": str(row.id), "cost": str(row.cost), "tokens": row.tokens}))
    if row.status in rules.TERMINAL_RUN:
        events.append(
            ("done", {"run_id": str(row.id), "status": row.status, "stop_reason": row.stop_reason})
        )
    return [Event(str(i), name, data) for i, (name, data) in enumerate(events, start=1)]


@router.get(
    "/v1/runs/{id}/events",
    **endpoint("GET", "/v1/runs/{id}/events", operation_id="streamRunEvents"),
)
async def stream_run_events(
    run_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    last_id: Annotated[str | None, Depends(last_event_id)],
) -> Response:
    async with transaction(ROLE, principal) as session:
        row = await _run(session, run_id, principal, data_space)
        events = _run_events(row, await _steps(session, row.id))
    if last_id:
        try:
            after = int(last_id)
        except ValueError as exc:
            raise Problem(400, "invalid_request", "Last-Event-ID must be numeric.") from exc
        events = [e for e in events if int(e.id) > after]

    async def produce() -> AsyncIterator[Event]:
        for event in events:
            yield event

    return event_stream(produce())


@router.get(
    "/v1/runs/{id}/children",
    response_model=Page[RunSummary],
    **endpoint("GET", "/v1/runs/{id}/children", operation_id="listRunChildren"),
)
async def list_children(
    run_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(order_by(*_RUN_ORDER, default="created_at desc")),
    ],
) -> Page[RunSummary]:
    query = _run_keyset(
        select(agent.AgentRun).where(
            agent.AgentRun.parent_run_id == run_id, agent.AgentRun.org_id == principal.org_id
        ),
        order,
        page,
    )
    async with transaction(ROLE, principal) as session:
        await _run(session, run_id, principal, data_space)
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[RunSummary.model_validate(r) for r in rows], next_page_token=token)


async def _intervene(
    session: AsyncSession, run_id: uuid.UUID, principal: Principal, data_space: uuid.UUID
) -> agent.AgentRun:
    """UC-13-06 precondition (AC-13-06-07): only the agent's owner, the run's initiator or the
    Data Space steward steers a run; anyone else gets 403 and the run is untouched."""
    run = await _run(session, run_id, principal, data_space, lock=True)
    if run.initiated_by == principal.user_id:
        return run
    owner = await session.scalar(
        select(agent.AgentDef.owner_user_id).where(agent.AgentDef.id == run.agent_id)
    )
    if owner == principal.user_id:
        return run
    if await _is_steward(session, principal, data_space):
        return run
    raise Problem(
        403,
        "forbidden",
        "Only the agent owner, the run's initiator or the Data Space steward can steer this run.",
    )


def _status_changed(
    session: AsyncSession, principal: Principal, run: agent.AgentRun, previous: str
) -> None:
    _emit(
        session,
        principal,
        "agent_run",
        run.id,
        "run.status_changed",
        run_id=run.id,
        agent_id=run.agent_id,
        config_version=run.config_version,
        is_test=run.is_test,
        previous_status=previous,
        status=run.status,
        stop_reason=run.stop_reason,
    )


async def _set_status(
    run_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    allowed: frozenset[str],
    action: str,
    status: str | None = None,
    reason: str | None = None,
) -> RunView:
    async with transaction(ROLE, principal) as session:
        run = await _intervene(session, run_id, principal, data_space)
        rules.transition(run.status, allowed, action)
        previous = run.status
        run.status = status or rules.resume_status(run.started_at is not None)
        if run.status == "cancelled":
            # BR-170: a finished run carries its stop reason; the intermediate results stay for
            # 30 days (UC-13-06 3a), which is the retention job's, not a delete here.
            run.stop_reason, run.finished_at = "user_cancelled", _now()
            run.stop_detail = {**(run.stop_detail or {}), "cancelled_by": str(principal.user_id)}
            if reason:
                run.stop_detail["reason"] = reason
        _status_changed(session, principal, run, previous)
        await session.flush()
        return RunView.model_validate(run)


@router.post(
    "/v1/runs/{id}:pause",
    response_model=RunView,
    **endpoint("POST", "/v1/runs/{id}:pause", operation_id="pauseRun"),
)
async def pause_run(
    run_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> RunView:
    # The worker stops at the next step boundary when it sees the run paused.
    return await _set_status(run_id, principal, data_space, rules.PAUSABLE, "pause", "paused")


@router.post(
    "/v1/runs/{id}:resume",
    response_model=RunView,
    **endpoint("POST", "/v1/runs/{id}:resume", operation_id="resumeRun"),
)
async def resume_run(
    run_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> RunView:
    return await _set_status(run_id, principal, data_space, frozenset({"paused"}), "resume")


@router.post(
    "/v1/runs/{id}:cancel",
    response_model=RunView,
    **endpoint("POST", "/v1/runs/{id}:cancel", operation_id="cancelRun"),
)
async def cancel_run(
    run_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    body: Annotated[RunCancel, Body()] = _NO_CANCEL,
) -> RunView:
    reason = body.reason
    return await _set_status(
        run_id, principal, data_space, rules.ACTIVE_RUN, "be cancelled", "cancelled", reason
    )


@router.put(
    "/v1/runs/{id}/plan",
    response_model=PlanView,
    **endpoint("PUT", "/v1/runs/{id}/plan", operation_id="editRunPlan"),
)
async def edit_run_plan(
    body: PlanPut,
    run_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> PlanView:
    async with transaction(ROLE, principal) as session:
        run = await _intervene(session, run_id, principal, data_space)
        rules.transition(run.status, rules.PLAN_EDITABLE, "have its plan edited")
        # qa.plan (read-only to svc_agent) holds the run's plan versions; the next number is taken
        # from there and from the edits already requested, so two edits never share one.
        stored = await session.scalar(
            select(func.max(qa.Plan.version)).where(qa.Plan.agent_run_id == run.id)
        )
        requested = [
            e.get("plan_version", 0)
            for e in run.narrative or []
            if isinstance(e, dict) and e.get("event") == "plan.edited"
        ]
        version = max([stored or 0, *requested]) + 1
        # The narrative keeps the edit for the run screen and for the worker, which restarts from
        # the first changed step on resume; qa writes the plan version from the event.
        run.narrative = [
            *(run.narrative or []),
            {
                "event": "plan.edited",
                "plan_version": version,
                "steps": body.steps,
                "edit_note": body.edit_note,
                "edited_by": str(principal.user_id),
                "at": _now().isoformat(),
            },
        ]
        _emit(
            session,
            principal,
            "agent_run",
            run.id,
            "run.plan_edited",
            run_id=run.id,
            agent_id=run.agent_id,
            plan_version=version,
            status=run.status,
        )
        await session.flush()
    return PlanView(
        run_id=run.id,
        version=version,
        steps=body.steps,
        edit_note=body.edit_note,
        edited_by_user=True,
    )


# ---------------------------------------------------------------- memories (UC-13-07)
#
# Who sees and changes a memory: a user-scoped one only its user, an agent-scoped one only the
# agent's owner, a Data Space one everybody in it (anyone may propose a change, which goes back to
# review; only the steward approves, disables or deletes it), and the steward everything. A memory
# outside the caller's rights is answered 404 like a missing one (A1.7).


async def _agent_owner(session: AsyncSession, agent_id: uuid.UUID | None) -> uuid.UUID | None:
    if agent_id is None:
        return None
    owner: uuid.UUID | None = await session.scalar(
        select(agent.AgentDef.owner_user_id).where(agent.AgentDef.id == agent_id)
    )
    return owner


def _visible(row: agent.AgentMemory, principal: Principal, agent_owner: uuid.UUID | None) -> bool:
    if row.scope == "user":
        return row.user_id == principal.user_id
    if row.scope == "agent":
        return agent_owner == principal.user_id
    return True


@router.get(
    "/v1/agents/{id}/memories",
    response_model=Page[MemoryView],
    **endpoint("GET", "/v1/agents/{id}/memories", operation_id="listAgentMemories"),
)
async def list_memories(
    agent_id: AgentId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by(
                "created_at",
                "updated_at",
                "kind",
                "scope",
                "status",
                "use_count",
                default="created_at desc",
            )
        ),
    ],
    kind: Annotated[MemoryKind | None, Query()] = None,
    scope: Annotated[MemoryScope | None, Query()] = None,
    status: Annotated[MemoryStatus | None, Query()] = None,
) -> Page[MemoryView]:
    # The agent's own memories and the ones shared by the whole Data Space (agent_id null).
    query = select(agent.AgentMemory).where(
        agent.AgentMemory.org_id == principal.org_id,
        agent.AgentMemory.data_space_id == data_space,
        (agent.AgentMemory.agent_id == agent_id) | agent.AgentMemory.agent_id.is_(None),
    )
    if kind:
        query = query.where(agent.AgentMemory.kind == kind)
    if scope:
        query = query.where(agent.AgentMemory.scope == scope)
    if status:
        query = query.where(agent.AgentMemory.status == status)
    async with transaction(ROLE, principal) as session:
        owner = await _agent(session, agent_id, principal, data_space)
        if not await _is_steward(session, principal, data_space):
            mine = (agent.AgentMemory.scope == "user") & (
                agent.AgentMemory.user_id == principal.user_id
            )
            visible = (agent.AgentMemory.scope == "data_space") | mine
            if owner.owner_user_id == principal.user_id:
                visible = visible | (agent.AgentMemory.scope == "agent")
            query = query.where(visible)
        query = keyset(
            query,
            columns={
                "created_at": agent.AgentMemory.created_at,
                "updated_at": agent.AgentMemory.updated_at,
                "kind": agent.AgentMemory.kind,
                "scope": agent.AgentMemory.scope,
                "status": agent.AgentMemory.status,
                "use_count": agent.AgentMemory.use_count,
            },
            tiebreak=agent.AgentMemory.id,
            order=order,
            page=page,
        )
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[MemoryView.model_validate(r) for r in rows], next_page_token=token)


@router.post(
    "/v1/agents/{id}/memories",
    status_code=201,
    response_model=MemoryView,
    **endpoint("POST", "/v1/agents/{id}/memories", operation_id="createAgentMemory"),
)
async def create_memory(
    body: MemoryCreate, agent_id: AgentId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> MemoryView:
    rules.check_memory_content(body.content)
    async with transaction(ROLE, principal) as session:
        owner = await _agent(session, agent_id, principal, data_space)
        in_charge = owner.owner_user_id == principal.user_id or await _is_steward(
            session, principal, data_space
        )
        if body.scope == "agent" and not in_charge:
            raise Problem(
                403,
                "forbidden",
                "Only the agent owner or the Data Space steward adds memories to this agent.",
            )
        shared = body.scope == "data_space"
        user_id = None
        if body.scope == "user":
            # A preference is the caller's own; only the people in charge record one for another.
            user_id = body.user_id if body.user_id and in_charge else principal.user_id
        row = agent.AgentMemory(
            id=uuid7(),
            org_id=principal.org_id,
            # A memory for the whole Data Space belongs to no single agent.
            agent_id=None if shared else owner.id,
            data_space_id=data_space,
            scope=body.scope,
            user_id=user_id,
            kind=body.kind,
            content=body.content,
            source_kind="manual",
            expires_at=body.expires_at,
            # BR-76: a shared memory waits for the Data Space owner's review (inbox, from the event).
            status="pending_review" if shared else "active",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        _emit(
            session,
            principal,
            "agent_memory",
            row.id,
            "agent_memory.created",
            memory_id=row.id,
            agent_id=owner.id,
            data_space_id=data_space,
            scope=row.scope,
            status=row.status,
        )
        return MemoryView.model_validate(row)


async def _memory(
    session: AsyncSession, memory_id: uuid.UUID, principal: Principal, data_space: uuid.UUID
) -> tuple[agent.AgentMemory, bool]:
    """The memory if the caller may see it, and whether the caller is the Data Space steward."""
    row = await session.scalar(
        select(agent.AgentMemory)
        .where(
            agent.AgentMemory.id == memory_id,
            agent.AgentMemory.org_id == principal.org_id,
            agent.AgentMemory.data_space_id == data_space,
        )
        .with_for_update()
    )
    if row is None:
        raise Problem(404, "not_found", "Memory not found.")
    steward = await _is_steward(session, principal, data_space)
    if not steward and not _visible(row, principal, await _agent_owner(session, row.agent_id)):
        raise Problem(404, "not_found", "Memory not found.")
    return row, steward


@router.patch(
    "/v1/memories/{id}",
    response_model=MemoryView,
    **endpoint("PATCH", "/v1/memories/{id}", operation_id="patchMemory"),
)
async def patch_memory(
    body: MemoryPatch,
    memory_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> MemoryView:
    changes = body.model_dump(exclude_unset=True)
    if changes.get("content") is not None:
        rules.check_memory_content(changes["content"])
    async with transaction(ROLE, principal) as session:
        row, steward = await _memory(session, memory_id, principal, data_space)
        previous = row.status
        shared = row.scope == "data_space"
        # BR-76 / AC-13-07-03: only the Data Space owner approves, disables or expires a shared
        # memory; anyone else may only propose new content.
        if shared and not steward and ({"status", "expires_at"} & changes.keys()):
            raise Problem(
                403,
                "forbidden",
                "Only the Data Space owner decides on a memory shared by all agents.",
            )
        for key in ("content", "expires_at", "status"):
            if key in changes and (key != "status" or changes[key] is not None):
                setattr(row, key, changes[key])
        # An edit of a shared memory by anyone but its approver goes back to review.
        if shared and "content" in changes and not steward:
            row.status = "pending_review"
        row.updated_by, row.updated_at = principal.user_id, _now()
        if row.status != previous:
            _emit(
                session,
                principal,
                "agent_memory",
                row.id,
                "agent_memory.status_changed",
                memory_id=row.id,
                agent_id=row.agent_id,
                data_space_id=data_space,
                scope=row.scope,
                previous_status=previous,
                status=row.status,
            )
        await session.flush()
        return MemoryView.model_validate(row)


@router.delete(
    "/v1/memories/{id}",
    status_code=204,
    response_class=Response,
    **endpoint("DELETE", "/v1/memories/{id}", operation_id="deleteMemory"),
)
async def delete_memory(
    memory_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> Response:
    async with transaction(ROLE, principal) as session:
        row, steward = await _memory(session, memory_id, principal, data_space)
        if row.scope == "data_space" and not steward:
            raise Problem(
                403, "forbidden", "Only the Data Space owner deletes a memory shared by all agents."
            )
        _emit(
            session,
            principal,
            "agent_memory",
            row.id,
            "agent_memory.deleted",
            memory_id=row.id,
            agent_id=row.agent_id,
            data_space_id=data_space,
            scope=row.scope,
            previous_status=row.status,
        )
        await session.delete(row)
    return Response(status_code=204)
