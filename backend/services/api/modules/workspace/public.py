"""What other api modules may ask the workspace module.

ADR-28: a module asks through the owner's public interface. The agent module records background
tasks whose results land in a session (UC-08-07); svc_agent reads no workspace table, so it asks
here and the answer comes from svc_workspace's own connection.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from libs.dataagent_db.models import workspace as ws
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction
from services.api.modules.workspace import rules
from services.api.modules.workspace.routes import ROLE, _permission


async def session_takes_results(
    principal: Principal, data_space: uuid.UUID, workspace_id: uuid.UUID
) -> bool:
    """True when the session is of this Data Space, not deleted, and the caller may edit it."""
    async with transaction(ROLE, principal) as session:
        item = await session.scalar(
            select(ws.Workspace).where(
                ws.Workspace.id == workspace_id,
                ws.Workspace.org_id == principal.org_id,
                ws.Workspace.data_space_id == data_space,
                ws.Workspace.deleted_at.is_(None),
            )
        )
        if item is None:
            return False
        permission = await _permission(session, principal, item)
    return permission is not None and rules.at_least(permission, "edit")
