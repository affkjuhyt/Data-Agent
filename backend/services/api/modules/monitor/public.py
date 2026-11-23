"""What other api modules may ask the monitor module (ADR-28: through this, not its tables).

An investigation opened from an alert (AL-11, ANALYTICS_SPEC F-20 B.4) is a workspace the
workspace module creates; it asks here what the alert is about: its metric, its period and the
dimension its monitor is split by, from the caller's Data Space only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from libs.dataagent_db.models import context, monitor
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction
from services.api.modules.monitor.support import ROLE


@dataclass(frozen=True)
class AlertSubject:
    alert_id: uuid.UUID
    monitor_id: uuid.UUID
    metric_code: str
    metric_name: str
    period: str | None
    split: str | None
    summary: str


async def alert_subject(
    principal: Principal, data_space: uuid.UUID, alert_id: uuid.UUID
) -> AlertSubject | None:
    """The alert's subject, or None when it is not an alert of this Data Space."""
    async with transaction(ROLE, principal) as session:
        found = (
            await session.execute(
                select(
                    monitor.Alert,
                    monitor.Monitor,
                    context.Metric.code,
                    context.Metric.current_version,
                )
                .join(monitor.Monitor, monitor.Monitor.id == monitor.Alert.monitor_id)
                .join(context.Metric, context.Metric.id == monitor.Monitor.metric_id)
                .where(
                    monitor.Alert.id == alert_id,
                    monitor.Alert.org_id == principal.org_id,
                    monitor.Monitor.data_space_id == data_space,
                )
            )
        ).first()
        if found is None:
            return None
        alert, row, code, current = found
        # The name of the version in force; a metric without one is named by its code.
        name = await session.scalar(
            select(context.MetricVersion.display_name).where(
                context.MetricVersion.metric_id == row.metric_id,
                context.MetricVersion.version == current,
            )
        )
        scope = row.scope_filter if isinstance(row.scope_filter, dict) else {}
        split = scope.get("split")
        return AlertSubject(
            alert_id=alert.id,
            monitor_id=row.id,
            metric_code=code,
            metric_name=name or code,
            period=alert.period_label,
            split=split if isinstance(split, str) and split and split != "none" else None,
            summary=alert.summary,
        )
