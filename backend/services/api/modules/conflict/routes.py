"""A2 group "Xung đột": conflicts between sources, their resolution, source authority and
resolution rules (UC-07-04, UC-07-05).

Every create and status change writes its event to conflict.outbox in the same transaction (D1
rule 8). Effects outside schema conflict go through those events: inbox closes the HITL request,
qa re-labels the answers that used a rejected figure, governance turns a remembered decision into
a behaviour policy (UC-07-08) and approves a drafted source authority (UC-07-05 step 5).
Sources are read through the catalog module's public interface, never its tables.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import conflict as cf
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Page,
    PageRequest,
    encode_page_token,
    invalid_page_token,
    page_request,
)
from services.api.kernel.problems import FieldError, Problem
from services.api.modules.catalog import sources_in_data_space
from services.api.modules.conflict import rules
from services.api.modules.conflict.schemas import (
    ClaimView,
    ConflictDetail,
    ConflictResolve,
    ConflictView,
    ResolutionRuleCreate,
    ResolutionRuleView,
    SourceAuthorityPut,
    SourceAuthorityView,
    SourceBrief,
    SuggestionView,
)

router = APIRouter()
ROLE = "svc_conflict"
PathId = Annotated[uuid.UUID, Path(alias="id")]
RESOLVABLE = ("open", "insufficient_basis")
BACKTEST_DAYS = 90
# Bound on the conflicts a synchronous backtest reads; the newest are the most telling.
BACKTEST_LIMIT = 5_000


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def _event(
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> cf.Outbox:
    return cf.Outbox(
        id=uuid7(),
        org_id=principal.org_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload={k: _jsonable(v) for k, v in payload.items()},
    )


# --- conflicts ----------------------------------------------------------------------------------


@router.get(
    "/v1/conflicts",
    response_model=Page[ConflictView],
    **endpoint("GET", "/v1/conflicts", operation_id="listConflicts"),
)
async def list_conflicts(
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    status: str | None = None,
    kind: str | None = None,
    material: bool | None = None,
) -> Page[ConflictView]:
    c = cf.Conflict
    query = select(c).where(c.org_id == principal.org_id, c.data_space_id == space)
    if status:
        query = query.where(c.status == status)
    if kind:
        query = query.where(c.kind == kind)
    if material is not None:
        query = query.where(c.material.is_(material))
    if page.cursor:
        try:
            after = (
                datetime.datetime.fromisoformat(page.cursor["created_at"]),
                uuid.UUID(page.cursor["id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(c.created_at, c.id) < after)
    query = query.order_by(c.created_at.desc(), c.id.desc()).limit(page.page_size + 1)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    token = (
        {"created_at": rows[-1].created_at.isoformat(), "id": str(rows[-1].id)} if more else None
    )
    return Page(
        items=[ConflictView.model_validate(r, from_attributes=True) for r in rows],
        next_page_token=encode_page_token(token),
    )


async def _conflict(
    session: AsyncSession,
    principal: Principal,
    space: uuid.UUID,
    conflict_id: uuid.UUID,
    *,
    lock: bool = False,
) -> cf.Conflict:
    query = select(cf.Conflict).where(
        cf.Conflict.id == conflict_id,
        cf.Conflict.org_id == principal.org_id,
        cf.Conflict.data_space_id == space,
    )
    if lock:
        # Two stewards deciding at once: the second finds it resolved.
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Conflict not found.")
    return row


async def _claims(session: AsyncSession, conflict_id: uuid.UUID) -> list[tuple[cf.Claim, str]]:
    rows = (
        await session.execute(
            select(cf.Claim, cf.ConflictClaim.role)
            .join(cf.ConflictClaim, cf.ConflictClaim.claim_id == cf.Claim.id)
            .where(cf.ConflictClaim.conflict_id == conflict_id)
            .order_by(cf.ConflictClaim.role.desc(), cf.Claim.extracted_at, cf.Claim.id)
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


async def _authority(
    session: AsyncSession, principal: Principal, space: uuid.UUID, data_kind: str
) -> cf.SourceAuthority | None:
    row: cf.SourceAuthority | None = await session.scalar(
        select(cf.SourceAuthority).where(
            cf.SourceAuthority.org_id == principal.org_id,
            cf.SourceAuthority.data_space_id == space,
            cf.SourceAuthority.data_kind == data_kind,
        )
    )
    return row


def _facts(row: cf.Conflict) -> dict[str, Any]:
    return {
        "kind": row.kind,
        "subject_key": row.subject_key,
        "attribute": row.attribute,
        "detected_at_stage": row.detected_at_stage,
        "material": row.material,
    }


@router.get(
    "/v1/conflicts/{id}",
    response_model=ConflictDetail,
    **endpoint("GET", "/v1/conflicts/{id}", operation_id="getConflict"),
)
async def get_conflict(
    conflict_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> ConflictDetail:
    async with transaction(ROLE, principal) as session:
        row = await _conflict(session, principal, space, conflict_id)
        claims = await _claims(session, row.id)
        # The data kind of a conflict is its attribute (dư nợ, số khách, …), as source
        # authority and resolution rules are keyed.
        authority = await _authority(session, principal, space, row.attribute)
        active_rules = (
            await session.execute(
                select(cf.ResolutionRule.condition, cf.ResolutionRule.action)
                .where(
                    cf.ResolutionRule.org_id == principal.org_id,
                    cf.ResolutionRule.data_space_id == space,
                    cf.ResolutionRule.data_kind == row.attribute,
                    cf.ResolutionRule.status == "active",
                )
                .order_by(cf.ResolutionRule.created_at.desc())
            )
        ).all()
        view = ConflictView.model_validate(row, from_attributes=True)
    sources = await sources_in_data_space(
        principal, space, (c.source_id for c, _ in claims if c.source_id is not None)
    )
    # Only an approved ranking decides (UC-07-05 step 5); a draft is shown, not applied.
    ranking = list(authority.ranking) if authority and authority.status == "active" else []
    position = {source: i + 1 for i, source in enumerate(ranking)}
    claim_views = []
    for claim, role in claims:
        summary = sources.get(claim.source_id) if claim.source_id else None
        claim_views.append(
            ClaimView(
                **{
                    k: getattr(claim, k)
                    for k in ClaimView.model_fields
                    if k not in ("role", "source", "rank")
                },
                role=role,
                source=SourceBrief(
                    id=summary.id,
                    name=summary.name,
                    kind=summary.kind,
                    authority_level=summary.authority_level,
                )
                if summary
                else None,
                rank=position.get(claim.source_id) if claim.source_id else None,
            )
        )
    suggestion = rules.suggest(
        _facts(row),
        [rules.Candidate(claim.id, claim.source_id) for claim, _ in claims],
        [(r.condition, r.action) for r in active_rules],
        ranking,
    )
    return ConflictDetail(
        **view.model_dump(),
        claims=claim_views,
        authority=SourceAuthorityView.model_validate(authority, from_attributes=True)
        if authority
        else None,
        suggestion=SuggestionView(
            claim_id=suggestion.claim_id,
            outcome=suggestion.outcome,
            basis=suggestion.basis,
        ),
    )


@router.post(
    "/v1/conflicts/{id}:resolve",
    response_model=ConflictView,
    **endpoint("POST", "/v1/conflicts/{id}:resolve", operation_id="resolveConflict"),
)
async def resolve_conflict(
    body: ConflictResolve, conflict_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> ConflictView:
    note = (body.note or "").strip() or None
    async with transaction(ROLE, principal) as session:
        row = await _conflict(session, principal, space, conflict_id, lock=True)
        if row.status not in RESOLVABLE:
            raise Problem(409, "invalid_state", f"The conflict is already {row.status}.")
        claims = await _claims(session, row.id)
        chosen: cf.Claim | None = None
        if body.chosen_claim_id is not None:
            chosen = next((c for c, _ in claims if c.id == body.chosen_claim_id), None)
            if chosen is None:
                raise Problem(
                    422,
                    "validation_failed",
                    "chosen_claim_id is not one of the conflict's claims.",
                    errors=[FieldError(field="chosen_claim_id", code="not_a_claim", message="")],
                )
        rule: cf.ResolutionRule | None = None
        if body.remember_as_rule:
            if chosen is None or chosen.source_id is None:
                raise Problem(
                    422,
                    "business_rule_violated",
                    "A rule picks a source; the chosen claim has no source to remember.",
                )
            # UC-07-04 step 6 → UC-07-08: the decision becomes an approved rule of this data kind;
            # governance drafts the HITL-origin behaviour policy from the event below.
            rule = cf.ResolutionRule(
                id=uuid7(),
                org_id=principal.org_id,
                data_space_id=row.data_space_id,
                data_kind=row.attribute,
                condition={"kind": row.kind},
                action={"source_id": str(chosen.source_id)},
                origin="hitl",
                status="active",
                created_by=principal.user_id,
                updated_by=principal.user_id,
            )
            session.add(rule)
        previous = row.status
        if body.outcome == "insufficient_basis":
            # 6a: the data engineer checks the unexplained gap; answers keep their warning.
            row.status, row.outcome = "insufficient_basis", None
            event_type = "conflict.marked_insufficient_basis"
        else:
            row.status, row.outcome = "resolved", body.outcome
            event_type = "conflict.resolved"
        row.chosen_claim_id = chosen.id if chosen else None
        row.decision_note = note
        row.updated_at, row.updated_by = _now(), principal.user_id
        if rule is not None:
            await session.flush()
            session.add(
                _event(
                    principal,
                    "resolution_rule",
                    rule.id,
                    "resolution_rule.created",
                    resolution_rule_id=rule.id,
                    data_space_id=rule.data_space_id,
                    data_kind=rule.data_kind,
                    origin=rule.origin,
                    conflict_id=row.id,
                    created_by=principal.user_id,
                )
            )
        rejected = [c.id for c, _ in claims if chosen is not None and c.id != chosen.id]
        # BR-31: source data is never changed; the losing sources' owners are told (inbox),
        # answers that used a rejected figure are re-labelled (qa), the HITL closes (inbox).
        session.add(
            _event(
                principal,
                "conflict",
                row.id,
                event_type,
                conflict_id=row.id,
                data_space_id=row.data_space_id,
                status=row.status,
                previous_status=previous,
                outcome=row.outcome,
                chosen_claim_id=row.chosen_claim_id,
                rejected_claim_ids=rejected,
                rejected_source_ids=[
                    c.source_id for c, _ in claims if c.id in rejected and c.source_id is not None
                ],
                hitl_request_id=row.hitl_request_id,
                affected_answer_ids=list(row.affected_answer_ids or []),
                resolution_rule_id=rule.id if rule else None,
                resolved_by=principal.user_id,
            )
        )
        await session.flush()
        await session.refresh(row)
        return ConflictView.model_validate(row, from_attributes=True)


# --- source authority (UC-07-05) ----------------------------------------------------------------


@router.get(
    "/v1/source-authorities",
    response_model=Page[SourceAuthorityView],
    **endpoint("GET", "/v1/source-authorities", operation_id="listSourceAuthorities"),
)
async def list_source_authorities(
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    data_kind: str | None = None,
) -> Page[SourceAuthorityView]:
    sa = cf.SourceAuthority
    query = select(sa).where(sa.org_id == principal.org_id, sa.data_space_id == space)
    if data_kind:
        query = query.where(sa.data_kind == data_kind)
    if page.cursor:
        try:
            after = (str(page.cursor["data_kind"]), uuid.UUID(page.cursor["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(sa.data_kind, sa.id) > after)
    query = query.order_by(sa.data_kind, sa.id).limit(page.page_size + 1)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    token = {"data_kind": rows[-1].data_kind, "id": str(rows[-1].id)} if more else None
    return Page(
        items=[SourceAuthorityView.model_validate(r, from_attributes=True) for r in rows],
        next_page_token=encode_page_token(token),
    )


async def _past_conflicts(
    session: AsyncSession, principal: Principal, space: uuid.UUID, data_kind: str
) -> list[list[rules.Candidate]]:
    since = _now() - datetime.timedelta(days=BACKTEST_DAYS)
    rows = (
        await session.execute(
            select(cf.ConflictClaim.conflict_id, cf.Claim.id, cf.Claim.source_id)
            .join(cf.Claim, cf.Claim.id == cf.ConflictClaim.claim_id)
            .where(
                cf.ConflictClaim.conflict_id.in_(
                    select(cf.Conflict.id)
                    .where(
                        cf.Conflict.org_id == principal.org_id,
                        cf.Conflict.data_space_id == space,
                        cf.Conflict.attribute == data_kind,
                        cf.Conflict.created_at >= since,
                    )
                    .order_by(cf.Conflict.created_at.desc())
                    .limit(BACKTEST_LIMIT)
                )
            )
        )
    ).all()
    grouped: dict[uuid.UUID, list[rules.Candidate]] = {}
    for conflict_id, claim_id, source_id in rows:
        grouped.setdefault(conflict_id, []).append(rules.Candidate(claim_id, source_id))
    return list(grouped.values())


@router.put(
    "/v1/source-authorities",
    response_model=SourceAuthorityView,
    **endpoint("PUT", "/v1/source-authorities", operation_id="putSourceAuthority"),
)
async def put_source_authority(
    body: SourceAuthorityPut, principal: CurrentPrincipal, space: CurrentDataSpace
) -> SourceAuthorityView:
    data_kind = body.data_kind.strip()
    if len(set(body.ranking)) != len(body.ranking):
        raise Problem(422, "validation_failed", "A source appears twice in the ranking.")
    sources = await sources_in_data_space(principal, space, body.ranking)
    unknown = [str(s) for s in body.ranking if s not in sources]
    if unknown:
        raise Problem(
            422,
            "validation_failed",
            "The ranking names sources that are not in this Data Space.",
            errors=[FieldError(field="ranking", code="unknown_source", message=", ".join(unknown))],
        )
    violation = rules.ranking_violation(
        body.ranking, {s: summary.authority_level for s, summary in sources.items()}
    )
    if violation:
        raise Problem(422, "business_rule_violated", violation)
    async with transaction(ROLE, principal) as session:
        backtest = rules.backtest(
            await _past_conflicts(session, principal, space, data_kind), body.ranking
        )
        row = await session.scalar(
            select(cf.SourceAuthority)
            .where(
                cf.SourceAuthority.org_id == principal.org_id,
                cf.SourceAuthority.data_space_id == space,
                cf.SourceAuthority.data_kind == data_kind,
            )
            .with_for_update()
        )
        previous = row.status if row else None
        if row is None:
            row = cf.SourceAuthority(
                id=uuid7(),
                org_id=principal.org_id,
                data_space_id=space,
                data_kind=data_kind,
                created_by=principal.user_id,
            )
            session.add(row)
        row.ranking = list(body.ranking)
        if body.materiality is not None:
            row.materiality = body.materiality.model_dump(mode="json")
        # A draft until approved (UC-07-05 step 5, E1 for data in filed reports): one row per
        # data kind, so the previous ranking stops applying until the new one is approved.
        row.status, row.approved_by, row.approved_at = "draft", None, None
        row.backtest = backtest
        row.updated_at, row.updated_by = _now(), principal.user_id
        await session.flush()
        session.add(
            _event(
                principal,
                "source_authority",
                row.id,
                "source_authority.drafted",
                source_authority_id=row.id,
                data_space_id=space,
                data_kind=data_kind,
                status=row.status,
                previous_status=previous,
                ranking=row.ranking,
                drafted_by=principal.user_id,
            )
        )
        await session.flush()
        await session.refresh(row)
        return SourceAuthorityView.model_validate(row, from_attributes=True)


# --- resolution rules ---------------------------------------------------------------------------


@router.post(
    "/v1/resolution-rules",
    status_code=201,
    response_model=ResolutionRuleView,
    **endpoint("POST", "/v1/resolution-rules", operation_id="createResolutionRule"),
)
async def create_resolution_rule(
    body: ResolutionRuleCreate, principal: CurrentPrincipal, space: CurrentDataSpace
) -> ResolutionRuleView:
    unknown_keys = sorted(set(body.condition) - rules.CONDITION_KEYS)
    if unknown_keys:
        raise Problem(
            422,
            "validation_failed",
            "condition tests only kind, subject_key, attribute, detected_at_stage, material.",
            errors=[
                FieldError(field="condition", code="unknown_key", message=", ".join(unknown_keys))
            ],
        )
    source_id = rules.rule_source(body.action)
    if source_id is None:
        raise Problem(
            422,
            "validation_failed",
            'action names the source to choose: {"source_id": …}.',
            errors=[FieldError(field="action.source_id", code="missing", message="")],
        )
    if source_id not in await sources_in_data_space(principal, space, [source_id]):
        raise Problem(
            422, "validation_failed", "action.source_id is not a source of this Data Space."
        )
    async with transaction(ROLE, principal) as session:
        rule = cf.ResolutionRule(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=space,
            data_kind=body.data_kind.strip(),
            condition=body.condition,
            action={**body.action, "source_id": str(source_id)},
            origin="manual",
            status="active",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(rule)
        await session.flush()
        session.add(
            _event(
                principal,
                "resolution_rule",
                rule.id,
                "resolution_rule.created",
                resolution_rule_id=rule.id,
                data_space_id=space,
                data_kind=rule.data_kind,
                origin=rule.origin,
                created_by=principal.user_id,
            )
        )
        await session.flush()
        await session.refresh(rule)
        return ResolutionRuleView.model_validate(rule, from_attributes=True)
