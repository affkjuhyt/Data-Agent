"""The organisation's self-approval switch, as the worker applies it.

The api accepts a decision by the author of a change only under `org.organization.allow_self_approval`
(and writes its `self_approval` row); the `org.two_people` trigger enforces the same rule in the
database. The worker, which applies the accepted change, reads the same flag instead of refusing the
author's decision whatever the switch says. Every worker role holds `org_read`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import org


async def allowed(session: AsyncSession, org_id: uuid.UUID) -> bool:
    """True when the org lets an author decide their own change; False when the row is missing."""
    return bool(
        await session.scalar(
            select(org.Organization.allow_self_approval).where(org.Organization.id == org_id)
        )
    )
