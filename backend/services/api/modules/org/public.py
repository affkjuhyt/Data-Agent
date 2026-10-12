"""What other api modules may ask of the org module.

`capabilities_of` answers for a person who is not the caller: an MCP client acting for its
owner (BR-48) gets exactly what that owner holds in the named Data Space, resolved the same way
as for a signed-in request (identity.OrgIdentityResolver), so a client never holds more than its
owner and loses it the moment the owner does.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import org
from services.api.kernel.auth import DataSpaceNotFound
from services.api.modules.org.identity import ROLE, OrgIdentityResolver


async def capabilities_of(
    org_id: uuid.UUID, user_id: uuid.UUID, data_space_id: uuid.UUID
) -> frozenset[str] | None:
    """The person's capabilities in the Data Space; None when they are not an active user of the
    organisation or not a current member of the space."""
    async with scoped_session(ROLE, org_id) as session:
        external_id = await session.scalar(
            select(org.AppUser.external_id).where(
                org.AppUser.id == user_id, org.AppUser.status == "active"
            )
        )
    if external_id is None:
        return None
    try:
        identity = await OrgIdentityResolver().resolve(
            org_id=org_id, external_id=external_id, data_space=str(data_space_id)
        )
    except DataSpaceNotFound:
        return None
    return identity.capabilities if identity else None
