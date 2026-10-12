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
    return RunUsage(
        days=[
            AgentDayRuns(
                day=r[0],
                agent_id=r[1],
                runs=int(r[2]),
                needs_person=int(r[3]),
                failed=int(r[4]),
                duration_s_sum=float(r[5]),
                finished=int(r[6]),
                cost=decimal.Decimal(r[7]),
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
                spent_this_month=d.spent_this_month,
            )
            for d in defs
        ],
    )


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
