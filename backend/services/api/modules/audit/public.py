"""What other modules may ask of the audit module: append one row to the log.

The audit log is in its own database (D1.1) and only svc_audit writes it, so a module never
inserts there itself; it calls this, in a transaction of its own (the chain trigger numbers and
links the row, BR-47). A failure here is the caller's to surface: an action that must be audited
is not done unaudited.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import audit

ROLE = "svc_audit"


async def append(
    org_id: uuid.UUID,
    *,
    actor_kind: Literal["user", "agent", "client", "system"],
    actor_id: uuid.UUID | None,
    action: str,
    outcome: Literal["ok", "denied", "error"] = "ok",
    on_behalf_of: uuid.UUID | None = None,
    data_space_id: uuid.UUID | None = None,
    object_kind: str | None = None,
    object_id: uuid.UUID | None = None,
    after: dict[str, Any] | None = None,
    service: str = "api",
) -> None:
    async with scoped_session(ROLE, org_id, actor_id) as session:
        session.add(
            audit.AuditLog(
                org_id=org_id,
                # seq, prev_hash and hash are the chain trigger's.
                seq=0,
                prev_hash="",
                hash="",
                actor_kind=actor_kind,
                actor_id=actor_id,
                on_behalf_of=on_behalf_of,
                data_space_id=data_space_id,
                service=service,
                action=action,
                object_kind=object_kind,
                object_id=object_id,
                outcome=outcome,
                after=after,
            )
        )
