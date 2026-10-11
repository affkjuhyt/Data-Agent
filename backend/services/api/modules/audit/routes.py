"""A2 group Kiểm toán (UC-11-03): the audit log and security events of the dataagent_audit database.

audit.audit_log is append-only and hash-chained per organisation (BR-47, D5.3): the trigger
audit.chain_hash numbers and links every row, audit.verify_chain finds the first broken link, and
svc_audit may only SELECT and INSERT it. RLS keeps a caller to their organisation's rows.

Reading the log is itself logged (A3: "việc tra cứu cũng được ghi nhật ký"), in the same
transaction, so a search that fails leaves no trace and one that succeeds always does.

POST /v1/audit:export is not served: A3 points it at an export_job, and the only one
(qa.export_job) must belong to an answer or an artifact; the design has no table for an audit
export yet (raised with the DB layer). The group stays out of COMPLETE_GROUPS until it has one.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, func, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import audit
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Page,
    PageRequest,
    encode_page_token,
    invalid_page_token,
    page_request,
)
from services.api.modules.audit.schemas import (
    AuditFilters,
    AuditLogView,
    SecurityEventView,
    VerifyIn,
    VerifyResult,
)

router = APIRouter()
ROLE = "svc_audit"
SERVICE = "api"


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


@router.get(
    "/v1/security-events",
    response_model=Page[SecurityEventView],
    **endpoint("GET", "/v1/security-events", operation_id="listSecurityEvents"),
)
async def list_security_events(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    kind: Annotated[str | None, Query(max_length=64)] = None,
    severity: Annotated[str | None, Query(pattern="^(low|medium|high|critical)$")] = None,
    status: Annotated[str | None, Query(pattern="^(open|investigating|closed)$")] = None,
) -> Page[SecurityEventView]:
    ev = audit.SecurityEvent
    query = select(ev).order_by(ev.occurred_at.desc(), ev.id.desc()).limit(page.page_size + 1)
    for column, value in ((ev.kind, kind), (ev.severity, severity), (ev.status, status)):
        if value is not None:
            query = query.where(column == value)
    if page.cursor:
        try:
            after = (
                datetime.datetime.fromisoformat(str(page.cursor["at"])),
                uuid.UUID(str(page.cursor["id"])),
            )
        except (KeyError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(ev.occurred_at, ev.id) < after)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    last = rows[-1] if more else None
    return Page[SecurityEventView](
        items=[SecurityEventView.model_validate(r) for r in rows],
        next_page_token=encode_page_token(
            {"at": last.occurred_at.isoformat(), "id": str(last.id)} if last else None
        ),
    )
