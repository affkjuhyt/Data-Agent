"""Acting on a decision: the `hitl.resolved` handler.

The inbox records the decision and emits `hitl.resolved` (inbox.outbox); the module that asked
for it changes its own object. Each source is routed by `source_service` to the code that knows
its object, in the role that owns it. The audit export, the answer export and agent checkpoints
(runs.resume_after_checkpoint) are wired; other sources'
its object, in the role that owns it. The audit export, the answer export and the period's
exceptions and manual numbers are wired; other sources'
decisions are acknowledged and left to their own consumer (they are no-ops here, logged).

Idempotent: an object that already left its waiting state is not touched again.
"""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import update

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import audit, qa
from services.worker_agent import exports, runs
from services.worker_agent import exports, periods
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


async def _answer_export(event: Event) -> None:
    """BR-85: an answer export that needed approval runs once a steward approved it; any other
    decision ends it. Only the request recorded on the job moves it."""
    p = event.payload
    job_id = uuid.UUID(str(p["source_ref"]))
    approved = p.get("status") == "approved"
    async with scoped_session("svc_qa", event.org_id) as session:
        job = await session.scalar(
            update(qa.ExportJob)
            .where(
                qa.ExportJob.id == job_id,
                qa.ExportJob.hitl_request_id == uuid.UUID(str(p["hitl_request_id"])),
                qa.ExportJob.status == "waiting_approval",
            )
            .values(status="queued" if approved else "failed")
            .returning(qa.ExportJob.requested_by)
        )
    if job is not None and approved:
        await exports.run_export(event.org_id, job_id, job)


ROUTES = {
    ("audit", "export"): _audit_export,
    ("qa", "export"): _answer_export,
    ("agent", "checkpoint"): runs.resume_after_checkpoint,
    ("period", "period_exception"): periods.exception_decided,
}


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
