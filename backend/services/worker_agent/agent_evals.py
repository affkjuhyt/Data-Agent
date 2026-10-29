"""An agent version's eval run (UC-13-04, R1-12): the gate before it may be deployed.

POST /v1/agents/{id}/eval-runs reserves an eval run id on the config and emits
agent_config.eval_requested. Here the eval set is chosen, the run made under that id and scored
through the production QA path (eval_runs.run_eval), and a run at the bar (BR-175, as for a data
product: at least the set's `min_items` items, a pass rate of at least its `pass_threshold`) moves
the config to `passed`, the one status POST /v1/agents/{id}/deployments accepts. The items are
answered as the agent's own account and within the version's data_scope, so the version passes on
what it would itself be shown, not on what the person who asked for the run may read. Below the bar the
config stays `testing` and the run says which items missed.

The set is the agent's own (subject_type agent) when it has one, else its Data Space's (subject
type context); a set of another Data Space, or of none, is never used (R1-11). The baseline is
the eval run of the version the agent serves now, so the run shows which items changed against
it (AC-13-04-02).
"""

from __future__ import annotations

import uuid

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import agent
from libs.dataagent_db.models import eval as ev
from services.worker_agent import eval_runs
from services.worker_agent.events import Event, Handler
from services.worker_agent.runs import _tell

HANDLER = "agent_config.eval_requested"
AGENT_ROLE = "svc_agent"


async def _eval_set(
    session: AsyncSession, agent_id: uuid.UUID, data_space_id: uuid.UUID
) -> ev.EvalSet | None:
    found: ev.EvalSet | None = await session.scalar(
        select(ev.EvalSet)
        .where(
            ev.EvalSet.status == "active",
            ev.EvalSet.data_space_id == data_space_id,
            ((ev.EvalSet.subject_type == "agent") & (ev.EvalSet.subject_id == agent_id))
            | (ev.EvalSet.subject_type == "context"),
        )
        .order_by(
            case((ev.EvalSet.subject_type == "agent", 0), else_=1),
            ev.EvalSet.created_at.desc(),
            ev.EvalSet.id,
        )
        .limit(1)
    )
    return found


async def evaluate_agent(event: Event) -> None:
    agent_id = uuid.UUID(str(event.payload["agent_id"]))
    version = int(event.payload["config_version"])
    run_id = uuid.UUID(str(event.payload["eval_run_id"]))
    async with scoped_session(AGENT_ROLE, event.org_id) as session:
        owner = await session.get(agent.AgentDef, agent_id)
        config = await session.scalar(
            select(agent.AgentConfig).where(
                agent.AgentConfig.agent_id == agent_id, agent.AgentConfig.version == version
            )
        )
        # A later request reserved another run on this config: that one is the run to make.
        if owner is None or config is None or config.eval_run_id != run_id:
            return
        serving = await session.scalar(
            select(agent.AgentConfig.eval_run_id).where(
                agent.AgentConfig.agent_id == agent_id,
                agent.AgentConfig.version == owner.current_config_version,
                agent.AgentConfig.version != version,
            )
        )
        config_id, space_id = config.id, owner.data_space_id
        account, data_scope = owner.account_user_id, dict(config.data_scope or {})
        requested_by = uuid.UUID(str(event.payload.get("requested_by") or owner.owner_user_id))
        tell = [owner.owner_user_id]
    link = f"/agents/agent/{agent_id}"

    async with scoped_session(eval_runs.ROLE, event.org_id, requested_by) as session:
        # An event handled twice finds its run made (and maybe scored): run_eval then does
        # nothing, and only the gate below is applied again.
        if await session.get(ev.EvalRun, run_id) is None:
            eval_set = await _eval_set(session, agent_id, space_id)
            if eval_set is None:
                await _tell(
                    event.org_id,
                    requested_by,
                    tell,
                    kind="alert",
                    title=f"Chưa đánh giá được phiên bản {version} của agent",
                    body="Data Space của agent chưa có bộ đánh giá nào; người phụ trách cần tạo "
                    "bộ đánh giá trước khi agent được triển khai.",
                    link=link,
                    data_space_id=space_id,
                    source_ref=run_id,
                )
                return
            count = await session.scalar(
                select(func.count())
                .select_from(ev.EvalItem)
                .where(ev.EvalItem.eval_set_id == eval_set.id, ev.EvalItem.status == "active")
            )
            baseline = None
            if serving is not None:
                baseline = await session.scalar(
                    select(ev.EvalRun.id).where(
                        ev.EvalRun.id == serving,
                        ev.EvalRun.eval_set_id == eval_set.id,
                        ev.EvalRun.status == "completed",
                    )
                )
            session.add(
                ev.EvalRun(
                    id=run_id,
                    org_id=event.org_id,
                    eval_set_id=eval_set.id,
                    subject_version=f"agent {agent_id} v{version}",
                    trigger="deploy",
                    trigger_ref=config_id,
                    item_count=int(count or 0),
                    baseline_run_id=baseline,
                    status="running",
                )
            )

    await eval_runs.run_eval(
        Event(
            event.event_id,
            event.org_id,
            eval_runs.HANDLER,
            "eval_run",
            run_id,
            {
                "eval_run_id": str(run_id),
                "requested_by": str(requested_by),
                "answer_as": str(account),
                "data_scope": data_scope,
                "data_space_id": str(space_id),
                "trigger": "deploy",
            },
        )
    )

    async with scoped_session(eval_runs.ROLE, event.org_id, requested_by) as session:
        found = await session.execute(
            select(ev.EvalRun, ev.EvalSet)
            .join(ev.EvalSet, ev.EvalSet.id == ev.EvalRun.eval_set_id)
            .where(ev.EvalRun.id == run_id)
        )
        scored, eval_set = found.one()
        rate = float(scored.pass_rate or 0)
        at_the_bar = (
            scored.status == "completed"
            and scored.item_count >= eval_set.min_items
            and rate >= float(eval_set.pass_threshold)
        )
        why = (
            f"{scored.passed}/{scored.item_count} tình huống đạt ({rate:.1%}); "
            f"cần ít nhất {eval_set.min_items} tình huống và {float(eval_set.pass_threshold):.0%}."
        )

    async with scoped_session(AGENT_ROLE, event.org_id) as session:
        config = await session.scalar(
            select(agent.AgentConfig).where(agent.AgentConfig.id == config_id).with_for_update()
        )
        if config is None or config.eval_run_id != run_id or config.status != "testing":
            return
        if at_the_bar:
            config.status = "passed"
    await _tell(
        event.org_id,
        requested_by,
        tell,
        kind="task_done" if at_the_bar else "alert",
        title=(
            f"Phiên bản {version} của agent đạt bộ đánh giá"
            if at_the_bar
            else f"Phiên bản {version} của agent chưa đạt bộ đánh giá"
        ),
        body=why + (" Có thể triển khai." if at_the_bar else " Chưa triển khai được."),
        link=link,
        data_space_id=space_id,
        source_ref=run_id,
    )


HANDLERS: dict[str, Handler] = {HANDLER: evaluate_agent}
