"""What other api modules may ask the eval module.

ADR-28: modules call each other through public interfaces, not through each other's tables. A
caller in another module (qa) has no grant on schema eval, so it asks here and the answer comes
from svc_eval's own connection.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select

from libs.dataagent_db.models import eval as evaluation
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction


async def eval_set_accepts_items(
    principal: Principal, data_space: uuid.UUID, eval_set_id: uuid.UUID
) -> bool:
    """True when the eval set is this organisation's, open, and the Data Space's or org-wide."""
    async with transaction("svc_eval", principal) as session:
        found = await session.scalar(
            select(evaluation.EvalSet.id).where(
                evaluation.EvalSet.id == eval_set_id,
                evaluation.EvalSet.org_id == principal.org_id,
                evaluation.EvalSet.status != "archived",
                or_(
                    evaluation.EvalSet.data_space_id == data_space,
                    evaluation.EvalSet.data_space_id.is_(None),
                ),
            )
        )
    return found is not None
