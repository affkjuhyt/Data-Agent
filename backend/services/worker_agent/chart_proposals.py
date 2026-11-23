"""A chart the validator blocked because of a declaration, filed for the steward (AL-03).

`answer/chart_check` blocks a share or a stacked total of a measure that does not add up. When
the measure is declared additive (`full` or `semi`) but its SQL is not a plain SUM or COUNT (a
COUNT DISTINCT, an average, a ratio), the declaration is what let the chart through until now:
a `measure_additivity` proposal asks the steward to declare it `none`. Nothing changes until the
steward accepts it (reviews._apply_additivity), and a measure has one such proposal at a time:
its id is derived from the measure, so a second blocked chart files nothing new.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import context
from services.worker_agent.events import Event

logger = logging.getLogger(__name__)

KIND = "measure_additivity"
_NAMESPACE = uuid.UUID("6f1c2f1e-4a0b-4b8e-9a52-1c0d7a3b5e21")


async def file(
    *,
    org_id: uuid.UUID,
    data_space_id: uuid.UUID,
    user_id: uuid.UUID,
    proposal: dict[str, Any],
    reason: str,
) -> uuid.UUID | None:
    """Open the proposal; None when the measure is gone or already declared `none`."""
    from services.worker_agent import proposals

    code = str(proposal.get("measure_code") or "")
    if proposal.get("kind") != KIND or not code:
        return None
    async with scoped_session("svc_context", org_id, user_id) as session:
        measure = await session.scalar(
            select(context.SemanticMeasure).where(
                context.SemanticMeasure.org_id == org_id,
                context.SemanticMeasure.data_space_id == data_space_id,
                context.SemanticMeasure.code == code,
            )
        )
    if measure is None or measure.additivity == "none":
        return None
    pid = uuid.uuid5(_NAMESPACE, f"{org_id}:{measure.id}:none")
    event = Event(
        event_id=pid,
        org_id=org_id,
        event_type="chart_check.blocked",
        aggregate_type="semantic_measure",
        aggregate_id=measure.id,
        payload={"proposal_id": str(pid)},
    )
    return await proposals._open(
        event,
        data_space_id=data_space_id,
        kind=KIND,
        target_service="context",
        target_ref=measure.id,
        title=f"Khai báo “{measure.name}” là không cộng được",
        diff={
            "measure_id": measure.id,
            "measure_code": code,
            "metric_id": proposal.get("metric_id"),
            "from": measure.additivity,
            "to": "none",
            "reason": reason,
        },
        proposed_by=user_id,
        eval_run_id=None,
        impact={"why": "chart_check"},
        impact_level="low",
    )
