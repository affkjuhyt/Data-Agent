"""Opening HITL requests: the `hitl.requested` handler.

Any module asks a person to decide by adding `hitl.requested` to its own outbox, in the
transaction that needs the decision (the payload is built by services/api/modules/inbox/public.py;
the keys below are that contract). This worker holds svc_inbox (docs/v3/09 §1) and writes the
request and its approval steps under the id the module chose, so a redelivered event finds the
row and does nothing.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

from sqlalchemy import select

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import inbox
from services.worker_agent.events import Event, Handler

logger = logging.getLogger(__name__)

ROLE = "svc_inbox"
EVENT_TYPE = "hitl.requested"


def _uuid(value: Any) -> uuid.UUID | None:
    return uuid.UUID(str(value)) if value else None


async def open_request(event: Event) -> None:
    p = event.payload
    request_id = uuid.UUID(str(p["hitl_request_id"]))
    created_by = _uuid(p.get("created_by"))
    async with scoped_session(ROLE, event.org_id, created_by) as session:
        if await session.scalar(
            select(inbox.HitlRequest.id).where(inbox.HitlRequest.id == request_id)
        ):
            return
        session.add(
            inbox.HitlRequest(
                id=request_id,
                org_id=event.org_id,
                data_space_id=_uuid(p.get("data_space_id")),
                kind=p["kind"],
                risk=p.get("risk") or "medium",
                title=p["title"],
                # Who may not decide travels with the request; inbox's :resolve enforces it.
                context={
                    **(p.get("context") or {}),
                    "excluded_approver_ids": p.get("excluded_approver_ids") or [],
                },
                options=p.get("options") or [],
                source_service=p["source_service"],
                source_ref=_uuid(p.get("source_ref")),
                parent_run_label=p.get("parent_run_label"),
                assignee_id=_uuid(p.get("assignee_id")),
                assignee_group=p.get("assignee_group"),
                assignee_capability=p.get("assignee_capability"),
                due_at=datetime.datetime.fromisoformat(p["due_at"]) if p.get("due_at") else None,
                created_by=created_by,
                updated_by=created_by,
            )
        )
        await session.flush()
        for step, capability in enumerate(p.get("approval_steps") or [], start=1):
            session.add(
                inbox.HitlApprovalStep(
                    id=uuid7(),
                    org_id=event.org_id,
                    hitl_request_id=request_id,
                    step=step,
                    approver_role=capability,
                )
            )
        session.add(
            inbox.Outbox(
                id=uuid7(),
                org_id=event.org_id,
                aggregate_type="hitl_request",
                aggregate_id=request_id,
                event_type="hitl.opened",
                # What notify needs to tell the assignees; the request itself is read by id.
                payload={
                    "hitl_request_id": str(request_id),
                    "data_space_id": p.get("data_space_id"),
                    "kind": p["kind"],
                    "risk": p.get("risk") or "medium",
                    "source_service": p["source_service"],
                },
            )
        )
    logger.info("worker-agent | opened hitl_request %s (%s)", request_id, p["kind"])


HANDLERS: dict[str, Handler] = {EVENT_TYPE: open_request}
