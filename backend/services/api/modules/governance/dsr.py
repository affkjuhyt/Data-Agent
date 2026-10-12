"""Data subject requests (UC-11-06, BR-141): know, copy, delete or correct one person's data
where Data Agent keeps it.

Only a keyed hash of the identifier is kept. Finding the person's data across chunks, session
snapshots, derived tables, conversations and agent memory is a worker's job: it writes the
dsr_item rows and moves the request to review. The handler then decides each item here; deleting
is again the worker's, on the copies inside Data Agent only (E1: a source system is reported,
not touched).
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import unicodedata
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import governance
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.problems import FieldError, Problem

router = APIRouter()
ROLE = "svc_governance"
POLL_AFTER_MS = 2_000
DsrId = Annotated[uuid.UUID, Path(alias="id")]


class _View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DsrCreate(BaseModel):
    request_kind: Literal["access", "copy", "delete", "correct"]
    received_at: datetime.datetime
    due_at: datetime.datetime
    # Hashed on arrival and never stored (UC-11-06 step 1); a request without it cannot search.
    subject_identifier: str = Field(min_length=1, max_length=500)


class DsrDecision(BaseModel):
    id: uuid.UUID
    action: Literal["delete", "provide", "retain"]
    legal_basis: str | None = Field(default=None, min_length=1, max_length=1_000)


class DsrExecute(BaseModel):
    items: list[DsrDecision] = Field(min_length=1, max_length=10_000)


class DsrItemView(_View):
    id: uuid.UUID
    location_kind: str
    location_ref: str
    proposed_action: str
    legal_basis: str | None
    decision: str
    executed_at: datetime.datetime | None


class DsrRequestView(_View):
    id: uuid.UUID
    request_kind: str
    received_at: datetime.datetime
    due_at: datetime.datetime
    status: str
    handler_id: uuid.UUID
    response_ref: str | None
    closed_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    items: list[DsrItemView] = Field(default_factory=list)


class DsrAccepted(BaseModel):
    id: uuid.UUID
    status: str
    poll_after_ms: int


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


SUBJECT_KEY_ENV = "DSR_SUBJECT_KEY"


def subject_hash(identifier: str, org_id: uuid.UUID) -> str:
    """HMAC-SHA256 of the normalised identifier under the organisation's key (tab 3b
    dsr_request.subject_hash, 12/10/2026): a plain SHA-256 of a 12-digit national id is reversed
    by trying every id. The organisation's key is derived from DSR_SUBJECT_KEY, which the deploy
    reads from OpenBao; the same person written with other spacing, case or full-width digits
    gives the same hash."""
    master = os.environ.get(SUBJECT_KEY_ENV, "")
    if len(master) < 32:
        raise Problem(503, "dsr_key_unavailable", "The data subject key is not configured.")
    org_key = hmac.new(master.encode(), str(org_id).encode(), hashlib.sha256).digest()
    text = unicodedata.normalize("NFKC", identifier)
    return hmac.new(org_key, "".join(text.split()).lower().encode(), hashlib.sha256).hexdigest()


def _rule(detail: str) -> Problem:
    return Problem(422, "business_rule_violated", detail)


def _event(
    session: AsyncSession,
    principal: Principal,
    row_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    session.add(
        governance.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type="dsr_request",
            aggregate_id=row_id,
            event_type=event_type,
            payload={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in payload.items()},
        )
    )


async def _request(
    session: AsyncSession, principal: Principal, dsr_id: uuid.UUID, *, lock: bool = False
) -> governance.DsrRequest:
    query = select(governance.DsrRequest).where(
        governance.DsrRequest.id == dsr_id,
        governance.DsrRequest.org_id == principal.org_id,
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Data subject request not found.")
    return row


@router.post(
    "/v1/dsr",
    status_code=202,
    response_model=DsrAccepted,
    **endpoint("POST", "/v1/dsr", operation_id="createDsr"),
)
async def create_dsr(
    body: DsrCreate, principal: CurrentPrincipal, response: Response
) -> DsrAccepted:
    if body.received_at.tzinfo is None or body.due_at.tzinfo is None:
        raise Problem(422, "validation_failed", "received_at and due_at need a time zone.")
    if body.due_at <= body.received_at:
        raise Problem(
            422,
            "validation_failed",
            "due_at is after received_at.",
            errors=[FieldError(field="due_at", code="invalid", message="after received_at")],
        )
    digest = subject_hash(body.subject_identifier, principal.org_id)
    stamp = _now()
    async with transaction(ROLE, principal) as session:
        row = governance.DsrRequest(
            id=uuid7(),
            org_id=principal.org_id,
            subject_hash=digest,
            request_kind=body.request_kind,
            received_at=body.received_at,
            due_at=body.due_at,
            status="searching",
            handler_id=principal.user_id,
            created_by=principal.user_id,
            updated_by=principal.user_id,
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(row)
        await session.flush()
        # The search worker needs the hash to match; the identifier itself never leaves here.
        _event(
            session,
            principal,
            row.id,
            "dsr.search_requested",
            dsr_request_id=row.id,
            subject_hash=digest,
            request_kind=row.request_kind,
            due_at=row.due_at.isoformat(),
            status=row.status,
        )
    response.headers["Location"] = f"/v1/dsr/{row.id}"
    return DsrAccepted(id=row.id, status=row.status, poll_after_ms=POLL_AFTER_MS)


@router.get(
    "/v1/dsr/{id}",
    response_model=DsrRequestView,
    **endpoint("GET", "/v1/dsr/{id}", operation_id="getDsr"),
)
async def get_dsr(dsr_id: DsrId, principal: CurrentPrincipal) -> DsrRequestView:
    async with transaction(ROLE, principal) as session:
        row = await _request(session, principal, dsr_id)
        items = (
            await session.scalars(
                select(governance.DsrItem)
                .where(governance.DsrItem.dsr_request_id == row.id)
                .order_by(governance.DsrItem.created_at, governance.DsrItem.id)
            )
        ).all()
    view = DsrRequestView.model_validate(row)
    view.items = [DsrItemView.model_validate(i) for i in items]
    return view


@router.post(
    "/v1/dsr/{id}:execute",
    status_code=202,
    response_model=DsrAccepted,
    **endpoint("POST", "/v1/dsr/{id}:execute", operation_id="executeDsr"),
)
async def execute_dsr(
    dsr_id: DsrId, body: DsrExecute, principal: CurrentPrincipal, response: Response
) -> DsrAccepted:
    stamp = _now()
    async with transaction(ROLE, principal) as session:
        row = await _request(session, principal, dsr_id, lock=True)
        if row.status != "review":
            raise _rule(f"The request is {row.status}; items are decided in review.")
        items = {
            i.id: i
            for i in (
                await session.scalars(
                    select(governance.DsrItem)
                    .where(governance.DsrItem.dsr_request_id == row.id)
                    .with_for_update()
                )
            ).all()
        }
        decided = {d.id: d for d in body.items}
        unknown = set(decided) - set(items)
        if unknown:
            raise _rule(f"Not items of this request: {sorted(map(str, unknown))}.")
        pending = {i for i, item in items.items() if item.decision == "pending"}
        if pending - set(decided):
            raise _rule("Every pending item needs a decision before the request is executed.")
        for item_id, decision in decided.items():
            item = items[item_id]
            if decision.action == "retain" and not decision.legal_basis:
                raise _rule("Retaining an item needs its legal basis (UC-11-06 3a).")
            if decision.action == "delete" and item.location_kind == "source_system":
                # E1: the original lives in the source; the bank deletes it there.
                raise _rule("Data in a source system is reported, not deleted by Data Agent.")
            if decision.action == "delete" and row.request_kind not in ("delete", "correct"):
                raise _rule(f"A {row.request_kind} request does not delete data.")
            item.proposed_action = decision.action
            if decision.action == "retain":
                item.legal_basis = decision.legal_basis
                item.decision = "retained"
            item.updated_by, item.updated_at = principal.user_id, stamp
        row.status = "executing"
        row.updated_by, row.updated_at = principal.user_id, stamp
        _event(
            session,
            principal,
            row.id,
            "dsr.execute_requested",
            dsr_request_id=row.id,
            status=row.status,
            delete_item_ids=[str(i) for i, d in decided.items() if d.action == "delete"],
            provide_item_ids=[str(i) for i, d in decided.items() if d.action == "provide"],
        )
    response.headers["Location"] = f"/v1/dsr/{row.id}"
    return DsrAccepted(id=row.id, status=row.status, poll_after_ms=POLL_AFTER_MS)
