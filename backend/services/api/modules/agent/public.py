"""What other api modules may ask of the agent module.

`run_usage` and `agents_of_runs` are the agent side of GET /v1/admin/agent-usage (governance, UC-11-04, UC-13-06): run
counts live in agent.agent_run, which svc_governance does not read (definitions.json cross_reads),
so governance asks here
instead of reading the tables.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from dataclasses import dataclass

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import agent
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction

ROLE = "svc_agent"


@dataclass(frozen=True)
class AgentDayRuns:
    day: datetime.date
    agent_id: uuid.UUID
    runs: int
    # Runs that stopped at least once for a person (a checkpoint hit, UC-13-05).
    needs_person: int
    failed: int
    # Runs that succeeded without stopping for a person: what "Tự hoàn tất" counts. runs minus
    # needs_person counted a failed run as done alone.
    auto: int
    # Seconds over the runs of the day that have finished.
    duration_s_sum: float
    finished: int
    cost: decimal.Decimal


@dataclass(frozen=True)
class AgentBudget:
    agent_id: uuid.UUID
    name: str
    data_space_id: uuid.UUID
    owner_user_id: uuid.UUID
    status: str
    monthly_budget: decimal.Decimal | None
    spent_this_month: decimal.Decimal


@dataclass(frozen=True)
class RunUsage:
    days: list[AgentDayRuns]
    agents: list[AgentBudget]


async def run_usage(
    principal: Principal,
    start: datetime.datetime,
    end: datetime.datetime,
    also_agents: set[uuid.UUID] | None = None,
) -> RunUsage:
    """Production runs (not test runs) created in [start, end), per UTC day and agent, across
    every Data Space of the caller's organisation. `agents` covers the agents with runs in the
    window and `also_agents` (e.g. ones with model calls but no new run)."""
    run, hit = agent.AgentRun, agent.AgentCheckpointHit
    day = cast(func.timezone("UTC", run.created_at), Date).label("day")
    in_window = (
        run.org_id == principal.org_id,
        run.is_test.is_(False),
        run.created_at >= start,
        run.created_at < end,
    )
    # One row per run however many times it stopped, so the join does not repeat runs.
    stopped_for_person = (
        select(hit.run_id).where(hit.org_id == principal.org_id).distinct().subquery()
    )
    duration = func.extract("epoch", run.finished_at - run.started_at)
    query = (
        select(
            day,
            run.agent_id,
            func.count(),
            func.count(stopped_for_person.c.run_id),
            func.count().filter(run.status == "failed"),
            func.count().filter(run.status == "succeeded", stopped_for_person.c.run_id.is_(None)),
            func.coalesce(func.sum(duration), 0),
            func.count(duration),
            func.coalesce(func.sum(run.cost), 0),
        )
        .select_from(run)
        .outerjoin(stopped_for_person, stopped_for_person.c.run_id == run.id)
        .where(*in_window)
        .group_by(day, run.agent_id)
        .order_by(day, run.agent_id)
    )
    async with transaction(ROLE, principal) as session:
        rows = (await session.execute(query)).all()
        ids = {r[1] for r in rows} | (also_agents or set())
        defs = (
            (
                await session.scalars(
                    select(agent.AgentDef).where(
                        agent.AgentDef.org_id == principal.org_id, agent.AgentDef.id.in_(ids)
                    )
                )
            ).all()
            if ids
            else []
        )
        spent = await spent_this_month(session, list(ids))
    return RunUsage(
        days=[
            AgentDayRuns(
                day=r[0],
                agent_id=r[1],
                runs=int(r[2]),
                needs_person=int(r[3]),
                failed=int(r[4]),
                auto=int(r[5]),
                duration_s_sum=float(r[6]),
                finished=int(r[7]),
                cost=decimal.Decimal(r[8]),
            )
            for r in rows
        ],
        agents=[
            AgentBudget(
                agent_id=d.id,
                name=d.name,
                data_space_id=d.data_space_id,
                owner_user_id=d.owner_user_id,
                status=d.status,
                monthly_budget=d.monthly_budget,
                spent_this_month=spent.get(d.id, decimal.Decimal(0)),
            )
            for d in defs
        ],
    )


def month_start(now: datetime.datetime | None = None) -> datetime.datetime:
    """The first instant of the current UTC month."""
    now = now or datetime.datetime.now(datetime.UTC)
    return now.astimezone(datetime.UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def spent_this_month(
    session: AsyncSession, agent_ids: list[uuid.UUID]
) -> dict[uuid.UUID, decimal.Decimal]:
    """What each agent's production runs of this UTC month cost, summed from agent_run.cost.
    agent_def.spent_this_month has no writer, so every screen read a 0 (or a seed's figure) from
    it and the "≥90% ngân sách" warning could not fire."""
    if not agent_ids:
        return {}
    run = agent.AgentRun
    rows = await session.execute(
        select(run.agent_id, func.coalesce(func.sum(run.cost), 0))
        .where(
            run.agent_id.in_(agent_ids),
            run.is_test.is_(False),
            run.created_at >= month_start(),
        )
        .group_by(run.agent_id)
    )
    return {agent_id: decimal.Decimal(total) for agent_id, total in rows.tuples()}


async def agents_of_runs(
    principal: Principal, run_ids: set[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """run id → agent id for the named runs of the caller's organisation (unknown ids are left
    out), so a module that records per-run rows (governance.model_usage) can total them per
    agent."""
    if not run_ids:
        return {}
    run = agent.AgentRun
    async with transaction(ROLE, principal) as session:
        found = await session.execute(
            select(run.id, run.agent_id).where(run.org_id == principal.org_id, run.id.in_(run_ids))
        )
        return dict(found.tuples().all())


@dataclass(frozen=True)
class TurnSteps:
    """The persisted trace of an Ask turn (worker turn_trace.py): its run and the steps that
    carry the timeline's view (`output._view`); steps recorded before that contract are left out."""

    run_id: uuid.UUID
    status: str
    started_at: datetime.datetime | None
    finished_at: datetime.datetime | None
    tokens: int
    steps: list[agent.AgentStep]


async def turn_steps(principal: Principal, question_id: uuid.UUID) -> TurnSteps | None:
    """The latest run of the question's turns, for the qa module's GET /v1/questions/{id}/steps;
    the caller has already checked the question is the principal's."""
    async with transaction(ROLE, principal) as session:
        run = await session.scalar(
            select(agent.AgentRun)
            .where(
                agent.AgentRun.org_id == principal.org_id,
                agent.AgentRun.trigger_ref == f"question:{question_id}",
            )
            .order_by(agent.AgentRun.created_at.desc())
            .limit(1)
        )
        if run is None:
            return None
        steps = (
            await session.scalars(
                select(agent.AgentStep)
                .where(agent.AgentStep.run_id == run.id)
                .order_by(agent.AgentStep.seq)
            )
        ).all()
    return TurnSteps(
        run_id=run.id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        tokens=run.tokens or 0,
        steps=[s for s in steps if isinstance(s.output, dict) and "_view" in s.output],
    )
