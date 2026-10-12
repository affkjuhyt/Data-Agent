"""Background tasks handed to an agent in words (UC-08-05), table agent.background_task.

A2 lists these under "Theo dõi, báo cáo, thông báo", but the table is schema agent's, so the
routes live here with svc_agent. worker-agent plans the task and asks the requester to confirm
the plan before it runs (pending_confirmation); the endpoints record the request or the
cancellation and write the event to agent.outbox in the same transaction (D1 rule 8).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Path, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import agent
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.problems import FieldError, Problem
from services.api.modules.workspace import session_takes_results

router = APIRouter()
ROLE = "svc_agent"
POLL_AFTER_MS = 2_000

TaskId = Annotated[uuid.UUID, Path(alias="id")]
CANCELLABLE = ("pending_confirmation", "running")
# Without agent_id the task goes to the Data Space's running analysis agent (UC-08-05 step 1).
DEFAULT_AGENT_KIND = "analysis"


class TaskCreate(BaseModel):
    agent_id: uuid.UUID | None = None
    description: str = Field(min_length=1, max_length=4_000)
    due_at: datetime.datetime | None = None
    workspace_id: uuid.UUID | None = None


class TaskAccepted(BaseModel):
    """A1.6: the body of a 202, next to the Location header."""

    id: uuid.UUID
    status: str
    poll_after_ms: int


class TaskView(BaseModel):
    id: uuid.UUID
    requested_by: uuid.UUID
    agent_id: uuid.UUID
    description: str
    due_at: datetime.datetime | None
    workspace_id: uuid.UUID | None
    run_id: uuid.UUID | None
    status: str
    result_artifact_id: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _emit(
    session: AsyncSession,
    principal: Principal,
    row: agent.BackgroundTask,
    event_type: str,
    **payload: Any,
) -> None:
    session.add(
        agent.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type="background_task",
            aggregate_id=row.id,
            event_type=event_type,
            payload={
                k: str(v) if isinstance(v, uuid.UUID | datetime.datetime) else v
                for k, v in {
                    "task_id": row.id,
                    "agent_id": row.agent_id,
                    "status": row.status,
                    **payload,
                }.items()
            },
        )
    )


def _invalid(field: str, message: str) -> Problem:
    return Problem(
        422,
        "validation_failed",
        message,
        errors=[FieldError(field=field, code="invalid", message=message)],
    )


async def _agent_for(
    session: AsyncSession, principal: Principal, data_space: uuid.UUID, agent_id: uuid.UUID | None
) -> agent.AgentDef:
    query = select(agent.AgentDef).where(
        agent.AgentDef.org_id == principal.org_id,
        agent.AgentDef.data_space_id == data_space,
        agent.AgentDef.deleted_at.is_(None),
    )
    if agent_id is not None:
        found = await session.scalar(query.where(agent.AgentDef.id == agent_id))
        if found is None:
            raise _invalid("agent_id", "No such agent in this Data Space.")
        if found.status != "running":
            raise Problem(409, "invalid_state", f"The agent is {found.status}, not running.")
        return found
    default: agent.AgentDef | None = await session.scalar(
        query.where(
            agent.AgentDef.kind == DEFAULT_AGENT_KIND, agent.AgentDef.status == "running"
        ).order_by(agent.AgentDef.created_at)
    )
    if default is None:
        raise _invalid("agent_id", "The Data Space has no running analysis agent; name one.")
    return default


async def _task(
    session: AsyncSession,
    task_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> agent.BackgroundTask:
    query = (
        select(agent.BackgroundTask)
        .join(agent.AgentDef, agent.AgentDef.id == agent.BackgroundTask.agent_id)
        .where(
            agent.BackgroundTask.id == task_id,
            agent.BackgroundTask.org_id == principal.org_id,
            agent.AgentDef.data_space_id == data_space,
            # A task is its requester's (it runs with their delegated rights, BR-126).
            agent.BackgroundTask.requested_by == principal.user_id,
        )
    )
    if lock:
        query = query.with_for_update(of=agent.BackgroundTask)
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Task not found.")
    return row


@router.post(
    "/v1/tasks",
    status_code=202,
    response_model=TaskAccepted,
    **endpoint("POST", "/v1/tasks", operation_id="createBackgroundTask"),
)
async def create_task(
    body: TaskCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace, response: Response
) -> TaskAccepted:
    if body.due_at is not None and body.due_at.tzinfo is None:
        raise _invalid("due_at", "due_at needs a time zone (RFC 3339).")
    # Results land in a session (UC-08-07): one of this Data Space the requester may edit, so a
    # task never writes into another space's or another person's session.
    if body.workspace_id is not None and not await session_takes_results(
        principal, data_space, body.workspace_id
    ):
        raise _invalid("workspace_id", "No session of this Data Space you may edit has that id.")
    async with transaction(ROLE, principal) as session:
        owner = await _agent_for(session, principal, data_space, body.agent_id)
        row = agent.BackgroundTask(
            id=uuid7(),
            org_id=principal.org_id,
            requested_by=principal.user_id,
            agent_id=owner.id,
            description=body.description,
            due_at=body.due_at,
            workspace_id=body.workspace_id,
            status="pending_confirmation",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        # worker-agent plans the task and asks the requester to confirm before it runs.
        _emit(
            session,
            principal,
            row,
            "task.created",
            data_space_id=data_space,
            requested_by=principal.user_id,
            workspace_id=row.workspace_id,
            due_at=row.due_at,
        )
    response.headers["Location"] = f"/v1/tasks/{row.id}"
    return TaskAccepted(id=row.id, status=row.status, poll_after_ms=POLL_AFTER_MS)


@router.get(
    "/v1/tasks/{id}",
    response_model=TaskView,
    **endpoint("GET", "/v1/tasks/{id}", operation_id="getBackgroundTask"),
)
async def get_task(
    task_id: TaskId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> TaskView:
    async with transaction(ROLE, principal) as session:
        row = await _task(session, task_id, principal, data_space)
    return TaskView.model_validate(row, from_attributes=True)


@router.post(
    "/v1/tasks/{id}:cancel",
    response_model=TaskView,
    **endpoint("POST", "/v1/tasks/{id}:cancel", operation_id="cancelBackgroundTask"),
)
async def cancel_task(
    task_id: TaskId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> TaskView:
    async with transaction(ROLE, principal) as session:
        row = await _task(session, task_id, principal, data_space, lock=True)
        if row.status not in CANCELLABLE:
            raise Problem(409, "invalid_state", f"The task is {row.status}.")
        previous, row.status = row.status, "cancelled"
        row.updated_by, row.updated_at = principal.user_id, _now()
        # worker-agent stops the task's run at its next step boundary.
        _emit(
            session,
            principal,
            row,
            "task.cancelled",
            data_space_id=data_space,
            previous_status=previous,
            run_id=row.run_id,
        )
        await session.flush()
        await session.refresh(row)
        return TaskView.model_validate(row, from_attributes=True)
