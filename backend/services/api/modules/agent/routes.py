from __future__ import annotations

import datetime
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy import func, select

from libs.dataagent_db.models import agent
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import Page, PageRequest, encode_page_token, page_request
from services.api.kernel.problems import Problem
from services.api.kernel.sse import Event, event_stream, last_event_id
from services.api.modules.agent.schemas import AgentCreate, AgentView, ConfigCreate, ConfigView, MemoryCreate, MemoryPatch, MemoryView, RunPlan, RunView, TriggerCreate

router = APIRouter()
ROLE = "svc_agent"


async def _agent(agent_id: uuid.UUID, principal: CurrentPrincipal) -> agent.AgentDef:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(agent.AgentDef).where(agent.AgentDef.id == agent_id, agent.AgentDef.org_id == principal.org_id, agent.AgentDef.deleted_at.is_(None)))
    if row is None:
        raise Problem(404, "not_found", "Agent not found.")
    return row


def _run_view(row: agent.AgentRun) -> RunView:
    return RunView.model_validate(row, from_attributes=True)


@router.get("/v1/agents", response_model=Page[AgentView], **endpoint("GET", "/v1/agents", operation_id="listAgents"))
async def list_agents(principal: CurrentPrincipal, data_space: CurrentDataSpace, page: Annotated[PageRequest, Depends(page_request)]) -> Page[AgentView]:
    query = select(agent.AgentDef).where(agent.AgentDef.org_id == principal.org_id, agent.AgentDef.data_space_id == data_space, agent.AgentDef.deleted_at.is_(None)).order_by(agent.AgentDef.created_at.desc()).limit(page.page_size + 1)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[:page.page_size]
    return Page(items=[AgentView.model_validate(row, from_attributes=True) for row in rows], next_page_token=encode_page_token({"created_at": rows[-1].created_at.isoformat()} if more else None))


@router.post("/v1/agents", status_code=201, response_model=AgentView, **endpoint("POST", "/v1/agents", operation_id="createAgent"))
async def create_agent(body: AgentCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace) -> AgentView:
    async with transaction(ROLE, principal) as session:
        row = agent.AgentDef(org_id=principal.org_id, data_space_id=data_space, owner_user_id=principal.user_id, created_by=principal.user_id, updated_by=principal.user_id, **body.model_dump())
        session.add(row)
        await session.flush()
    return AgentView.model_validate(row, from_attributes=True)


@router.post("/v1/agents:draft-from-text", response_model=AgentView, status_code=201, **endpoint("POST", "/v1/agents:draft-from-text", operation_id="draftAgentFromText"))
async def draft_agent(body: dict[str, str], principal: CurrentPrincipal, data_space: CurrentDataSpace) -> AgentView:
    async with transaction(ROLE, principal) as session:
        row = agent.AgentDef(org_id=principal.org_id, data_space_id=data_space, owner_user_id=principal.user_id, account_user_id=principal.user_id, code=f"draft-{uuid.uuid4().hex[:8]}", name=body.get("text", "Agent draft")[:500], kind="custom", created_by=principal.user_id, updated_by=principal.user_id)
        session.add(row)
        await session.flush()
    return AgentView.model_validate(row, from_attributes=True)


@router.get("/v1/agents/{id}", response_model=AgentView, **endpoint("GET", "/v1/agents/{id}", operation_id="getAgent"))
async def get_agent(agent_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> AgentView:
    return AgentView.model_validate(await _agent(agent_id, principal), from_attributes=True)


@router.get("/v1/agents/{id}/configs", response_model=list[ConfigView], **endpoint("GET", "/v1/agents/{id}/configs", operation_id="listAgentConfigs"))
async def list_configs(agent_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> list[ConfigView]:
    await _agent(agent_id, principal)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(select(agent.AgentConfig).where(agent.AgentConfig.agent_id == agent_id, agent.AgentConfig.org_id == principal.org_id).order_by(agent.AgentConfig.version.desc()))).all())
    return [ConfigView.model_validate(row, from_attributes=True) for row in rows]


@router.post("/v1/agents/{id}/configs", status_code=201, response_model=ConfigView, **endpoint("POST", "/v1/agents/{id}/configs", operation_id="createAgentConfig"))
async def create_config(body: ConfigCreate, agent_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> ConfigView:
    await _agent(agent_id, principal)
    async with transaction(ROLE, principal) as session:
        version = int(await session.scalar(select(func.coalesce(func.max(agent.AgentConfig.version), 0)).where(agent.AgentConfig.agent_id == agent_id))) + 1
        row = agent.AgentConfig(org_id=principal.org_id, agent_id=agent_id, version=version, **body.model_dump())
        session.add(row)
        await session.flush()
    return ConfigView.model_validate(row, from_attributes=True)


@router.put("/v1/agents/{id}/triggers", response_model=list[dict], **endpoint("PUT", "/v1/agents/{id}/triggers", operation_id="setAgentTriggers"))
async def set_triggers(body: list[TriggerCreate], agent_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> list[dict]:
    await _agent(agent_id, principal)
    async with transaction(ROLE, principal) as session:
        old = list((await session.scalars(select(agent.AgentTrigger).where(agent.AgentTrigger.agent_id == agent_id, agent.AgentTrigger.org_id == principal.org_id))).all())
        for row in old:
            await session.delete(row)
        rows = [agent.AgentTrigger(org_id=principal.org_id, agent_id=agent_id, created_by=principal.user_id, updated_by=principal.user_id, **item.model_dump()) for item in body]
        session.add_all(rows)
        await session.flush()
    return [{"id": row.id, "kind": row.kind, "enabled": row.enabled} for row in rows]


async def _create_run(agent_id: uuid.UUID, principal: CurrentPrincipal, is_test: bool, trigger_kind: str = "manual") -> agent.AgentRun:
    item = await _agent(agent_id, principal)
    async with transaction(ROLE, principal) as session:
        row = agent.AgentRun(org_id=principal.org_id, agent_id=item.id, config_version=item.current_config_version or 1, data_space_id=item.data_space_id, trigger_kind=trigger_kind, initiated_by=principal.user_id, is_test=is_test)
        session.add(row)
        await session.flush()
    return row


@router.post("/v1/agents/{id}/test-runs", status_code=202, response_model=RunView, **endpoint("POST", "/v1/agents/{id}/test-runs", operation_id="createAgentTestRun"))
async def create_test_run(agent_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> RunView:
    return _run_view(await _create_run(agent_id, principal, True, "test"))


@router.post("/v1/agents/{id}/eval-runs", status_code=202, response_model=RunView, **endpoint("POST", "/v1/agents/{id}/eval-runs", operation_id="createAgentEvalRun"))
async def create_eval_run(agent_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> RunView:
    return _run_view(await _create_run(agent_id, principal, True, "manual"))


@router.get("/v1/runs", response_model=Page[RunView], **endpoint("GET", "/v1/runs", operation_id="listRuns"))
async def list_runs(principal: CurrentPrincipal, data_space: CurrentDataSpace, page: Annotated[PageRequest, Depends(page_request)]) -> Page[RunView]:
    query = select(agent.AgentRun).where(agent.AgentRun.org_id == principal.org_id, agent.AgentRun.data_space_id == data_space).order_by(agent.AgentRun.created_at.desc()).limit(page.page_size + 1)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[:page.page_size]
    return Page(items=[_run_view(row) for row in rows], next_page_token=encode_page_token({"created_at": rows[-1].created_at.isoformat()} if more else None))


async def _run(run_id: uuid.UUID, principal: CurrentPrincipal) -> agent.AgentRun:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(agent.AgentRun).where(agent.AgentRun.id == run_id, agent.AgentRun.org_id == principal.org_id))
    if row is None:
        raise Problem(404, "not_found", "Agent run not found.")
    return row


@router.get("/v1/runs/{id}", response_model=RunView, **endpoint("GET", "/v1/runs/{id}", operation_id="getRun"))
async def get_run(run_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> RunView:
    return _run_view(await _run(run_id, principal))


@router.get("/v1/runs/{id}/events", **endpoint("GET", "/v1/runs/{id}/events", operation_id="streamRunEvents"))
async def stream_run_events(run_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal, last_id: Annotated[str | None, Depends(last_event_id)]):
    row = await _run(run_id, principal)
    events = [Event("1", "run.status", {"run_id": str(row.id), "status": row.status})]
    if last_id:
        events = [event for event in events if int(event.id) > int(last_id)]
    async def produce() -> AsyncIterator[Event]:
        for event in events:
            yield event
    return event_stream(produce())


@router.get("/v1/runs/{id}/children", response_model=list[RunView], **endpoint("GET", "/v1/runs/{id}/children", operation_id="listRunChildren"))
async def list_children(run_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> list[RunView]:
    await _run(run_id, principal)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(select(agent.AgentRun).where(agent.AgentRun.parent_run_id == run_id, agent.AgentRun.org_id == principal.org_id))).all())
    return [_run_view(row) for row in rows]


async def _change_run(run_id: uuid.UUID, status: str, reason: str, principal: CurrentPrincipal) -> RunView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(agent.AgentRun).where(agent.AgentRun.id == run_id, agent.AgentRun.org_id == principal.org_id))
        if row is None:
            raise Problem(404, "not_found", "Agent run not found.")
        row.status, row.stop_reason = status, reason
        row.finished_at = datetime.datetime.now(datetime.UTC) if status == "cancelled" else row.finished_at
        await session.flush()
    return _run_view(row)


@router.post("/v1/runs/{id}:pause", response_model=RunView, **endpoint("POST", "/v1/runs/{id}:pause", operation_id="pauseRun"))
async def pause_run(run_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> RunView:
    return await _change_run(run_id, "paused", "user_cancelled", principal)


@router.post("/v1/runs/{id}:resume", response_model=RunView, **endpoint("POST", "/v1/runs/{id}:resume", operation_id="resumeRun"))
async def resume_run(run_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> RunView:
    return await _change_run(run_id, "running", "user_cancelled", principal)


@router.post("/v1/runs/{id}:cancel", response_model=RunView, **endpoint("POST", "/v1/runs/{id}:cancel", operation_id="cancelRun"))
async def cancel_run(run_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> RunView:
    return await _change_run(run_id, "cancelled", "user_cancelled", principal)


@router.put("/v1/runs/{id}/plan", response_model=RunView, **endpoint("PUT", "/v1/runs/{id}/plan", operation_id="editRunPlan"))
async def edit_run_plan(body: RunPlan, run_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> RunView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(agent.AgentRun).where(agent.AgentRun.id == run_id, agent.AgentRun.org_id == principal.org_id))
        if row is None:
            raise Problem(404, "not_found", "Agent run not found.")
        row.narrative = [*row.narrative, {"event": "plan.edited", "plan": body.plan}]
        await session.flush()
    return _run_view(row)


@router.post("/v1/agents/{id}/deployments", response_model=dict, status_code=202, **endpoint("POST", "/v1/agents/{id}/deployments", operation_id="createDeployment"))
async def create_deployment(agent_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> dict:
    item = await _agent(agent_id, principal)
    async with transaction(ROLE, principal) as session:
        deployment = agent.AgentDeployment(org_id=principal.org_id, agent_id=item.id, config_version=item.current_config_version or 1, gates={}, created_by=principal.user_id, updated_by=principal.user_id)
        session.add(deployment)
        await session.flush()
    return {"id": deployment.id, "status": deployment.status, "config_version": deployment.config_version}


@router.patch("/v1/deployments/{id}", response_model=dict, **endpoint("PATCH", "/v1/deployments/{id}", operation_id="patchDeployment"))
async def patch_deployment(body: dict[str, Any], deployment_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> dict:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(agent.AgentDeployment).where(agent.AgentDeployment.id == deployment_id, agent.AgentDeployment.org_id == principal.org_id))
        if row is None:
            raise Problem(404, "not_found", "Deployment not found.")
        if "traffic_pct" in body:
            row.traffic_pct = int(body["traffic_pct"])
        row.updated_by = principal.user_id
        await session.flush()
    return {"id": row.id, "status": row.status, "traffic_pct": row.traffic_pct}


@router.post("/v1/deployments/{id}:rollback", response_model=dict, **endpoint("POST", "/v1/deployments/{id}:rollback", operation_id="rollbackDeployment"))
async def rollback_deployment(body: dict[str, str] | None, deployment_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> dict:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(agent.AgentDeployment).where(agent.AgentDeployment.id == deployment_id, agent.AgentDeployment.org_id == principal.org_id))
        if row is None:
            raise Problem(404, "not_found", "Deployment not found.")
        row.status, row.rollback_reason, row.updated_by = "rolled_back", (body or {}).get("reason", "User rollback"), principal.user_id
        await session.flush()
    return {"id": row.id, "status": row.status, "rollback_reason": row.rollback_reason}


@router.get("/v1/agents/{id}/memories", response_model=list[MemoryView], **endpoint("GET", "/v1/agents/{id}/memories", operation_id="listAgentMemories"))
async def list_memories(agent_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> list[MemoryView]:
    await _agent(agent_id, principal)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(select(agent.AgentMemory).where(agent.AgentMemory.agent_id == agent_id, agent.AgentMemory.org_id == principal.org_id, agent.AgentMemory.status == "active").order_by(agent.AgentMemory.created_at.desc()))).all())
    return [MemoryView.model_validate(row, from_attributes=True) for row in rows]


@router.post("/v1/agents/{id}/memories", status_code=201, response_model=MemoryView, **endpoint("POST", "/v1/agents/{id}/memories", operation_id="createAgentMemory"))
async def create_memory(body: MemoryCreate, agent_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal, data_space: CurrentDataSpace) -> MemoryView:
    await _agent(agent_id, principal)
    async with transaction(ROLE, principal) as session:
        row = agent.AgentMemory(org_id=principal.org_id, agent_id=agent_id, data_space_id=data_space, user_id=principal.user_id if body.scope == "user" else None, created_by=principal.user_id, updated_by=principal.user_id, **body.model_dump(exclude={"agent_id"}))
        session.add(row)
        await session.flush()
    return MemoryView.model_validate(row, from_attributes=True)


@router.patch("/v1/memories/{id}", response_model=MemoryView, **endpoint("PATCH", "/v1/memories/{id}", operation_id="patchMemory"))
async def patch_memory(body: MemoryPatch, memory_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> MemoryView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(agent.AgentMemory).where(agent.AgentMemory.id == memory_id, agent.AgentMemory.org_id == principal.org_id))
        if row is None:
            raise Problem(404, "not_found", "Memory not found.")
        for key, value in body.model_dump(exclude_unset=True).items():
            setattr(row, key, value)
        row.updated_by = principal.user_id
        await session.flush()
    return MemoryView.model_validate(row, from_attributes=True)


@router.delete("/v1/memories/{id}", response_model=None, **endpoint("DELETE", "/v1/memories/{id}", operation_id="deleteMemory"))
async def delete_memory(memory_id: Annotated[uuid.UUID, Path(alias="id")], principal: CurrentPrincipal) -> None:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(select(agent.AgentMemory).where(agent.AgentMemory.id == memory_id, agent.AgentMemory.org_id == principal.org_id))
        if row is None:
            raise Problem(404, "not_found", "Memory not found.")
        row.status, row.updated_by = "disabled", principal.user_id
