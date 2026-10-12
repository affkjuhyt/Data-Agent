"""A2 group "Lớp riêng lãnh đạo": the Exec vault service on schema exec (UC-16-01 to UC-16-06).

Everything here acts for the vault owner (app.user_id), and the schema is the most guarded one: every
table is owner-only under FORCE RLS, ciphertext columns are column-granted, and the lifecycle rules
(BR-188 to BR-195) are triggers in backend/schema/csdl/routines/exec.sql. The routes therefore check
only what makes a better answer than the trigger's (409 invalid_state for a state, 409 conflict for a
rotated key) and let the database refuse the rest.

Content is ciphertext made on the owner's device (BR-189): the api stores and returns it, never opens
it, never logs it, and puts only ids and statuses in exec.outbox. Private-question text never touches
the database at all (BR-193, ports.PrivateQuestions).

app.exec_admin = 'on' is set only for the AC05/AC06 handover processing of UC-16-06: an
administrator's POST /v1/exec/handovers, GET /v1/exec/handovers/{id} for admin/compliance, and
:approve. It opens exec.handover and exec.vault_admin_overview(), never a ciphertext column.
"""

from __future__ import annotations

import datetime
import math
import re
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path, Query, Request, Response
from sqlalchemy import Row, Select, func, insert, select, text, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import exec as vaultdb
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
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
from services.api.kernel.sse import Event, event_stream
from services.api.kernel.sse import last_event_id as get_last_event_id
from services.api.modules.exec.ports import (
    ModelUnavailable,
    PrivateQuestion,
    private_questions,
    transit,
)
from services.api.modules.exec.schemas import (
    Accepted,
    EvidenceView,
    HandoverAccepted,
    HandoverApprove,
    HandoverCreate,
    HandoverItemsCreate,
    HandoverItemView,
    HandoverView,
    KeyAccessView,
    KeyAction,
    KeyWrapCreate,
    KeyWrapView,
    MemoryItemCreate,
    MemoryItemView,
    MemoryKind,
    PrivateQuestionCreate,
    PublicationCreate,
    PublicationView,
    PublicationWithdraw,
    ScoreView,
    ScoreWithThesis,
    ThesisCreate,
    ThesisDetail,
    ThesisStatus,
    ThesisView,
    TransitKeyView,
    VaultCreate,
    VaultDetail,
)

router = APIRouter()
ROLE = "svc_exec"

IdPath = Annotated[uuid.UUID, Path(alias="id")]
FromQuery = Annotated[datetime.datetime | None, Query(alias="from")]
ToQuery = Annotated[datetime.datetime | None, Query(alias="to")]
Paging = Annotated[PageRequest, Depends(page_request)]

# BR-189: a vault is opened offline by a hardware key or the paper recovery code.
OFFLINE_WRAPS = ("hardware_key", "recovery_code")
OPEN_PUBLICATION = ("pending_review", "published")
PUBLISHABLE = ("open", "scored")
REFRESH_EVENT = "exec.thesis.evidence_refresh_requested"
# BR-192: at most three condition decrypts per thesis in 24 hours; a refresh asked within the hour
# joins the pending batch instead of costing another one.
DECRYPTS_PER_DAY = 3
REFRESH_WINDOW = datetime.timedelta(hours=1)
# A1.4 / tab 4: private context is at most 8 items and 4,000 tokens. The api has no tokenizer, so
# it counts one token per 4 characters, the usual estimate for the local model's vocabulary.
CONTEXT_TOKENS = 4_000
CHARS_PER_TOKEN = 4
DIGIT = re.compile(r"[0-9]")
# The optional bodies of :withdraw and :approve; a call without a body gets these.
_NO_REASON = PublicationWithdraw()
_NO_NOTE = HandoverApprove()


# ─── helpers ───


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat()
    return value


def _event(
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> vaultdb.Outbox:
    """One exec.outbox row: ids and states only, never content (BR-188, BR-193)."""
    return vaultdb.Outbox(
        id=uuid7(),
        org_id=principal.org_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload={k: _jsonable(v) for k, v in payload.items()},
    )


async def _log_key(
    session: AsyncSession,
    principal: Principal,
    *,
    vault_id: uuid.UUID,
    owner_id: uuid.UUID,
    key_kind: str,
    action: str,
    purpose: str,
    actor_kind: str,
    actor_ref: str,
) -> uuid.UUID:
    """A key_access_log row (BR-192). A plain INSERT without RETURNING: an administrator writes the
    leaving owner's log, which the owner-only SELECT policy would refuse to return. The trigger
    stamps `at` and checks that the actor kind is the caller's own."""
    correlation = uuid7()
    await session.execute(
        insert(vaultdb.KeyAccessLog).values(
            id=uuid7(),
            org_id=principal.org_id,
            vault_id=vault_id,
            owner_id=owner_id,
            key_kind=key_kind,
            action=action,
            purpose=purpose,
            actor_kind=actor_kind,
            actor_ref=actor_ref,
            correlation_id=correlation,
        )
    )
    return correlation


def _parse_time(value: Any) -> datetime.datetime:
    return datetime.datetime.fromisoformat(str(value))


async def _paged(
    session: AsyncSession,
    query: Select[Any],
    page: PageRequest,
    key: InstrumentedAttribute[Any],
    ident: InstrumentedAttribute[Any],
) -> tuple[Sequence[Row[Any]], str]:
    """A1.4 cursor paging, newest first, on (key, id) of the query's first entity. Only for a
    query that selects more than an entity; an entity list pages through keyset()."""
    if page.cursor:
        try:
            after = (_parse_time(page.cursor["k"]), uuid.UUID(str(page.cursor["id"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(key, ident) < after)
    order = (key.desc(), ident.desc())
    rows = (await session.execute(query.order_by(*order).limit(page.page_size + 1))).all()
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    token = None
    if more:
        last = rows[-1][0]
        token = {"k": _jsonable(getattr(last, key.key)), "id": str(getattr(last, ident.key))}
    return rows, encode_page_token(token)


async def _vault(
    session: AsyncSession, principal: Principal, *, lock: bool = False
) -> vaultdb.Vault | None:
    query = select(vaultdb.Vault).where(
        vaultdb.Vault.org_id == principal.org_id, vaultdb.Vault.owner_id == principal.user_id
    )
    if lock:
        query = query.with_for_update()
    vault: vaultdb.Vault | None = await session.scalar(query)
    return vault


async def _require_vault(session: AsyncSession, principal: Principal) -> vaultdb.Vault:
    vault = await _vault(session, principal)
    if vault is None:
        raise Problem(404, "not_found", "No vault yet: create one first.")
    return vault


async def _writable_vault(session: AsyncSession, principal: Principal) -> vaultdb.Vault:
    """The vault new content goes into: an active one (a sealed vault is read-only, BR-195)."""
    vault = await _vault(session, principal)
    if vault is None:
        raise Problem(409, "invalid_state", "No vault yet: create one before writing to it.")
    if vault.status != "active":
        raise Problem(
            409, "invalid_state", f"The vault is {vault.status} and takes no new content."
        )
    return vault


async def _thesis(
    session: AsyncSession, thesis_id: uuid.UUID, principal: Principal, *, lock: bool = False
) -> vaultdb.Thesis:
    query = select(vaultdb.Thesis).where(
        vaultdb.Thesis.id == thesis_id,
        vaultdb.Thesis.org_id == principal.org_id,
        vaultdb.Thesis.owner_id == principal.user_id,
    )
    if lock:
        query = query.with_for_update()
    thesis: vaultdb.Thesis | None = await session.scalar(query)
    if thesis is None:
        raise Problem(404, "not_found", "Thesis not found.")
    return thesis


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


# ─── vault and key wraps (UC-16-01) ───


async def _live_wraps(session: AsyncSession, vault_id: uuid.UUID) -> list[KeyWrapView]:
    wraps = await session.scalars(
        select(vaultdb.VaultKeyWrap)
        .where(vaultdb.VaultKeyWrap.vault_id == vault_id, vaultdb.VaultKeyWrap.revoked_at.is_(None))
        .order_by(vaultdb.VaultKeyWrap.created_at, vaultdb.VaultKeyWrap.id)
    )
    return [KeyWrapView.model_validate(w) for w in wraps]


@router.get(
    "/v1/exec/vault",
    response_model=VaultDetail,
    **endpoint("GET", "/v1/exec/vault", operation_id="getExecVault"),
)
async def get_vault(principal: CurrentPrincipal) -> VaultDetail:
    async with transaction(ROLE, principal) as session:
        vault = await _require_vault(session, principal)
        wraps = await _live_wraps(session, vault.id)
    return VaultDetail.model_validate(vault).model_copy(update={"key_wraps": wraps})


def _wrap_row(
    principal: Principal, vault_id: uuid.UUID, wrap: KeyWrapCreate
) -> vaultdb.VaultKeyWrap:
    return vaultdb.VaultKeyWrap(
        id=uuid7(),
        org_id=principal.org_id,
        vault_id=vault_id,
        owner_id=principal.user_id,
        kind=wrap.kind,
        credential_id=wrap.credential_id,
        label=wrap.label,
        kdf=wrap.kdf,
        kdf_salt=wrap.kdf_salt,
        wrapped_vmk=wrap.wrapped_vmk,
        created_by=principal.user_id,
    )


@router.post(
    "/v1/exec/vault",
    status_code=201,
    response_model=VaultDetail,
    **endpoint("POST", "/v1/exec/vault", operation_id="createExecVault"),
)
async def create_vault(
    body: VaultCreate, principal: CurrentPrincipal, request: Request
) -> VaultDetail:
    if len(body.key_wraps) < 2 or not any(w.kind in OFFLINE_WRAPS for w in body.key_wraps):
        # BR-189: losing every key loses the content, so the vault starts with a spare offline one.
        raise Problem(
            422,
            "business_rule_violated",
            "A vault needs at least 2 key wraps, one of them offline (BR-189).",
        )
    keys = transit(request)
    async with transaction(ROLE, principal) as session:
        if await _vault(session, principal) is not None:
            raise Problem(409, "conflict", "This user already has a vault.")
    # One RSA-4096 Transit key per vault (BR-189). Named by org and owner, so a retry after a failed
    # insert finds the same key instead of leaving an orphan.
    name = f"exec-cond-{principal.org_id}-{principal.user_id}"
    key = await keys.create(name)
    async with transaction(ROLE, principal) as session:
        vault = vaultdb.Vault(
            id=uuid7(),
            org_id=principal.org_id,
            owner_id=principal.user_id,
            box_public_key=body.box_public_key,
            box_private_key_ct=body.box_private_key_ct,
            transit_key_name=name,
            transit_key_version=key.version,
            exit_policy=body.exit_policy,
            contract_ref=body.contract_ref,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(vault)
        await session.flush()
        session.add_all(_wrap_row(principal, vault.id, w) for w in body.key_wraps)
        session.add(
            _event(
                principal,
                "vault",
                vault.id,
                "exec.vault.created",
                vault_id=vault.id,
                owner_id=principal.user_id,
                status=vault.status,
                exit_policy=body.exit_policy,
            )
        )
        await session.flush()
        await session.refresh(vault)
        wraps = await _live_wraps(session, vault.id)
    return VaultDetail.model_validate(vault).model_copy(
        update={"key_wraps": wraps, "transit_public_key": key.public_key_pem}
    )


@router.post(
    "/v1/exec/vault/key-wraps",
    status_code=201,
    response_model=KeyWrapView,
    **endpoint("POST", "/v1/exec/vault/key-wraps", operation_id="addExecKeyWrap"),
)
async def add_key_wrap(body: KeyWrapCreate, principal: CurrentPrincipal) -> KeyWrapView:
    async with transaction(ROLE, principal) as session:
        vault = await _require_vault(session, principal)
        if vault.status != "active":
            raise Problem(409, "invalid_state", f"The vault is {vault.status}.")
        wrap = _wrap_row(principal, vault.id, body)
        session.add(wrap)
        session.add(
            _event(
                principal,
                "vault_key_wrap",
                wrap.id,
                "exec.vault_key_wrap.added",
                vault_id=vault.id,
                key_wrap_id=wrap.id,
                kind=body.kind,
            )
        )
        await session.flush()
        await session.refresh(wrap)
        return KeyWrapView.model_validate(wrap)


@router.delete(
    "/v1/exec/vault/key-wraps/{id}",
    status_code=204,
    response_class=Response,
    **endpoint("DELETE", "/v1/exec/vault/key-wraps/{id}", operation_id="revokeExecKeyWrap"),
)
async def revoke_key_wrap(wrap_id: IdPath, principal: CurrentPrincipal) -> Response:
    async with transaction(ROLE, principal) as session:
        wrap = await session.scalar(
            select(vaultdb.VaultKeyWrap)
            .where(
                vaultdb.VaultKeyWrap.id == wrap_id,
                vaultdb.VaultKeyWrap.org_id == principal.org_id,
                vaultdb.VaultKeyWrap.owner_id == principal.user_id,
                vaultdb.VaultKeyWrap.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if wrap is None:
            raise Problem(404, "not_found", "Key wrap not found.")
        # The trigger refuses a revocation that leaves fewer than 2 live wraps or no offline one
        # (422 business_rule_violated, BR-189).
        wrap.revoked_at = func.now()
        session.add(
            _event(
                principal,
                "vault_key_wrap",
                wrap.id,
                "exec.vault_key_wrap.revoked",
                vault_id=wrap.vault_id,
                key_wrap_id=wrap.id,
            )
        )
        await session.flush()
    return Response(status_code=204)


@router.get(
    "/v1/exec/transit-key",
    response_model=TransitKeyView,
    **endpoint("GET", "/v1/exec/transit-key", operation_id="getExecTransitKey"),
)
async def get_transit_key(principal: CurrentPrincipal, request: Request) -> TransitKeyView:
    keys = transit(request)
    async with transaction(ROLE, principal) as session:
        vault = await _require_vault(session, principal)
        if vault.status == "shredded":
            raise Problem(404, "not_found", "The vault's keys were destroyed.")
        name, version = vault.transit_key_name, vault.transit_key_version
    key = await keys.public_key(name, version)
    return TransitKeyView(public_key_pem=key.public_key_pem, key_version=key.version)


# ─── theses (UC-16-01) ───


@router.get(
    "/v1/exec/theses",
    response_model=Page[ThesisView],
    **endpoint("GET", "/v1/exec/theses", operation_id="listExecTheses"),
)
async def list_theses(
    principal: CurrentPrincipal,
    page: Paging,
    order: Annotated[
        Order,
        Depends(
            order_by("committed_at", "due_at", "created_at", "status", default="committed_at desc")
        ),
    ],
    status: ThesisStatus | None = None,
    due_from: datetime.datetime | None = None,
    due_to: datetime.datetime | None = None,
) -> Page[ThesisView]:
    t = vaultdb.Thesis
    query = select(t).where(t.org_id == principal.org_id, t.owner_id == principal.user_id)
    if status:
        query = query.where(t.status == status)
    if due_from:
        query = query.where(t.due_at >= due_from)
    if due_to:
        query = query.where(t.due_at < due_to)
    query = keyset(
        query,
        columns={
            "committed_at": t.committed_at,
            "due_at": t.due_at,
            "created_at": t.created_at,
            "status": t.status,
        },
        tiebreak=t.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[ThesisView.model_validate(r) for r in rows], next_page_token=token)


@router.post(
    "/v1/exec/theses",
    status_code=201,
    response_model=ThesisView,
    **endpoint("POST", "/v1/exec/theses", operation_id="createExecThesis"),
)
async def create_thesis(body: ThesisCreate, principal: CurrentPrincipal) -> ThesisView:
    async with transaction(ROLE, principal) as session:
        vault = await _writable_vault(session, principal)
        if body.cond_key_version != vault.transit_key_version:
            # UC-16-01 E3: the key was rotated; the device fetches the new public key and retries.
            raise Problem(
                409,
                "conflict",
                f"cond_key_version {body.cond_key_version} is not the vault's current Transit key "
                f"version ({vault.transit_key_version}).",
            )
        if body.supersedes_id is not None:
            # Loaded under the owner policy: a foreign key does not see RLS, so another leader's
            # thesis id would otherwise be accepted as the one this replaces (BR-188).
            await _thesis(session, body.supersedes_id, principal)
        thesis_id = uuid7()
        # The triggers refuse a vault without 2 live wraps (one offline), a due_at off the whole
        # hour, and the CHECK one under a day away: 422 business_rule_violated (BR-188, BR-189).
        thesis = vaultdb.Thesis(
            id=thesis_id,
            org_id=principal.org_id,
            vault_id=vault.id,
            owner_id=principal.user_id,
            content_ct=body.content_ct,
            wrapped_dek=body.wrapped_dek,
            content_version=body.content_version,
            cond_ct=body.cond_ct,
            cond_key_version=body.cond_key_version,
            due_at=body.due_at,
            supersedes_id=body.supersedes_id,
            # Deterministic, so the worker starting ExecThesisWorkflow from the event is idempotent.
            workflow_id=f"exec-thesis-{thesis_id}",
            created_by=principal.user_id,
        )
        session.add(thesis)
        await session.flush()
        # BR-188: the event carries no content, only what the workflow schedules on.
        session.add(
            _event(
                principal,
                "thesis",
                thesis.id,
                "exec.thesis.committed",
                thesis_id=thesis.id,
                vault_id=vault.id,
                owner_id=principal.user_id,
                due_at=body.due_at,
                workflow_id=thesis.workflow_id,
                supersedes_id=body.supersedes_id,
                status="open",
            )
        )
        await session.flush()
        await session.refresh(thesis)
        return ThesisView.model_validate(thesis)


@router.get(
    "/v1/exec/theses/{id}",
    response_model=ThesisDetail,
    **endpoint("GET", "/v1/exec/theses/{id}", operation_id="getExecThesis"),
)
async def get_thesis(thesis_id: IdPath, principal: CurrentPrincipal) -> ThesisDetail:
    async with transaction(ROLE, principal) as session:
        thesis = await _thesis(session, thesis_id, principal)
        batches = await session.scalar(
            select(func.coalesce(func.max(vaultdb.ThesisEvidence.batch_no), 0)).where(
                vaultdb.ThesisEvidence.thesis_id == thesis.id
            )
        )
        publication = await session.scalar(
            select(vaultdb.Publication.id)
            .where(
                vaultdb.Publication.thesis_id == thesis.id,
                vaultdb.Publication.status.in_(OPEN_PUBLICATION),
            )
            .order_by(vaultdb.Publication.created_at.desc())
            .limit(1)
        )
    return ThesisDetail.model_validate(thesis).model_copy(
        update={"evidence_batches": int(batches or 0), "publication_id": publication}
    )


@router.post(
    "/v1/exec/theses/{id}:withdraw",
    response_model=ThesisView,
    **endpoint("POST", "/v1/exec/theses/{id}:withdraw", operation_id="withdrawExecThesis"),
)
async def withdraw_thesis(thesis_id: IdPath, principal: CurrentPrincipal) -> ThesisView:
    async with transaction(ROLE, principal) as session:
        thesis = await _thesis(session, thesis_id, principal, lock=True)
        if thesis.status != "open":
            raise Problem(409, "invalid_state", f"The thesis is {thesis.status}.")
        if thesis.due_at <= _now():
            raise Problem(409, "invalid_state", "The thesis is due; it can no longer be withdrawn.")
        thesis.status = "withdrawn"
        session.add(
            _event(
                principal,
                "thesis",
                thesis.id,
                "exec.thesis.withdrawn",
                thesis_id=thesis.id,
                workflow_id=thesis.workflow_id,
                status="withdrawn",
            )
        )
        await session.flush()
        await session.refresh(thesis)
        return ThesisView.model_validate(thesis)


# ─── evidence and scores (UC-16-03, UC-16-04) ───


@router.get(
    "/v1/exec/theses/{id}/evidence",
    response_model=Page[EvidenceView],
    **endpoint("GET", "/v1/exec/theses/{id}/evidence", operation_id="listExecThesisEvidence"),
)
async def list_evidence(
    thesis_id: IdPath,
    principal: CurrentPrincipal,
    page: Paging,
    order: Annotated[
        Order,
        Depends(order_by("batch_no", "recorded_at", "created_at", default="batch_no desc")),
    ],
    batch_no: Annotated[int | None, Query(ge=1)] = None,
) -> Page[EvidenceView]:
    e = vaultdb.ThesisEvidence
    async with transaction(ROLE, principal) as session:
        thesis = await _thesis(session, thesis_id, principal)
        query = select(e).where(e.thesis_id == thesis.id)
        if batch_no is not None:
            query = query.where(e.batch_no == batch_no)
        else:
            # The screen compares the latest batch with the one before (SCR-73).
            latest = await session.scalar(
                select(func.coalesce(func.max(e.batch_no), 0)).where(e.thesis_id == thesis.id)
            )
            query = query.where(e.batch_no >= int(latest or 0) - 1)
        query = keyset(
            query,
            columns={
                "batch_no": e.batch_no,
                "recorded_at": e.recorded_at,
                "created_at": e.created_at,
            },
            tiebreak=e.id,
            order=order,
            page=page,
        )
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[EvidenceView.model_validate(r) for r in rows], next_page_token=token)


@router.post(
    "/v1/exec/theses/{id}/evidence:refresh",
    status_code=202,
    response_model=Accepted,
    **endpoint(
        "POST", "/v1/exec/theses/{id}/evidence:refresh", operation_id="refreshExecThesisEvidence"
    ),
)
async def refresh_evidence(
    thesis_id: IdPath, principal: CurrentPrincipal, response: Response
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        thesis = await _thesis(session, thesis_id, principal, lock=True)
        if thesis.status != "open":
            raise Problem(409, "invalid_state", f"The thesis is {thesis.status}.")
        vault = await _require_vault(session, principal)
        if vault.status != "active":
            # A sealed vault stops its evidence batches (UC-16-06 step 2).
            raise Problem(409, "invalid_state", f"The vault is {vault.status}.")
        now = await session.scalar(select(func.now()))
        assert now is not None
        pending = await session.scalar(
            select(vaultdb.Outbox.payload)
            .where(
                vaultdb.Outbox.aggregate_id == thesis.id,
                vaultdb.Outbox.event_type == REFRESH_EVENT,
                vaultdb.Outbox.created_at > now - REFRESH_WINDOW,
            )
            .order_by(vaultdb.Outbox.created_at.desc())
            .limit(1)
        )
        if isinstance(pending, dict) and pending.get("agent_run_id"):
            # BR-192: joins the batch already asked for this hour; no second decrypt.
            run_id = uuid.UUID(str(pending["agent_run_id"]))
        else:
            decrypts = await session.scalar(
                select(func.count()).where(
                    vaultdb.KeyAccessLog.thesis_id == thesis.id,
                    vaultdb.KeyAccessLog.action == "decrypt_condition",
                    vaultdb.KeyAccessLog.at > now - datetime.timedelta(hours=24),
                )
            )
            if int(decrypts or 0) >= DECRYPTS_PER_DAY:
                raise Problem(
                    429,
                    "rate_limited",
                    "The condition was decrypted 3 times in the last 24 hours (BR-192).",
                )
            # The Evidence agent's run is created by the worker under this id (agent schema).
            run_id = uuid7()
            session.add(
                _event(
                    principal,
                    "thesis",
                    thesis.id,
                    REFRESH_EVENT,
                    thesis_id=thesis.id,
                    vault_id=vault.id,
                    owner_id=principal.user_id,
                    workflow_id=thesis.workflow_id,
                    agent_run_id=run_id,
                    purpose="evidence_refresh",
                )
            )
            await session.flush()
    response.headers["Location"] = f"/v1/runs/{run_id}"
    return Accepted(id=run_id, status="queued", poll_after_ms=5_000)


@router.get(
    "/v1/exec/theses/{id}/scores",
    response_model=Page[ScoreView],
    **endpoint("GET", "/v1/exec/theses/{id}/scores", operation_id="listExecThesisScores"),
)
async def list_thesis_scores(
    thesis_id: IdPath,
    principal: CurrentPrincipal,
    page: Paging,
    order: Annotated[
        Order, Depends(order_by("scored_at", "as_of", "created_at", default="scored_at desc"))
    ],
) -> Page[ScoreView]:
    s = vaultdb.ThesisScore
    async with transaction(ROLE, principal) as session:
        thesis = await _thesis(session, thesis_id, principal)
        query = keyset(
            select(s).where(s.thesis_id == thesis.id),
            columns={"scored_at": s.scored_at, "as_of": s.as_of, "created_at": s.created_at},
            tiebreak=s.id,
            order=order,
            page=page,
        )
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[ScoreView.model_validate(r) for r in rows], next_page_token=token)


@router.get(
    "/v1/exec/scores",
    response_model=Page[ScoreWithThesis],
    **endpoint("GET", "/v1/exec/scores", operation_id="listExecScores"),
)
async def list_scores(
    principal: CurrentPrincipal,
    page: Paging,
    from_: FromQuery = None,
    to: ToQuery = None,
) -> Page[ScoreWithThesis]:
    s, t = vaultdb.ThesisScore, vaultdb.Thesis
    query = (
        select(s, t.committed_at)
        .join(t, t.id == s.thesis_id)
        .where(s.org_id == principal.org_id, s.owner_id == principal.user_id)
    )
    if from_:
        query = query.where(s.scored_at >= from_)
    if to:
        query = query.where(s.scored_at < to)
    async with transaction(ROLE, principal) as session:
        rows, token = await _paged(session, query, page, s.scored_at, s.id)
    items = [
        ScoreWithThesis.model_validate(
            {**ScoreView.model_validate(r[0]).model_dump(), "committed_at": r[1]}
        )
        for r in rows
    ]
    return Page(items=items, next_page_token=token)


@router.get(
    "/v1/exec/key-access-log",
    response_model=Page[KeyAccessView],
    **endpoint("GET", "/v1/exec/key-access-log", operation_id="listExecKeyAccessLog"),
)
async def list_key_access(
    principal: CurrentPrincipal,
    page: Paging,
    order: Annotated[Order, Depends(order_by("at", "action", default="at desc"))],
    thesis_id: uuid.UUID | None = None,
    from_: FromQuery = None,
    to: ToQuery = None,
    action: KeyAction | None = None,
) -> Page[KeyAccessView]:
    log = vaultdb.KeyAccessLog
    query = select(log).where(log.org_id == principal.org_id, log.owner_id == principal.user_id)
    if thesis_id:
        query = query.where(log.thesis_id == thesis_id)
    if from_:
        query = query.where(log.at >= from_)
    if to:
        query = query.where(log.at < to)
    if action:
        query = query.where(log.action == action)
    query = keyset(
        query,
        columns={"at": log.at, "action": log.action},
        tiebreak=log.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[KeyAccessView.model_validate(r) for r in rows], next_page_token=token)


# ─── private memory (UC-16-02) ───


@router.get(
    "/v1/exec/memory-items",
    response_model=Page[MemoryItemView],
    **endpoint("GET", "/v1/exec/memory-items", operation_id="listExecMemoryItems"),
)
async def list_memory_items(
    principal: CurrentPrincipal,
    page: Paging,
    order: Annotated[Order, Depends(order_by("created_at", "kind", default="created_at desc"))],
    kind: MemoryKind | None = None,
    from_: FromQuery = None,
    to: ToQuery = None,
) -> Page[MemoryItemView]:
    m = vaultdb.MemoryItem
    query = select(m).where(m.org_id == principal.org_id, m.owner_id == principal.user_id)
    if kind:
        query = query.where(m.kind == kind)
    if from_:
        query = query.where(m.created_at >= from_)
    if to:
        query = query.where(m.created_at < to)
    query = keyset(
        query,
        columns={"created_at": m.created_at, "kind": m.kind},
        tiebreak=m.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(items=[MemoryItemView.model_validate(r) for r in rows], next_page_token=token)


@router.post(
    "/v1/exec/memory-items",
    status_code=201,
    response_model=MemoryItemView,
    **endpoint("POST", "/v1/exec/memory-items", operation_id="createExecMemoryItem"),
)
async def create_memory_item(body: MemoryItemCreate, principal: CurrentPrincipal) -> MemoryItemView:
    async with transaction(ROLE, principal) as session:
        vault = await _writable_vault(session, principal)
        if body.related_thesis_id is not None:
            # A foreign key does not see RLS: without this another leader's thesis id would do.
            await _thesis(session, body.related_thesis_id, principal)
        item = vaultdb.MemoryItem(
            id=uuid7(),
            org_id=principal.org_id,
            vault_id=vault.id,
            owner_id=principal.user_id,
            kind=body.kind,
            content_ct=body.content_ct,
            wrapped_dek=body.wrapped_dek,
            content_version=body.content_version,
            related_thesis_id=body.related_thesis_id,
            created_by=principal.user_id,
        )
        session.add(item)
        await session.flush()
        session.add(
            _event(
                principal,
                "memory_item",
                item.id,
                "exec.memory_item.created",
                memory_item_id=item.id,
                vault_id=vault.id,
                kind=body.kind,
            )
        )
        await session.flush()
        await session.refresh(item)
        return MemoryItemView.model_validate(item)


@router.post(
    "/v1/exec/memory-items/{id}:shred",
    response_model=MemoryItemView,
    **endpoint("POST", "/v1/exec/memory-items/{id}:shred", operation_id="shredExecMemoryItem"),
)
async def shred_memory_item(item_id: IdPath, principal: CurrentPrincipal) -> MemoryItemView:
    async with transaction(ROLE, principal) as session:
        item = await session.scalar(
            select(vaultdb.MemoryItem)
            .where(
                vaultdb.MemoryItem.id == item_id,
                vaultdb.MemoryItem.org_id == principal.org_id,
                vaultdb.MemoryItem.owner_id == principal.user_id,
            )
            .with_for_update()
        )
        if item is None:
            raise Problem(404, "not_found", "Memory item not found.")
        if item.shredded_at is not None:
            raise Problem(409, "invalid_state", "The item is already shredded.")
        # Dropping the wrapped DEK is the shred; the trigger stamps shredded_at (UC-16-02).
        item.content_ct = None
        item.wrapped_dek = None
        session.add(
            _event(
                principal,
                "memory_item",
                item.id,
                "exec.memory_item.shredded",
                memory_item_id=item.id,
                vault_id=item.vault_id,
            )
        )
        await session.flush()
        await session.refresh(item)
        return MemoryItemView.model_validate(item)


# ─── private questions (UC-16-02) ───


@router.post(
    "/v1/exec/private-questions",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/exec/private-questions", operation_id="askExecPrivateQuestion"),
)
async def ask_private_question(
    body: PrivateQuestionCreate,
    principal: CurrentPrincipal,
    request: Request,
    response: Response,
) -> Accepted:
    context = [item.text for item in body.context_items]
    if sum(math.ceil(len(c) / CHARS_PER_TOKEN) for c in context) > CONTEXT_TOKENS:
        raise Problem(
            422, "validation_failed", "context_items exceed 4,000 tokens; attach fewer items."
        )
    channel = private_questions(request)
    if channel is None:
        raise Problem(503, "model_unavailable", "The local model is not available.")
    question_id = uuid7()
    # No row anywhere: the channel holds the text for the run and drops it (BR-193).
    try:
        await channel.submit(
            PrivateQuestion(
                id=question_id,
                org_id=principal.org_id,
                owner_id=principal.user_id,
                text=body.text,
                context=context,
                as_of=body.as_of.isoformat() if body.as_of else None,
            )
        )
    except ModelUnavailable as exc:
        # BR-193: never routed to a frontier provider instead.
        raise Problem(
            503, "model_unavailable", "The local model is busy; ask again shortly."
        ) from exc
    location = f"/v1/exec/private-questions/{question_id}/stream"
    response.headers["Location"] = location
    return Accepted(id=question_id, status="queued", poll_after_ms=500)


@router.get(
    "/v1/exec/private-questions/{id}/stream",
    **endpoint(
        "GET", "/v1/exec/private-questions/{id}/stream", operation_id="streamExecPrivateQuestion"
    ),
)
async def stream_private_question(
    question_id: IdPath,
    principal: CurrentPrincipal,
    request: Request,
    last_id: Annotated[str | None, Depends(get_last_event_id)],
) -> Response:
    try:
        after = int(last_id) if last_id else -1
    except ValueError as exc:
        raise Problem(
            400, "invalid_request", "Last-Event-ID is not an id this stream sent."
        ) from exc
    channel = private_questions(request)
    events = (
        await channel.events(
            org_id=principal.org_id, owner_id=principal.user_id, question_id=question_id
        )
        if channel is not None
        else None
    )
    if events is None:
        # The state lives 15 minutes and only for its asker (UC-16-02 step 4).
        raise Problem(410, "gone", "This private answer has expired.")
    fresh = [e for e in events if int(e.id) > after]

    async def produce() -> AsyncIterator[Event]:
        for item in fresh:
            yield item

    return event_stream(produce())


# ─── publications (UC-16-05) ───


@router.post(
    "/v1/exec/theses/{id}/publications",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/exec/theses/{id}/publications", operation_id="createExecPublication"),
)
async def create_publication(
    body: PublicationCreate,
    thesis_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> Accepted:
    # svc_exec reads nothing outside schema exec, so the target is checked through the caller's
    # X-Data-Space: the identity resolver (svc_org) admits only a Data Space of the caller's
    # organisation where they are a current member, and 404s anything else.
    if body.target_data_space_id != data_space:
        raise Problem(
            422,
            "validation_failed",
            "target_data_space_id must be the Data Space named by X-Data-Space.",
        )
    if DIGIT.search(body.statement_public):
        # BR-162: numbers belong to the structured condition, never to the sentence.
        raise Problem(
            422,
            "business_rule_violated",
            "statement_public must not contain digits; the threshold goes in the condition (BR-162).",
        )
    async with transaction(ROLE, principal) as session:
        thesis = await _thesis(session, thesis_id, principal, lock=True)
        if thesis.status not in PUBLISHABLE:
            raise Problem(
                409, "invalid_state", f"A {thesis.status} thesis cannot be published (BR-194)."
            )
        publication = vaultdb.Publication(
            id=uuid7(),
            org_id=principal.org_id,
            thesis_id=thesis.id,
            owner_id=principal.user_id,
            title=body.title,
            statement_public=body.statement_public,
            ref_kind=body.ref_kind,
            metric_ref=body.metric_ref,
            scope=body.scope,
            period_label=body.period_label,
            comparator=body.comparator,
            threshold=body.threshold,
            threshold_hi=body.threshold_hi,
            unit=body.unit,
            due_at=thesis.due_at,
            attribution=body.attribution,
            attribution_label=body.attribution_label,
            confidence_public=body.confidence_public,
            rationale_public=body.rationale_public,
            target_data_space_id=body.target_data_space_id,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(publication)
        await session.flush()
        # The workflow checks the published condition against cond_ct (purpose validate), then the
        # Context service drafts the hypothesis page and its review proposal (BR-194).
        session.add(
            _event(
                principal,
                "publication",
                publication.id,
                "exec.publication.requested",
                publication_id=publication.id,
                thesis_id=thesis.id,
                owner_id=principal.user_id,
                workflow_id=thesis.workflow_id,
                target_data_space_id=body.target_data_space_id,
                status="pending_review",
            )
        )
        await session.flush()
    response.headers["Location"] = f"/v1/exec/publications/{publication.id}"
    return Accepted(id=publication.id, status="pending_review", poll_after_ms=2_000)


async def _publication(
    session: AsyncSession, publication_id: uuid.UUID, principal: Principal, *, lock: bool = False
) -> vaultdb.Publication:
    query = select(vaultdb.Publication).where(
        vaultdb.Publication.id == publication_id,
        vaultdb.Publication.org_id == principal.org_id,
        vaultdb.Publication.owner_id == principal.user_id,
    )
    if lock:
        query = query.with_for_update()
    row: vaultdb.Publication | None = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Publication not found.")
    return row


@router.get(
    "/v1/exec/publications/{id}",
    response_model=PublicationView,
    **endpoint("GET", "/v1/exec/publications/{id}", operation_id="getExecPublication"),
)
async def get_publication(publication_id: IdPath, principal: CurrentPrincipal) -> PublicationView:
    async with transaction(ROLE, principal) as session:
        return PublicationView.model_validate(
            await _publication(session, publication_id, principal)
        )


@router.post(
    "/v1/exec/publications/{id}:withdraw",
    response_model=PublicationView,
    **endpoint(
        "POST", "/v1/exec/publications/{id}:withdraw", operation_id="withdrawExecPublication"
    ),
)
async def withdraw_publication(
    publication_id: IdPath,
    principal: CurrentPrincipal,
    body: Annotated[PublicationWithdraw, Body()] = _NO_REASON,
) -> PublicationView:
    # The reason is the leader's free text: it is not stored (no column) nor put in the event.
    async with transaction(ROLE, principal) as session:
        row = await _publication(session, publication_id, principal, lock=True)
        if row.status not in OPEN_PUBLICATION:
            raise Problem(409, "invalid_state", f"The publication is {row.status}.")
        row.status = "withdrawn"
        row.withdrawn_at = func.now()
        row.updated_at = func.now()
        row.updated_by = principal.user_id
        # exec.publication_emit writes exec.publication.withdrawn; the Context service retires the
        # page from it. Writing a second event here would retire it twice.
        await session.flush()
        await session.refresh(row)
        return PublicationView.model_validate(row)


# ─── handovers (UC-16-06) ───


def _admin_kind(exit_policy: str) -> str:
    """BR-195: an administrator seals or shreds as the contract says; a selective handover needs
    the owner, so without them it falls back to the seal."""
    return "seal" if exit_policy == "selective_handover" else exit_policy


def _handover_event(principal: Principal, row: vaultdb.Handover) -> vaultdb.Outbox:
    # exec.handover.<status>, the pattern of exec.publication.<status>: the shred worker acts on
    # exec.handover.approved with kind shred, the notifier on the others.
    return _event(
        principal,
        "handover",
        row.id,
        f"exec.handover.{row.status}",
        handover_id=row.id,
        vault_id=row.vault_id,
        owner_id=row.owner_id,
        kind=row.kind,
        status=row.status,
        successor_id=row.successor_id,
        owner_participates=row.owner_participates,
    )


async def _owner_handover(
    session: AsyncSession, principal: Principal, body: HandoverCreate
) -> vaultdb.Handover:
    vault = await _vault(session, principal, lock=True)
    if vault is None:
        raise Problem(404, "not_found", "No vault to hand over.")
    if vault.status == "shredded":
        raise Problem(409, "invalid_state", "The vault is already shredded.")
    if body.kind == "seal" and vault.status != "active":
        raise Problem(409, "invalid_state", f"The vault is {vault.status}.")
    if body.kind == "selective_handover" and vault.exit_policy != "selective_handover":
        raise Problem(
            422,
            "business_rule_violated",
            f"The contract's exit policy is {vault.exit_policy}, not selective_handover (BR-195).",
        )
    # The owner confirmed with a passkey on the device (UC-16-06): no second person is needed, so
    # a seal completes now, a selective handover opens for items and a shred waits for the worker.
    status = {"seal": "in_progress", "selective_handover": "in_progress", "shred": "approved"}
    row = vaultdb.Handover(
        id=uuid7(),
        org_id=principal.org_id,
        vault_id=vault.id,
        owner_id=principal.user_id,
        kind=body.kind,
        reason=body.reason,
        requested_by=principal.user_id,
        owner_participates=True,
        successor_id=body.successor_id,
        status=status[body.kind],
        contract_ref=body.contract_ref,
        created_by=principal.user_id,
        updated_by=principal.user_id,
    )
    session.add(row)
    await session.flush()
    if body.kind == "shred":
        await _log_key(
            session,
            principal,
            vault_id=vault.id,
            owner_id=principal.user_id,
            key_kind="vault_key",
            action="shred",
            purpose="shred",
            actor_kind="owner_device",
            actor_ref=f"handover:{row.id}",
        )
    return row


async def _admin_handover(
    session: AsyncSession, principal: Principal, owner_id: uuid.UUID, body: HandoverCreate
) -> vaultdb.Handover:
    # AC05 sees the five operational columns of the function, never the vault row (BR-189).
    vault = (
        await session.execute(
            text(
                "SELECT id, status, exit_policy, sealed_read_until "
                "FROM exec.vault_admin_overview() WHERE owner_id = :owner"
            ),
            {"owner": owner_id},
        )
    ).first()
    if vault is None:
        raise Problem(404, "not_found", "That user has no vault.")
    if body.kind == "selective_handover":
        raise Problem(
            422,
            "business_rule_violated",
            "A selective handover needs the owner; without them only seal or shred (BR-195).",
        )
    # The end of a seal's term is the seal policy itself ending in a shred (BR-195: "tới
    # sealed_read_until rồi huỷ khoá"), so it is not held to exit_policy = shred.
    expiry = body.kind == "shred" and body.reason == "sealed_expired"
    if not expiry and body.kind != _admin_kind(vault.exit_policy):
        raise Problem(
            422,
            "business_rule_violated",
            f"The contract's exit policy is {vault.exit_policy}; an administrator may only "
            f"{_admin_kind(vault.exit_policy)} (BR-195).",
        )
    if vault.status == "shredded" or (body.kind == "seal" and vault.status != "active"):
        raise Problem(409, "invalid_state", f"The vault is {vault.status}.")
    status = "in_progress" if body.kind == "seal" else "requested"
    if expiry:
        # The sealed term ran out (UC-16-06 step 3): the contract already decided, no AC06 step.
        expired = vault.status == "sealed" and (
            vault.sealed_read_until is not None and vault.sealed_read_until < _now().date()
        )
        if not expired:
            raise Problem(
                422,
                "business_rule_violated",
                "reason sealed_expired needs a sealed vault past sealed_read_until (BR-195).",
            )
        status = "approved"
    row = vaultdb.Handover(
        id=uuid7(),
        org_id=principal.org_id,
        vault_id=vault.id,
        owner_id=owner_id,
        kind=body.kind,
        reason=body.reason,
        requested_by=principal.user_id,
        owner_participates=False,
        status=status,
        contract_ref=body.contract_ref,
        created_by=principal.user_id,
        updated_by=principal.user_id,
    )
    session.add(row)
    await session.flush()
    if status == "approved":
        await _log_key(
            session,
            principal,
            vault_id=row.vault_id,
            owner_id=owner_id,
            key_kind="vault_key",
            action="shred",
            purpose="shred",
            actor_kind="admin_request",
            actor_ref=f"handover:{row.id}",
        )
    return row


@router.post(
    "/v1/exec/handovers",
    status_code=201,
    response_model=HandoverView,
    **endpoint("POST", "/v1/exec/handovers", operation_id="createExecHandover"),
)
async def create_handover(
    body: HandoverCreate,
    principal: CurrentPrincipal,
    owner_id: Annotated[
        uuid.UUID | None,
        Query(
            description="AC05 only: the leaving owner. Omitted (or the caller's own id) means the "
            "caller's own vault."
        ),
    ] = None,
) -> HandoverView:
    if (body.kind == "selective_handover") != (body.successor_id is not None):
        raise Problem(
            422,
            "validation_failed",
            "successor_id is required for selective_handover, and only for it.",
        )
    by_admin = owner_id is not None and owner_id != principal.user_id
    if not by_admin and "exec_vault" not in principal.capabilities:
        raise Problem(422, "validation_failed", "owner_id names the leaving owner's vault.")
    if by_admin:
        # AC05 only: exec_vault, also in the A2 row, never opens another leader's handover.
        _require(principal, "admin")
    settings = {"exec_admin": "on"} if by_admin else {}
    async with transaction(ROLE, principal, **settings) as session:
        if body.successor_id is not None and not await session.scalar(
            text("SELECT exec.successor_has_active_vault(:id)"), {"id": body.successor_id}
        ):
            # UC-16-06 precondition: items are sealed to the successor's own vault key.
            raise Problem(
                422, "business_rule_violated", "The successor has no active vault to receive items."
            )
        if by_admin:
            assert owner_id is not None
            row = await _admin_handover(session, principal, owner_id, body)
        else:
            row = await _owner_handover(session, principal, body)
        if row.kind == "seal":
            # No approval for a seal (UC-16-06 step 1): exec.handover_complete seals the vault.
            row.status = "completed"
            row.completed_at = func.now()
            row.updated_at = func.now()
            await session.flush()
        await session.refresh(row)
        session.add(_handover_event(principal, row))
        await session.flush()
        return HandoverView.model_validate(row)


# The handover processing of UC-16-06 is AC05's (admin) and AC06's (compliance). app.exec_admin opens
# every handover of the organisation, so it is set only after this check of the route itself, not
# on the strength of the A2 row alone (whose capability may be broader, e.g. exec_vault).
HANDOVER_ADMINS = frozenset({"admin", "compliance"})


def _sees_every_handover(principal: Principal) -> bool:
    return bool(principal.capabilities & HANDOVER_ADMINS)


def _require(principal: Principal, capability: str) -> None:
    if capability not in principal.capabilities:
        raise Problem(403, "forbidden", f"Missing capability: {capability}.")


async def _handover(
    session: AsyncSession, handover_id: uuid.UUID, principal: Principal, *, lock: bool = False
) -> vaultdb.Handover:
    query = select(vaultdb.Handover).where(
        vaultdb.Handover.id == handover_id, vaultdb.Handover.org_id == principal.org_id
    )
    if lock:
        query = query.with_for_update()
    row: vaultdb.Handover | None = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Handover not found.")
    return row


@router.get(
    "/v1/exec/handovers/{id}",
    response_model=HandoverView,
    **endpoint("GET", "/v1/exec/handovers/{id}", operation_id="getExecHandover"),
)
async def get_handover(handover_id: IdPath, principal: CurrentPrincipal) -> HandoverView:
    # AC05 and AC06 read the handover through the admin policy; the table has no ciphertext.
    settings = {"exec_admin": "on"} if _sees_every_handover(principal) else {}
    async with transaction(ROLE, principal, **settings) as session:
        return HandoverView.model_validate(await _handover(session, handover_id, principal))


@router.post(
    "/v1/exec/handovers/{id}/items",
    status_code=201,
    response_model=list[HandoverItemView],
    **endpoint("POST", "/v1/exec/handovers/{id}/items", operation_id="addExecHandoverItems"),
)
async def add_handover_items(
    body: HandoverItemsCreate, handover_id: IdPath, principal: CurrentPrincipal
) -> list[HandoverItemView]:
    async with transaction(ROLE, principal) as session:
        row = await _handover(session, handover_id, principal, lock=True)
        if row.owner_id != principal.user_id:
            raise Problem(404, "not_found", "Handover not found.")
        if row.kind != "selective_handover" or row.status not in ("approved", "in_progress"):
            raise Problem(
                409,
                "invalid_state",
                f"Items go to an approved or running selective handover; this one is a "
                f"{row.status} {row.kind}.",
            )
        assert row.successor_id is not None
        now = await session.scalar(select(func.now()))
        items = [
            HandoverItemView(
                id=uuid7(),
                handover_id=row.id,
                owner_id=principal.user_id,
                successor_id=row.successor_id,
                source_kind=item.source_kind,
                source_id=item.source_id,
                payload_sealed=item.payload_sealed,
                imported_at=None,
                created_at=now or _now(),
            )
            for item in body.items
        ]
        # No RETURNING: the rows are the successor's to read, not the owner's (successor_read).
        await session.execute(
            insert(vaultdb.HandoverItem),
            [
                {
                    **item.model_dump(exclude={"payload_sealed", "imported_at"}),
                    "payload_sealed": item.payload_sealed,
                    "org_id": principal.org_id,
                    "created_by": principal.user_id,
                }
                for item in items
            ],
        )
        await _log_key(
            session,
            principal,
            vault_id=row.vault_id,
            owner_id=principal.user_id,
            key_kind="box_key",
            action="rewrap_handover",
            purpose="handover",
            actor_kind="owner_device",
            actor_ref=f"handover:{row.id}",
        )
        row.status = "in_progress"
        row.updated_at = func.now()
        row.updated_by = principal.user_id
        await session.flush()
        await session.refresh(row)
        session.add(
            _event(
                principal,
                "handover",
                row.id,
                "exec.handover.items_added",
                handover_id=row.id,
                successor_id=row.successor_id,
                items_added=len(items),
                items_total=row.items_total,
                status=row.status,
            )
        )
        await session.flush()
    return items


@router.get(
    "/v1/exec/handovers/{id}/items",
    response_model=Page[HandoverItemView],
    **endpoint("GET", "/v1/exec/handovers/{id}/items", operation_id="listExecHandoverItems"),
)
async def list_handover_items(
    handover_id: IdPath,
    principal: CurrentPrincipal,
    page: Paging,
    order: Annotated[
        Order, Depends(order_by("created_at", "source_kind", default="created_at asc"))
    ],
) -> Page[HandoverItemView]:
    i = vaultdb.HandoverItem
    # successor_read: the caller sees only the items sealed for them.
    query = select(i).where(
        i.handover_id == handover_id,
        i.org_id == principal.org_id,
        i.successor_id == principal.user_id,
    )
    query = keyset(
        query,
        columns={"created_at": i.created_at, "source_kind": i.source_kind},
        tiebreak=i.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    if not rows and not page.cursor:
        raise Problem(404, "not_found", "No handover items for you here.")
    return Page(items=[HandoverItemView.model_validate(r) for r in rows], next_page_token=token)


@router.post(
    "/v1/exec/handovers/{id}:approve",
    response_model=HandoverView,
    **endpoint("POST", "/v1/exec/handovers/{id}:approve", operation_id="approveExecHandover"),
)
async def approve_handover(
    handover_id: IdPath,
    principal: CurrentPrincipal,
    body: Annotated[HandoverApprove, Body()] = _NO_NOTE,
) -> HandoverView:
    # The note is free text with no column; like a withdraw reason it is not kept.
    _require(principal, "compliance")
    async with transaction(ROLE, principal, exec_admin="on") as session:
        row = await _handover(session, handover_id, principal, lock=True)
        if principal.user_id in (row.requested_by, row.owner_id):
            # BR-195: two people, AC05 asks and another AC06 approves.
            raise Problem(
                403, "policy_denied", "The requester cannot approve their own request (BR-195)."
            )
        if row.kind != "shred" or row.owner_participates or row.status != "requested":
            raise Problem(
                409,
                "invalid_state",
                "Only a requested shred without the owner waits for approval.",
            )
        row.status = "approved"
        row.approved_by = principal.user_id
        row.updated_at = func.now()
        row.updated_by = principal.user_id
        await session.flush()
        # Logged once the handover is approved: the log trigger accepts an admin_request row only
        # for a vault with an approved or running handover (BR-192, BR-195).
        await _log_key(
            session,
            principal,
            vault_id=row.vault_id,
            owner_id=row.owner_id,
            key_kind="vault_key",
            action="shred",
            purpose="shred",
            actor_kind="admin_request",
            actor_ref=f"handover:{row.id}",
        )
        await session.refresh(row)
        session.add(_handover_event(principal, row))
        await session.flush()
        return HandoverView.model_validate(row)


@router.post(
    "/v1/exec/handovers/{id}:accept",
    response_model=HandoverAccepted,
    **endpoint("POST", "/v1/exec/handovers/{id}:accept", operation_id="acceptExecHandover"),
)
async def accept_handover(handover_id: IdPath, principal: CurrentPrincipal) -> HandoverAccepted:
    i = vaultdb.HandoverItem
    async with transaction(ROLE, principal) as session:
        rows = (
            await session.execute(
                select(i.id, i.imported_at)
                .where(
                    i.handover_id == handover_id,
                    i.org_id == principal.org_id,
                    i.successor_id == principal.user_id,
                )
                .with_for_update()
            )
        ).all()
        if not rows:
            raise Problem(404, "not_found", "No handover items for you here.")
        pending = [r.id for r in rows if r.imported_at is None]
        if not pending:
            raise Problem(409, "invalid_state", "Every item is already imported.")
        # The successor re-encrypted the items into their own vault on the device; marking them
        # imported lets the daily cleanup delete the transit rows (exec.handover_item_cleanup).
        await session.execute(
            update(i)
            .where(i.id.in_(pending))
            .values(imported_at=func.now())
            .execution_options(synchronize_session=False)
        )
        # The successor can neither read nor update exec.handover (owner only): the definer routine
        # completes it once nothing is left unimported, which seals the old vault (UC-16-06).
        status = await session.scalar(text("SELECT exec.handover_accept(:id)"), {"id": handover_id})
        session.add(
            _event(
                principal,
                "handover",
                handover_id,
                "exec.handover.accepted",
                handover_id=handover_id,
                successor_id=principal.user_id,
                items_imported=len(rows),
                status=status,
            )
        )
        await session.flush()
    return HandoverAccepted(
        handover_id=handover_id,
        items_imported=len(rows),
        items_pending=0,
        status=status or "in_progress",
    )
