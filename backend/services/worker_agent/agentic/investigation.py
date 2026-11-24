"""The prepared plan of an investigation opened from an alert (AL-11, ANALYTICS_SPEC F-20 B.4).

The api creates the investigation as a workspace (created_from alert) and shows its plan in the
notebook; "Chạy điều tra" sends the plan's question to Ask in a new conversation put in that
workspace (qa.conversation.workspace_id, checked by the api against the asker's rights). The
conversation's first question then runs that plan instead of one the planner writes: the router
gives it to the agent (router.py, INVESTIGATION) and the engine takes it as its plan (engine.plan).

The plan is built again here from the alert the workspace was created from (its
created_from_ref, which no edit changes) by the same function the api used
(libs/contracts/v1/investigation.py), never read back from the workspace's params, which an editor
of the session may rewrite. A later question of the conversation is planned as any other.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from libs.contracts.v1.investigation import investigation
from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import context, monitor, qa
from libs.dataagent_db.models import workspace as ws
from services.worker_agent.agentic import plan as p

MONITOR_ROLE = "svc_monitor"
WORKSPACE_ROLE = "svc_workspace"


async def prepared(
    org_id: uuid.UUID, conversation: qa.Conversation, question: qa.Question
) -> p.Plan | None:
    """The investigation's plan when `question` opens a conversation put in an investigation of
    an alert of the conversation's Data Space; None otherwise."""
    if conversation.workspace_id is None or question.seq != 1:
        return None
    async with scoped_session(WORKSPACE_ROLE, org_id) as session:
        alert_id = await session.scalar(
            select(ws.Workspace.created_from_ref).where(
                ws.Workspace.id == conversation.workspace_id,
                ws.Workspace.data_space_id == conversation.data_space_id,
                ws.Workspace.created_from == "alert",
                ws.Workspace.deleted_at.is_(None),
            )
        )
    if alert_id is None:
        return None
    async with scoped_session(MONITOR_ROLE, org_id) as session:
        found = (
            await session.execute(
                select(
                    monitor.Alert.period_label,
                    monitor.Monitor.metric_id,
                    monitor.Monitor.scope_filter,
                    context.Metric.code,
                    context.Metric.current_version,
                )
                .join(monitor.Monitor, monitor.Monitor.id == monitor.Alert.monitor_id)
                .join(context.Metric, context.Metric.id == monitor.Monitor.metric_id)
                .where(
                    monitor.Alert.id == alert_id,
                    monitor.Monitor.data_space_id == conversation.data_space_id,
                )
            )
        ).first()
        if found is None or not found.period_label:
            return None
        # The name of the version in force, as the api names it in the plan it showed.
        name = await session.scalar(
            select(context.MetricVersion.display_name).where(
                context.MetricVersion.metric_id == found.metric_id,
                context.MetricVersion.version == found.current_version,
            )
        )
    scope = found.scope_filter if isinstance(found.scope_filter, dict) else {}
    split = scope.get("split")
    made = investigation(
        metric_code=found.code,
        metric_name=name or found.code,
        period=found.period_label,
        split=split if isinstance(split, str) and split and split != "none" else None,
    )
    return p.Plan.model_validate(made["plan"])
