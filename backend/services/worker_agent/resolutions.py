"""Acting on a decision: the `hitl.resolved` handler.

The inbox records the decision and emits `hitl.resolved` (inbox.outbox); the module that asked
for it changes its own object. Each source is routed by `source_service` to the code that knows
its object, in the role that owns it. The audit export, the answer export, agent checkpoints
(runs.resume_after_checkpoint) and the period's exceptions and manual numbers are wired; other
sources'
decisions are acknowledged and left to their own consumer (they are no-ops here, logged).

Idempotent: an object that already left its waiting state is not touched again.
"""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import case, update

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import audit, qa
from services.worker_agent import audit_exports, exports, periods, runs, self_approval
from services.worker_agent.events import Event, Handler

logger = logging.getLogger(__name__)

EVENT_TYPE = "hitl.resolved"


async def _audit_export(event: Event) -> None:
    """BR-138: an export over the threshold runs once a second person approved it; any other
    decision ends it. The CHECKs of audit.export_job refuse an approver who requested it unless
    the row says self_approved, which only an org with the self-approval switch on earns."""
    p = event.payload
    job_id = uuid.UUID(str(p["source_ref"]))
    approved = p.get("status") == "approved"
    resolver = uuid.UUID(str(p["resolved_by"])) if p.get("resolved_by") else None
    now = datetime.datetime.now(datetime.UTC)
    values: dict[str, object] = {"status": "failed"}
    if approved:
        # The audit database cannot read the switch, so the worker reads it in the main one.
        async with scoped_session("svc_inbox", event.org_id) as session:
            self_ok = await self_approval.allowed(session, event.org_id)
        own = audit.ExportJob.requested_by == resolver
        if self_ok:
            values = {
                "status": "queued",
                "approved_by": resolver,
                "approved_at": now,
                # The API let the requester decide only because the switch was on; the CHECK
                # wants that said on the row.
                "self_approved": own,
            }
        else:
            # The requester approved their own export but the switch is off now (turned off
            # after the decision, or the event did not come through the API's check). The
            # request is already resolved, so nothing would ever decide the job again: it is
            # failed rather than left waiting, and a fresh export asks a second person.
            values = {
                "status": case((own, "failed"), else_="queued"),
                "approved_by": case((own, None), else_=resolver),
                "approved_at": case((own, None), else_=now),
            }
    # svc_audit_worker moves a waiting export only to queued (approved) or failed
    # (routines/audit.sql's export_job_worker_step).
    async with scoped_session("svc_audit_worker", event.org_id) as session:
        status = await session.scalar(
            update(audit.ExportJob)
            .where(
                audit.ExportJob.id == job_id,
                audit.ExportJob.hitl_request_id == uuid.UUID(str(p["hitl_request_id"])),
                audit.ExportJob.status == "waiting_approval",
            )
            .values(**values, updated_at=now)
            .returning(audit.ExportJob.status)
        )
    if status is not None:
        logger.info("worker-agent | audit export %s %s", job_id, status)
    if approved:
        # The approval starts the export the api did not (audit_exports.py). Run whether or not
        # this delivery moved it: a redelivery after the job was queued but before it was written
        # writes it, and a job already taken or failed is left alone by the claim.
        await audit_exports.run(event.org_id, job_id)


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
