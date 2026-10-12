"""Acting on a decision: the `hitl.resolved` handler.

The inbox records the decision and emits `hitl.resolved` (inbox.outbox); the module that asked
for it changes its own object. Each source is routed by `source_service` to the code that knows
its object, in the role that owns it. Only the audit export is wired so far; other sources'
decisions are acknowledged and left to their own consumer (they are no-ops here, logged).

Idempotent: an object that already left its waiting state is not touched again.
"""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import update

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import audit
from services.worker_agent.events import Event, Handler

logger = logging.getLogger(__name__)

EVENT_TYPE = "hitl.resolved"


async def _audit_export(event: Event) -> None:
    """BR-138: an export over the threshold runs once a second person approved it; any other
    decision ends it. The CHECKs of audit.export_job refuse an approver who requested it."""
    p = event.payload
    job_id = uuid.UUID(str(p["source_ref"]))
    approved = p.get("status") == "approved"
    resolver = uuid.UUID(str(p["resolved_by"])) if p.get("resolved_by") else None
    values: dict[str, object] = (
        {
            "status": "queued",
            "approved_by": resolver,
            "approved_at": datetime.datetime.now(datetime.UTC),
        }
        if approved
        else {"status": "failed"}
    )
    # svc_audit_worker may only move waiting_approval → queued/failed (v3_0011's policy), nothing
    # else in the audit database.
    async with scoped_session("svc_audit_worker", event.org_id) as session:
        done = await session.execute(
            update(audit.ExportJob)
            .where(
                audit.ExportJob.id == job_id,
                audit.ExportJob.hitl_request_id == uuid.UUID(str(p["hitl_request_id"])),
                audit.ExportJob.status == "waiting_approval",
            )
            .values(**values, updated_at=datetime.datetime.now(datetime.UTC))
        )
    if done.rowcount:
        logger.info("worker-agent | audit export %s %s", job_id, values["status"])


ROUTES = {("audit", "export"): _audit_export}


async def act_on_decision(event: Event) -> None:
    route = ROUTES.get((str(event.payload.get("source_service")), str(event.payload.get("kind"))))
    if route is None:
        logger.debug(
            "worker-agent | no action here for %s/%s",
            event.payload.get("source_service"),
            event.payload.get("kind"),
        )
        return
    await route(event)


HANDLERS: dict[str, Handler] = {EVENT_TYPE: act_on_decision}
