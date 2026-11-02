"""What other api modules may ask the skill module.

ADR-28: the inbox names the skill a skill_publish proposal publishes or shares (inbox/describe.py)
by asking here, not by reading skill.skill.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import ColumnElement, and_, or_, select

from libs.dataagent_db.models import skill
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction

ROLE = "svc_skill"


def visible_skills(principal: Principal, data_space: uuid.UUID) -> ColumnElement[bool]:
    """Skills the caller sees from this Data Space: org-wide ones, the Data Space's, and their own
    personal ones. The skill module's reads and the names other modules show use this one."""
    s = skill.Skill
    return and_(
        s.org_id == principal.org_id,
        s.deleted_at.is_(None),
        or_(s.scope == "org", s.data_space_id == data_space),
        or_(s.scope != "personal", s.owner_id == principal.user_id),
    )


async def skill_names(
    principal: Principal,
    data_space: uuid.UUID,
    ids: Iterable[uuid.UUID],
    *,
    under_review: uuid.UUID | None = None,
) -> dict[uuid.UUID, str]:
    """The names of the skills among `ids` the caller sees (visible_skills), by id.

    `under_review` is the skill version a proposal publishes or shares: its skill is named even
    when personal to someone else, since reviewing that publication or sharing is the request.
    No other personal skill is."""
    wanted = set(ids)
    if not wanted:
        return {}
    s = skill.Skill
    seen = visible_skills(principal, data_space)
    if under_review is not None:
        reviewed = (
            select(skill.SkillVersion.skill_id)
            .where(skill.SkillVersion.id == under_review)
            .scalar_subquery()
        )
        seen = or_(
            seen,
            and_(s.org_id == principal.org_id, s.deleted_at.is_(None), s.id == reviewed),
        )
    async with transaction(ROLE, principal) as session:
        rows = await session.execute(select(s.id, s.name).where(s.id.in_(wanted), seen))
        return {r.id: r.name for r in rows.all()}
