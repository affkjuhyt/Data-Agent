"""What other api modules may ask the skill module.

ADR-28: the inbox names the skill a skill_publish proposal publishes or shares (inbox/describe.py)
by asking here, not by reading skill.skill.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import or_, select

from libs.dataagent_db.models import skill
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction

ROLE = "svc_skill"


async def skill_names(
    principal: Principal, data_space: uuid.UUID, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """The names of the skills among `ids` the Data Space can see (its own and the org-wide ones)."""
    wanted = set(ids)
    if not wanted:
        return {}
    s = skill.Skill
    async with transaction(ROLE, principal) as session:
        rows = await session.execute(
            select(s.id, s.name).where(
                s.id.in_(wanted),
                or_(s.data_space_id == data_space, s.data_space_id.is_(None)),
            )
        )
        return {r.id: r.name for r in rows.all()}
