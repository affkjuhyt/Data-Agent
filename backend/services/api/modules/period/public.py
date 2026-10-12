"""What other api modules may ask the period module.

ADR-28: a caller in another module (pack) has no grant on schema period, so it asks here and the
answer comes from svc_period's own connection.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from libs.dataagent_db.models import period as pd
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction


async def closing_periods(principal: Principal, data_space_id: uuid.UUID) -> list[str]:
    """period_label of the Data Space's periods being closed right now (BR-38: no pack upgrade
    while a close runs)."""
    async with transaction("svc_period", principal) as session:
        labels = await session.scalars(
            select(pd.PeriodClose.period_label)
            .where(
                pd.PeriodClose.org_id == principal.org_id,
                pd.PeriodClose.data_space_id == data_space_id,
                pd.PeriodClose.status == "closing",
            )
            .order_by(pd.PeriodClose.period_label)
        )
        return list(labels.all())
