"""What other modules may ask of the inbox: open a HITL request ("mọi dịch vụ tạo yêu cầu qua
đây", tab 3b inbox.hitl_request).

A module never writes inbox.* itself. It adds a `hitl.requested` event to its own outbox, in the
transaction that makes the request necessary, with the payload built here; worker-agent (the
block that holds svc_inbox, docs/v3/09 §1) opens the request under the id the module chose, once
however often the event is delivered. The module keeps that id (e.g. export_job.hitl_request_id)
and learns the outcome from `hitl.resolved` on inbox.outbox.

The payload keys are the contract with services/worker_agent/hitl.py; change both together.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Sequence
from typing import Any, Literal

EVENT_TYPE = "hitl.requested"

Risk = Literal["low", "medium", "high"]


def hitl_requested(
    *,
    hitl_request_id: uuid.UUID,
    data_space_id: uuid.UUID | None,
    kind: str,
    title: str,
    source_service: str,
    source_ref: uuid.UUID | None,
    created_by: uuid.UUID | None,
    risk: Risk = "medium",
    context: dict[str, Any] | None = None,
    options: Sequence[Any] = (),
    assignee_id: uuid.UUID | None = None,
    assignee_group: str | None = None,
    assignee_capability: str | None = None,
    approval_steps: Sequence[str] = (),
    excluded_approver_ids: Sequence[uuid.UUID] = (),
    due_at: datetime.datetime | None = None,
    parent_run_label: str | None = None,
) -> dict[str, Any]:
    """The payload of a `hitl.requested` event.

    `data_space_id` None is an org-wide request. Exactly who decides: a person, a user group or a
    capability, and for a chain `approval_steps` lists the capability of each step in order (the
    first step's capability is then the request's assignee unless a person is named).
    `excluded_approver_ids` may not decide it at all (BR-62/BR-133: the filer, the closer)."""
    if not (assignee_id or assignee_group or assignee_capability or approval_steps):
        raise ValueError("a HITL request needs an assignee: a person, a group or a capability")
    if approval_steps and not (assignee_id or assignee_group):
        assignee_capability = assignee_capability or approval_steps[0]

    def _id(value: uuid.UUID | None) -> str | None:
        return str(value) if value is not None else None

    return {
        "hitl_request_id": str(hitl_request_id),
        "data_space_id": _id(data_space_id),
        "kind": kind,
        "risk": risk,
        "title": title,
        "context": context or {},
        "options": list(options),
        "source_service": source_service,
        "source_ref": _id(source_ref),
        "created_by": _id(created_by),
        "assignee_id": _id(assignee_id),
        "assignee_group": assignee_group,
        "assignee_capability": assignee_capability,
        "approval_steps": list(approval_steps),
        "excluded_approver_ids": [str(u) for u in excluded_approver_ids],
        "due_at": due_at.isoformat() if due_at else None,
        "parent_run_label": parent_run_label,
    }
