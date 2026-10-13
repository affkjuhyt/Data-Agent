"""A2 group Kiểm toán (UC-11-03): the audit log and security events of the dataagent_audit database.

audit.audit_log is append-only and hash-chained per organisation (BR-47, D5.3): the trigger
audit.chain_hash numbers and links every row, audit.verify_chain finds the first broken link, and
svc_audit may only SELECT and INSERT it. RLS keeps a caller to their organisation's rows.

Reading the log is itself logged (A3: "việc tra cứu cũng được ghi nhật ký"), in the same
transaction, so a search that fails leaves no trace and one that succeeds always does.

POST /v1/audit:export records an audit.export_job beside the rows it exports; an export
worker writes and signs the file. Over 100 000 rows a second person must approve it (BR-138), so
the job waits in waiting_approval, and the CHECKs of audit.export_job keep it from running
before approved_by is set by someone other than the requester.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import audit
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Order,
    Page,
    PageRequest,
    encode_page_token,
    invalid_page_token,
    keyset,
    next_page,
    order_by,
    page_request,
)
from services.api.kernel.problems import Problem
from services.api.modules.audit.schemas import (
    AuditFilters,
    AuditLogView,
    ExportAccepted,
    ExportIn,
    ExportJobView,
    SecurityEventView,
    VerifyIn,
    VerifyResult,
)
from services.api.modules.inbox.public import EVENT_TYPE as HITL_REQUESTED
from services.api.modules.inbox.public import hitl_requested

router = APIRouter()
ROLE = "svc_audit"
SERVICE = "api"
# BR-138: above this many rows an export needs a second person's approval.
APPROVAL_ROWS = 100_000


def audit_filters(
    actor_id: uuid.UUID | None = None,
    on_behalf_of: uuid.UUID | None = None,
    object_kind: Annotated[str | None, Query(max_length=64)] = None,
    object_id: uuid.UUID | None = None,
    action: Annotated[str | None, Query(max_length=128)] = None,
    outcome: Annotated[str | None, Query(pattern="^(ok|denied|error)$")] = None,
    from_: Annotated[datetime.datetime | None, Query(alias="from")] = None,
    to: datetime.datetime | None = None,
    agent_run_id: uuid.UUID | None = None,
) -> AuditFilters:
    return AuditFilters(
        actor_id=actor_id,
        on_behalf_of=on_behalf_of,
        object_kind=object_kind,
        object_id=object_id,
        action=action,
        outcome=outcome,  # type: ignore[arg-type]
        **{"from": from_},
        to=to,
        agent_run_id=agent_run_id,
    )


def _where(query: Select[Any], f: AuditFilters) -> Select[Any]:
    log = audit.AuditLog
    for column, value in (
        (log.actor_id, f.actor_id),
        (log.on_behalf_of, f.on_behalf_of),
        (log.object_kind, f.object_kind),
        (log.object_id, f.object_id),
        (log.action, f.action),
        (log.outcome, f.outcome),
        (log.agent_run_id, f.agent_run_id),
    ):
        if value is not None:
            query = query.where(column == value)
    if f.from_ is not None:
        query = query.where(log.occurred_at >= f.from_)
    if f.to is not None:
        query = query.where(log.occurred_at < f.to)
    return query


def _record(
    session: AsyncSession, principal: Principal, action: str, after: dict[str, Any]
) -> None:
    """Append one row for what the caller just did. seq, prev_hash and hash are the
    trigger's (audit.chain_hash replaces whatever is sent)."""
    session.add(
        audit.AuditLog(
            org_id=principal.org_id,
            seq=0,
            prev_hash="",
            hash="",
            actor_kind="client" if principal.client_id else "user",
            actor_id=principal.user_id,
            data_space_id=principal.data_space_id,
            service=SERVICE,
            action=action,
            outcome="ok",
            after=after,
        )
    )


@router.get(
    "/v1/audit",
    response_model=Page[AuditLogView],
    **endpoint("GET", "/v1/audit", operation_id="listAuditLog"),
)
async def list_audit_log(
    principal: CurrentPrincipal,
    filters: Annotated[AuditFilters, Depends(audit_filters)],
    page: Annotated[PageRequest, Depends(page_request)],
) -> Page[AuditLogView]:
    log = audit.AuditLog
    query = _where(select(log), filters).order_by(log.seq.desc()).limit(page.page_size + 1)
    if page.cursor:
        try:
            query = query.where(log.seq < int(page.cursor["seq"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise invalid_page_token() from exc
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
        _record(
            session,
            principal,
            "audit.searched",
            {"filters": filters.model_dump(mode="json", by_alias=True, exclude_none=True)},
        )
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    return Page[AuditLogView](
        items=[AuditLogView.model_validate(r) for r in rows],
        next_page_token=encode_page_token({"seq": rows[-1].seq} if more else None),
    )


@router.post(
    "/v1/audit:verify",
    response_model=VerifyResult,
    **endpoint("POST", "/v1/audit:verify", operation_id="verifyAuditChain"),
)
async def verify_audit_chain(principal: CurrentPrincipal, body: VerifyIn) -> VerifyResult:
    from_seq = body.from_seq or 1
    async with transaction(ROLE, principal) as session:
        checked = int(
            await session.scalar(
                select(func.count())
                .select_from(audit.AuditLog)
                .where(audit.AuditLog.seq >= from_seq)
            )
            or 0
        )
        broken = await session.scalar(
            text("SELECT audit.verify_chain(:org, :from_seq)"),
            {"org": principal.org_id, "from_seq": from_seq},
        )
        if broken is not None:
            # A3: a broken chain is a high-severity security event, recorded where the
            # compliance officer looks (GET /v1/security-events).
            session.add(
                audit.SecurityEvent(
                    org_id=principal.org_id,
                    kind="hash_chain_broken",
                    severity="high",
                    actor_id=principal.user_id,
                    source_ref="audit_log",
                    detail={"broken_at_seq": int(broken), "from_seq": from_seq},
                )
            )
        result = VerifyResult(
            ok=broken is None,
            broken_at_seq=int(broken) if broken is not None else None,
            checked_rows=checked,
        )
        _record(session, principal, "audit.verified", result.model_dump())
    return result


@router.post(
    "/v1/audit:export",
    status_code=202,
    response_model=ExportAccepted,
    **endpoint("POST", "/v1/audit:export", operation_id="exportAuditLog"),
)
async def export_audit_log(
    principal: CurrentPrincipal, body: ExportIn, response: Response
) -> ExportAccepted:
    filters = body.filters.model_dump(mode="json", by_alias=True, exclude_none=True)
    async with transaction(ROLE, principal) as session:
        # Counted now, so the approval rule is decided on what was asked for, not on what the
        # log grows to by the time the worker runs (it exports up to this seq, see `filters`).
        row_count, last_seq = (
            await session.execute(
                _where(
                    select(func.count(), func.max(audit.AuditLog.seq)).select_from(audit.AuditLog),
                    body.filters,
                )
            )
        ).one()
        # BR-138 counts what one person takes out, not one request: exports split under the
        # threshold add up over a day, so cutting a large export into pieces still needs approval.
        # One requester's exports are judged one at a time, or parallel requests would each see
        # the day's total before the others commit.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
            {"k": f"audit.export:{principal.org_id}:{principal.user_id}"},
        )
        taken_today = await session.scalar(
            select(func.coalesce(func.sum(audit.ExportJob.row_count), 0)).where(
                audit.ExportJob.requested_by == principal.user_id,
                audit.ExportJob.created_at >= func.now() - text("interval '24 hours'"),
                audit.ExportJob.status.not_in(("failed", "expired")),
            )
        )
        status: Literal["queued", "waiting_approval"] = (
            "waiting_approval" if int(taken_today or 0) + row_count > APPROVAL_ROWS else "queued"
        )
        job = audit.ExportJob(
            id=uuid7(),
            org_id=principal.org_id,
            filters={**filters, "to_seq": int(last_seq or 0)},
            format=body.format,
            row_count=int(row_count),
            status=status,
            requested_by=principal.user_id,
        )
        session.add(job)
        if status == "waiting_approval":
            # BR-138: a second person with audit_read, never the requester, approves it in Cần bạn.
            # Org-wide: an export of the audit log belongs to no Data Space.
            job.hitl_request_id = uuid7()
            session.add(
                audit.Outbox(
                    id=uuid7(),
                    org_id=principal.org_id,
                    aggregate_type="export_job",
                    aggregate_id=job.id,
                    event_type=HITL_REQUESTED,
                    payload=hitl_requested(
                        hitl_request_id=job.hitl_request_id,
                        data_space_id=None,
                        kind="export",
                        risk="high",
                        title=f"Xuất {int(row_count):,} dòng nhật ký kiểm toán".replace(",", "."),
                        source_service="audit",
                        source_ref=job.id,
                        created_by=principal.user_id,
                        context={
                            "filters": filters,
                            "row_count": int(row_count),
                            "format": body.format,
                        },
                        assignee_capability="audit_read",
                        excluded_approver_ids=[principal.user_id],
                    ),
                )
            )
        _record(
            session,
            principal,
            "audit.export_requested",
            {"export_job_id": str(job.id), "row_count": job.row_count, "filters": filters},
        )
    response.headers["Location"] = f"/v1/audit/exports/{job.id}"
    return ExportAccepted(id=job.id, status=status, row_count=int(row_count))


@router.get(
    "/v1/audit/exports/{id}",
    response_model=ExportJobView,
    **endpoint("GET", "/v1/audit/exports/{id}", operation_id="getAuditExport"),
)
async def get_audit_export(
    principal: CurrentPrincipal, job_id: Annotated[uuid.UUID, Path(alias="id")]
) -> ExportJobView:
    async with transaction(ROLE, principal) as session:
        job = await session.get(audit.ExportJob, job_id)
        if job is None:
            raise Problem(404, "not_found", "No such export.")
        return ExportJobView.model_validate(job)


@router.get(
    "/v1/security-events",
    response_model=Page[SecurityEventView],
    **endpoint("GET", "/v1/security-events", operation_id="listSecurityEvents"),
)
async def list_security_events(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[Order, Depends(order_by("occurred_at", default="occurred_at desc"))],
    kind: Annotated[str | None, Query(max_length=64)] = None,
    severity: Annotated[str | None, Query(pattern="^(low|medium|high|critical)$")] = None,
    status: Annotated[str | None, Query(pattern="^(open|investigating|closed)$")] = None,
) -> Page[SecurityEventView]:
    ev = audit.SecurityEvent
    query = select(ev)
    for column, value in ((ev.kind, kind), (ev.severity, severity), (ev.status, status)):
        if value is not None:
            query = query.where(column == value)
    query = keyset(
        query, columns={"occurred_at": ev.occurred_at}, tiebreak=ev.id, order=order, page=page
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page[SecurityEventView](
        items=[SecurityEventView.model_validate(r) for r in rows], next_page_token=token
    )
