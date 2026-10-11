"""A2 group "Thu thập" (P14, UC-14-01 to UC-14-06): crawl sources, schema versions, runs, review,
schema drift, consolidation specs and runs, entity links.

The api configures and decides; it never fetches a page. Fetching, extracting, inferring a schema,
testing it and consolidating are the `crawler` deployable's and the agent runtime's work: the
endpoints write the job row (or nothing but the request) with its first status and an event to
acquire.outbox in the same transaction (D1 rule 8), and answer 202 with a Location (A1.6). A source's
credentials stay in OpenBao: the row holds a reference under the caller's organisation, which no
response returns. Data-Space scoping goes through crawl_source.data_space_id, the only acquire
table besides consolidation_spec and entity_link that carries it.
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, Path, Query, Response
from sqlalchemy import ColumnElement, Select, and_, func, not_, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import acquire, catalog, org
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
from services.api.kernel.problems import Problem
from services.api.modules.acquire import rules
from services.api.modules.acquire.schemas import (
    Accepted,
    BulkReview,
    BulkReviewResult,
    ConsolidationRunCreate,
    ConsolidationRunView,
    ConsolidationSpecCreate,
    ConsolidationSpecPatch,
    ConsolidationSpecView,
    CrawlPageView,
    CrawlRunDetail,
    CrawlRunView,
    CrawlSchemaVersionView,
    CrawlSourceCreate,
    CrawlSourceDetail,
    CrawlSourcePatch,
    CrawlSourceSummary,
    CrawlSourceView,
    DriftResolve,
    EntityLinkDecision,
    EntityLinkView,
    ExtractedRecordItem,
    ExtractedRecordView,
    LastFetchError,
    LegalReview,
    PauseRequest,
    RecordReview,
    RecordReviewResult,
    RevertAccepted,
    ReviewDecisionView,
    SchemaApproveRequest,
    SchemaDriftItem,
    SchemaInferenceRequest,
    SchemaRevertRequest,
    SchemaTestRequest,
    SourcePage,
)

router = APIRouter()
ROLE = "svc_acquire"
POLL_AFTER_MS = 2_000
# A run detail lists its pages; past this the screen pages through GET /v1/crawl-runs/{id}/records.
MAX_PAGES_IN_RUN = 1_000

S = acquire.CrawlSource
V = acquire.CrawlSchemaVersion
R = acquire.CrawlRun
P = acquire.CrawlPage
E = acquire.ExtractedRecord
D = acquire.SchemaDrift
C = acquire.ConsolidationSpec
CR = acquire.ConsolidationRun
L = acquire.EntityLink

PathId = Annotated[uuid.UUID, Path(alias="id")]

RECORD_STATUS = {"confirm": "approved", "correct": "corrected", "reject": "rejected"}
# What a consolidation spec computes (BR-156: changing one makes the caller the spec's last editor).
SPEC_CONTENT = frozenset(
    {
        "entity_type",
        "grain",
        "source_ids",
        "normalization",
        "source_priority",
        "aggregates",
        "summary_template",
        "allow_interpolation",
        "target_dataset_id",
    }
)
SPEC_NOT_NULL = frozenset(
    {
        "name",
        "entity_type",
        "grain",
        "source_ids",
        "normalization",
        "source_priority",
        "aggregates",
        "allow_interpolation",
        "owner_id",
        "status",
    }
)
SOURCE_NOT_NULL = frozenset(
    {
        "name",
        "goal",
        "allowed_domains",
        "max_depth",
        "rate_limit_rps",
        "review_gate",
        "trust_level",
        "owner_id",
    }
)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID | decimal.Decimal):
        return str(value)
    if isinstance(value, datetime.datetime | datetime.date):
        return value.isoformat()
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v) for v in value]
    return value


def _emit(
    session: AsyncSession,
    principal: Principal,
    table: str,
    row_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    """One acquire.outbox row in the caller's transaction (D1 rule 8): ids and states only."""
    session.add(
        acquire.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=table,
            aggregate_id=row_id,
            event_type=event_type,
            payload={k: _jsonable(v) for k, v in payload.items()},
        )
    )


def _accepted(response: Response, location: str, row_id: uuid.UUID, status: str) -> Accepted:
    response.headers["Location"] = location
    return Accepted(id=row_id, status=status, poll_after_ms=POLL_AFTER_MS)


def _keyset(
    query: Select[Any],
    page: PageRequest,
    created: Any,
    ident: Any,
    *,
    newest_first: bool = True,
) -> Select[Any]:
    """A1.4 cursor on (created_at, id) of the last row served."""
    if page.cursor:
        try:
            after = (
                datetime.datetime.fromisoformat(page.cursor["created_at"]),
                uuid.UUID(page.cursor["id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        key = tuple_(created, ident)
        query = query.where(key < after if newest_first else key > after)
    order = (created.desc(), ident.desc()) if newest_first else (created.asc(), ident.asc())
    return query.order_by(*order).limit(page.page_size + 1)


def _cut(rows: list[Any], page: PageRequest) -> tuple[list[Any], str]:
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    if not more:
        return rows, ""
    last = rows[-1]
    return rows, encode_page_token({"created_at": last.created_at.isoformat(), "id": str(last.id)})


def _status_changed(
    session: AsyncSession, principal: Principal, source: acquire.CrawlSource, previous: str
) -> None:
    if source.status != previous:
        _emit(
            session,
            principal,
            "crawl_source",
            source.id,
            "acquire.source.status_changed",
            source_id=source.id,
            data_space_id=source.data_space_id,
            previous_status=previous,
            status=source.status,
            legal_status=source.legal_status,
        )


# ---------------------------------------------------------------- lookups (404 = missing or outside)


async def _source(
    session: AsyncSession,
    principal: Principal,
    space: uuid.UUID,
    source_id: uuid.UUID,
    *,
    lock: bool = False,
) -> acquire.CrawlSource:
    query = select(S).where(
        S.id == source_id, S.org_id == principal.org_id, S.data_space_id == space
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Crawl source not found.")
    return row


async def _run(
    session: AsyncSession,
    principal: Principal,
    space: uuid.UUID,
    run_id: uuid.UUID,
    *,
    lock: Literal["update", "share"] | None = None,
) -> acquire.CrawlRun:
    query = (
        select(R)
        .join(S, S.id == R.source_id)
        .where(R.id == run_id, R.org_id == principal.org_id, S.data_space_id == space)
    )
    if lock is not None:
        query = query.with_for_update(of=R, read=lock == "share")
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Crawl run not found.")
    return row


async def _version(
    session: AsyncSession,
    principal: Principal,
    space: uuid.UUID,
    version_id: uuid.UUID,
    *,
    lock: bool = False,
) -> tuple[acquire.CrawlSchemaVersion, acquire.CrawlSource]:
    """The version and its source; with `lock`, the source row first (the parent of the
    "one active version per source" rule), then the version."""
    source_id = await session.scalar(
        select(V.source_id)
        .join(S, S.id == V.source_id)
        .where(V.id == version_id, V.org_id == principal.org_id, S.data_space_id == space)
    )
    if source_id is None:
        raise Problem(404, "not_found", "Schema version not found.")
    source = await _source(session, principal, space, source_id, lock=lock)
    query = select(V).where(V.id == version_id)
    version = await session.scalar(query.with_for_update() if lock else query)
    if version is None:
        raise Problem(404, "not_found", "Schema version not found.")
    return version, source


async def _running_run(session: AsyncSession, source_id: uuid.UUID) -> uuid.UUID | None:
    found: uuid.UUID | None = await session.scalar(
        select(R.id).where(R.source_id == source_id, R.status.in_(rules.RUNNING_RUN)).limit(1)
    )
    return found


async def _open_major_drift(
    session: AsyncSession, source_id: uuid.UUID, *, besides: uuid.UUID | None = None
) -> bool:
    query = select(D.id).where(
        D.source_id == source_id, D.resolution.is_(None), D.severity == "major"
    )
    if besides is not None:
        query = query.where(D.id != besides)
    return await session.scalar(query.limit(1)) is not None


async def _maybe_resume(
    session: AsyncSession, source: acquire.CrawlSource, *, besides_drift: uuid.UUID | None = None
) -> None:
    """After a drift is settled the source runs again (UC-14-03), when nothing else holds it."""
    if (
        source.status in rules.RESUMABLE_SOURCE
        and source.active_schema_version_id is not None
        and rules.legal_in_force(source.legal_status, source.legal_review_due_at, _now())
        and not await _open_major_drift(session, source.id, besides=besides_drift)
    ):
        source.status = "active"


async def _check_active_user(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID, field: str
) -> None:
    found = await session.scalar(
        select(org.AppUser.id).where(
            org.AppUser.id == user_id,
            org.AppUser.org_id == principal.org_id,
            org.AppUser.status == "active",
        )
    )
    if found is None:
        raise rules.invalid(field, f"{field} is not an active user of the organisation.")


# ---------------------------------------------------------------- source views


def _issue() -> ColumnElement[bool]:
    """UC-14-06 step 2 "nguồn có vấn đề": held, blocked, without a valid legal review, an open
    drift, or a last run that failed or was quarantined."""
    last_status = (
        select(R.status)
        .where(R.source_id == S.id)
        .order_by(R.created_at.desc(), R.id.desc())
        .limit(1)
        .correlate(S)
        .scalar_subquery()
    )
    open_drift = select(D.id).where(D.source_id == S.id, D.resolution.is_(None)).exists()
    return or_(
        S.status.in_(("paused", "quarantined")),
        S.consecutive_blocked > 0,
        S.legal_status.in_(("rejected", "expired")),
        and_(S.legal_status == "approved", S.legal_review_due_at <= func.now()),
        open_drift.correlate(S),
        func.coalesce(last_status, "").in_(("failed", "quarantined")),
    )


def _source_view(row: acquire.CrawlSource) -> CrawlSourceView:
    view = CrawlSourceView.model_validate(row)
    view.has_secret = row.secret_ref is not None
    return view


async def _summaries(
    session: AsyncSession, rows: list[acquire.CrawlSource]
) -> list[CrawlSourceSummary]:
    """The monitoring columns of SCR-56, five grouped queries for the whole page."""
    ids = [r.id for r in rows]
    if not ids:
        return []
    moment = func.coalesce(R.finished_at, R.started_at, R.created_at)
    last = {
        r.source_id: r
        for r in (
            await session.execute(
                select(R.source_id, R.status, R.cost)
                .where(R.source_id.in_(ids))
                .distinct(R.source_id)
                .order_by(R.source_id, R.created_at.desc(), R.id.desc())
            )
        ).all()
    }
    published = dict(
        (
            await session.execute(
                select(R.source_id, func.max(moment))
                .where(R.source_id.in_(ids), R.status == "published")
                .group_by(R.source_id)
            )
        )
        .tuples()
        .all()
    )
    accuracy = dict(
        (
            await session.execute(
                select(R.source_id, R.sample_accuracy)
                .where(R.source_id.in_(ids), R.sample_accuracy.is_not(None))
                .distinct(R.source_id)
                .order_by(R.source_id, moment.desc())
            )
        )
        .tuples()
        .all()
    )
    pending = dict(
        (
            await session.execute(
                select(R.source_id, func.count())
                .select_from(E)
                .join(R, R.id == E.run_id)
                .where(
                    R.source_id.in_(ids),
                    E.status.in_(("pending", "flagged")),
                    R.status.not_in(rules.CLOSED_RUN),
                )
                .group_by(R.source_id)
            )
        )
        .tuples()
        .all()
    )
    drifts = dict(
        (
            await session.execute(
                select(D.source_id, func.count())
                .where(D.source_id.in_(ids), D.resolution.is_(None))
                .group_by(D.source_id)
            )
        )
        .tuples()
        .all()
    )
    errors = {
        r.source_id: r
        for r in (
            await session.execute(
                select(P.source_id, P.url, P.http_status, P.fetched_at)
                .where(P.source_id.in_(ids), or_(P.http_status >= 400, P.object_key.is_(None)))
                .distinct(P.source_id)
                .order_by(P.source_id, P.fetched_at.desc())
            )
        ).all()
    }
    now = _now()
    out = []
    for row in rows:
        view = CrawlSourceSummary(**_source_view(row).model_dump())
        if row.id in last:
            view.last_run_status = last[row.id].status
            view.last_run_cost = last[row.id].cost
        view.last_published_at = published.get(row.id)
        if view.last_published_at is not None:
            view.freshness_seconds = int((now - view.last_published_at).total_seconds())
        view.sample_accuracy = accuracy.get(row.id)
        view.pending_review = int(pending.get(row.id, 0))
        view.open_drifts = int(drifts.get(row.id, 0))
        if row.id in errors:
            err = errors[row.id]
            view.last_fetch_error = LastFetchError(
                url=err.url, http_status=err.http_status, fetched_at=err.fetched_at
            )
        view.has_issue = (
            row.status in ("paused", "quarantined")
            or row.consecutive_blocked > 0
            or row.legal_status in ("rejected", "expired")
            or (
                row.legal_status == "approved"
                and row.legal_review_due_at is not None
                and row.legal_review_due_at <= now
            )
            or view.open_drifts > 0
            or view.last_run_status in ("failed", "quarantined")
        )
        out.append(view)
    return out


# ---------------------------------------------------------------- crawl sources (UC-14-01, UC-14-06)


@router.get(
    "/v1/crawl-sources",
    response_model=Page[CrawlSourceSummary],
    **endpoint("GET", "/v1/crawl-sources", operation_id="listCrawlSources"),
)
async def list_crawl_sources(
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    status: Literal["draft", "active", "paused", "quarantined", "retired"] | None = None,
    kind: Literal["web_page", "web_listing", "public_api", "feed", "file_drop"] | None = None,
    owner_id: uuid.UUID | None = None,
    has_issue: bool | None = None,
) -> Page[CrawlSourceSummary]:
    query = select(S).where(S.org_id == principal.org_id, S.data_space_id == space)
    if status:
        query = query.where(S.status == status)
    if kind:
        query = query.where(S.kind == kind)
    if owner_id:
        query = query.where(S.owner_id == owner_id)
    if has_issue is not None:
        query = query.where(_issue() if has_issue else not_(_issue()))
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(_keyset(query, page, S.created_at, S.id))).all())
        rows, token = _cut(rows, page)
        items = await _summaries(session, rows)
    return Page(items=items, next_page_token=token)


@router.post(
    "/v1/crawl-sources",
    status_code=201,
    response_model=CrawlSourceView,
    **endpoint("POST", "/v1/crawl-sources", operation_id="createCrawlSource"),
)
async def create_crawl_source(
    body: CrawlSourceCreate, principal: CurrentPrincipal, space: CurrentDataSpace
) -> CrawlSourceView:
    rules.check_urls_in_scope("seed_urls", body.seed_urls, body.allowed_domains)
    for field in ("openapi_url", "terms_url"):
        value = getattr(body, field)
        if value is not None:
            rules.check_urls_in_scope(field, [value], body.allowed_domains)
    secret_ref = (
        rules.check_secret_ref(body.secret_ref, principal.org_id) if body.secret_ref else None
    )
    async with transaction(ROLE, principal) as session:
        row = acquire.CrawlSource(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=space,
            name=body.name.strip(),
            goal=body.goal.strip(),
            kind=body.kind,
            seed_urls=body.seed_urls,
            allowed_domains=body.allowed_domains,
            max_depth=body.max_depth,
            schedule_cron=body.schedule_cron,
            rate_limit_rps=body.rate_limit_rps,
            auth_mode=body.auth_mode,
            secret_ref=secret_ref,
            openapi_url=body.openapi_url,
            terms_url=body.terms_url,
            review_gate=body.review_gate,
            trust_level=body.trust_level,
            owner_id=principal.user_id,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        # The crawler checks robots.txt and the terms and fetches one page through the egress
        # proxy (UC-14-01 step 4); with openapi_url the agent reads the schema from it (alt. 1).
        _emit(
            session,
            principal,
            "crawl_source",
            row.id,
            "acquire.source.created",
            source_id=row.id,
            data_space_id=space,
            kind=row.kind,
            status=row.status,
            owner_id=row.owner_id,
            has_openapi=row.openapi_url is not None,
        )
        return _source_view(row)


@router.get(
    "/v1/crawl-sources/{id}",
    response_model=CrawlSourceDetail,
    **endpoint("GET", "/v1/crawl-sources/{id}", operation_id="getCrawlSource"),
)
async def get_crawl_source(
    source_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> CrawlSourceDetail:
    async with transaction(ROLE, principal) as session:
        row = await _source(session, principal, space, source_id)
        (summary,) = await _summaries(session, [row])
        active = (
            await session.scalar(select(V).where(V.id == row.active_schema_version_id))
            if row.active_schema_version_id
            else None
        )
        runs = (
            await session.scalars(
                select(R)
                .where(R.source_id == row.id)
                .order_by(R.created_at.desc(), R.id.desc())
                .limit(5)
            )
        ).all()
    return CrawlSourceDetail(
        **summary.model_dump(),
        active_schema=CrawlSchemaVersionView.model_validate(active) if active else None,
        recent_runs=[CrawlRunView.model_validate(r) for r in runs],
    )


@router.patch(
    "/v1/crawl-sources/{id}",
    response_model=CrawlSourceView,
    **endpoint("PATCH", "/v1/crawl-sources/{id}", operation_id="patchCrawlSource"),
)
async def patch_crawl_source(
    body: CrawlSourcePatch,
    source_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> CrawlSourceView:
    changes = body.model_dump(exclude_unset=True)
    for field in SOURCE_NOT_NULL & changes.keys():
        if changes[field] is None:
            raise rules.invalid(field, f"{field} cannot be null.")
    async with transaction(ROLE, principal) as session:
        row = await _source(session, principal, space, source_id, lock=True)
        if row.status == "retired":
            raise Problem(409, "invalid_state", "A retired source cannot be edited.")
        if "allowed_domains" in changes:
            # Narrowing the domains must keep every URL the source already fetches in scope, as
            # create checks them: the seeds, and the OpenAPI and terms pages (UC-14-01, BR-174).
            domains = changes["allowed_domains"]
            rules.check_urls_in_scope("allowed_domains", row.seed_urls, domains)
            for field in ("openapi_url", "terms_url"):
                value = getattr(row, field)
                if value is not None:
                    rules.check_urls_in_scope("allowed_domains", [value], domains)
        if "owner_id" in changes and changes["owner_id"] != row.owner_id:
            # Handing a source to someone else is the owner's call, or a data admin's; a steward
            # of the space cannot take over or reassign another person's source.
            if principal.user_id != row.owner_id and "admin_data" not in principal.capabilities:
                raise Problem(
                    403, "forbidden", "Only the owner or a data admin can change owner_id."
                )
            await _check_active_user(session, principal, changes["owner_id"], "owner_id")
        if "name" in changes:
            changes["name"] = changes["name"].strip()
        previous = row.status
        for key, value in changes.items():
            setattr(row, key, value)
        row.updated_by, row.updated_at = principal.user_id, _now()
        # The review-gate and legal triggers act here (UC-14-04 auto gate, BR-174).
        await session.flush()
        await session.refresh(row)
        _emit(
            session,
            principal,
            "crawl_source",
            row.id,
            "acquire.source.updated",
            source_id=row.id,
            data_space_id=row.data_space_id,
            changed=sorted(changes),
            status=row.status,
            owner_id=row.owner_id,
        )
        _status_changed(session, principal, row, previous)
        return _source_view(row)


@router.post(
    "/v1/crawl-sources/{id}:legal-review",
    response_model=CrawlSourceView,
    **endpoint(
        "POST", "/v1/crawl-sources/{id}:legal-review", operation_id="legalReviewCrawlSource"
    ),
)
async def legal_review(
    body: LegalReview,
    source_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> CrawlSourceView:
    now = _now()
    due = (
        rules.legal_review_due(now, body.legal_review_due_at)
        if body.legal_status == "approved"
        else None
    )
    async with transaction(ROLE, principal) as session:
        row = await _source(session, principal, space, source_id, lock=True)
        if row.status == "retired":
            raise Problem(409, "invalid_state", "A retired source is not reviewed.")
        if row.owner_id == principal.user_id:
            raise Problem(
                403, "policy_denied", "The source's owner cannot review it legally (BR-174)."
            )
        rules.check_urls_in_scope("terms_url", [body.terms_url], row.allowed_domains)
        previous = row.status
        row.terms_url = body.terms_url
        row.legal_status = body.legal_status
        row.legal_checklist = body.legal_checklist.model_dump(mode="json")
        row.legal_evidence = [e.model_dump(mode="json") for e in body.legal_evidence]
        row.legal_reviewed_by, row.legal_reviewed_at = principal.user_id, now
        row.legal_review_due_at = due
        if body.legal_status == "approved":
            # The capture of the terms page just taken is what later daily checks compare with:
            # a different hash voids the approval (BR-174, trigger crawl_source_legal_guard).
            terms = max(
                (e for e in body.legal_evidence if e.kind == "terms"), key=lambda e: e.captured_at
            )
            row.terms_sha256 = row.terms_observed_sha256 = terms.sha256
            row.terms_checked_at = terms.captured_at
        if body.note and body.note.strip():
            row.terms_note = body.note.strip()
        row.updated_by, row.updated_at = principal.user_id, now
        await session.flush()
        await session.refresh(row)
        # Inbox closes the legal_review request on this event.
        _emit(
            session,
            principal,
            "crawl_source",
            row.id,
            "acquire.source.legal_reviewed",
            source_id=row.id,
            data_space_id=row.data_space_id,
            legal_status=row.legal_status,
            legal_reviewed_by=principal.user_id,
            legal_review_due_at=row.legal_review_due_at,
        )
        _status_changed(session, principal, row, previous)
        return _source_view(row)


@router.post(
    "/v1/crawl-sources/{id}:pause",
    response_model=CrawlSourceView,
    **endpoint("POST", "/v1/crawl-sources/{id}:pause", operation_id="pauseCrawlSource"),
)
async def pause_crawl_source(
    body: PauseRequest,
    source_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> CrawlSourceView:
    async with transaction(ROLE, principal) as session:
        row = await _source(session, principal, space, source_id, lock=True)
        rules.source_transition(row.status, rules.PAUSABLE_SOURCE, "be paused")
        previous = row.status
        row.status = "paused"
        row.updated_by, row.updated_at = principal.user_id, _now()
        await session.flush()
        await session.refresh(row)
        _status_changed(session, principal, row, previous)
        return _source_view(row)


@router.post(
    "/v1/crawl-sources/{id}:resume",
    response_model=CrawlSourceView,
    **endpoint("POST", "/v1/crawl-sources/{id}:resume", operation_id="resumeCrawlSource"),
)
async def resume_crawl_source(
    source_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> CrawlSourceView:
    async with transaction(ROLE, principal) as session:
        row = await _source(session, principal, space, source_id, lock=True)
        rules.source_transition(row.status, rules.RESUMABLE_SOURCE, "be resumed")
        if row.active_schema_version_id is None:
            raise Problem(409, "invalid_state", "The source has no active schema (BR-152).")
        if await _open_major_drift(session, row.id):
            raise Problem(
                409, "invalid_state", "A major schema drift of the source is still open (BR-152)."
            )
        if not rules.legal_in_force(row.legal_status, row.legal_review_due_at, _now()):
            raise Problem(
                422,
                "business_rule_violated",
                "The source has no legal review in force (BR-174).",
            )
        previous = row.status
        row.status = "active"
        # A person resumed it: the count of blocked runs that paused it starts over (UC-14-06 E1).
        row.consecutive_blocked = 0
        row.updated_by, row.updated_at = principal.user_id, _now()
        await session.flush()
        await session.refresh(row)
        _status_changed(session, principal, row, previous)
        return _source_view(row)


@router.post(
    "/v1/crawl-sources/{id}/runs",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/crawl-sources/{id}/runs", operation_id="createCrawlRun"),
)
async def create_crawl_run(
    source_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    response: Response,
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        source = await _source(session, principal, space, source_id, lock=True)
        if source.status != "active":
            raise Problem(409, "invalid_state", f"The source is {source.status}, not active.")
        if await _running_run(session, source.id):
            raise Problem(409, "invalid_state", "The source already has a run in progress.")
        if not rules.legal_in_force(source.legal_status, source.legal_review_due_at, _now()):
            raise Problem(
                422,
                "business_rule_violated",
                "The source's legal review is not in force (BR-174).",
            )
        run = acquire.CrawlRun(
            id=uuid7(),
            org_id=principal.org_id,
            source_id=source.id,
            schema_version_id=source.active_schema_version_id,
            trigger="manual",
        )
        session.add(run)
        await session.flush()
        await session.refresh(run)
        # The crawler fetches, stores originals in da-raw-crawl with SHA-256 (BR-150) and emits
        # acquire.run.completed when the run is extracted.
        _emit(
            session,
            principal,
            "crawl_run",
            run.id,
            "acquire.run.queued",
            run_id=run.id,
            source_id=source.id,
            data_space_id=space,
            trigger=run.trigger,
            schema_version_id=run.schema_version_id,
            status=run.status,
        )
        return _accepted(response, f"/v1/crawl-runs/{run.id}", run.id, run.status)


# ---------------------------------------------------------------- runs and pages (UC-14-06)


@router.get(
    "/v1/crawl-runs",
    response_model=Page[CrawlRunView],
    **endpoint("GET", "/v1/crawl-runs", operation_id="listCrawlRuns"),
)
async def list_crawl_runs(
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    source_id: uuid.UUID | None = None,
    status: Literal[
        "queued", "fetching", "extracting", "in_review", "published", "quarantined", "failed"
    ]
    | None = None,
    from_: Annotated[datetime.datetime | None, Query(alias="from")] = None,
    to: datetime.datetime | None = None,
) -> Page[CrawlRunView]:
    query = (
        select(R)
        .join(S, S.id == R.source_id)
        .where(R.org_id == principal.org_id, S.data_space_id == space)
    )
    if source_id:
        query = query.where(R.source_id == source_id)
    if status:
        query = query.where(R.status == status)
    if from_:
        query = query.where(R.created_at >= from_)
    if to:
        query = query.where(R.created_at < to)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(_keyset(query, page, R.created_at, R.id))).all())
    rows, token = _cut(rows, page)
    return Page(items=[CrawlRunView.model_validate(r) for r in rows], next_page_token=token)


@router.get(
    "/v1/crawl-runs/{id}",
    response_model=CrawlRunDetail,
    **endpoint("GET", "/v1/crawl-runs/{id}", operation_id="getCrawlRun"),
)
async def get_crawl_run(
    run_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> CrawlRunDetail:
    async with transaction(ROLE, principal) as session:
        run = await _run(session, principal, space, run_id)
        pages = list(
            (
                await session.scalars(
                    select(P)
                    .where(P.run_id == run.id)
                    .order_by(P.fetched_at, P.id)
                    .limit(MAX_PAGES_IN_RUN + 1)
                )
            ).all()
        )
    return CrawlRunDetail(
        **CrawlRunView.model_validate(run).model_dump(),
        pages=[CrawlPageView.model_validate(p) for p in pages[:MAX_PAGES_IN_RUN]],
        pages_truncated=len(pages) > MAX_PAGES_IN_RUN,
    )


# ---------------------------------------------------------------- schema versions (UC-14-02, -03)


@router.post(
    "/v1/crawl-sources/{id}/schema-inference",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/crawl-sources/{id}/schema-inference", operation_id="inferCrawlSchema"),
)
async def infer_schema(
    source_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    response: Response,
    # Both fields are optional, so is the body.
    body: Annotated[SchemaInferenceRequest | None, Body()] = None,
) -> Accepted:
    request = body or SchemaInferenceRequest()
    async with transaction(ROLE, principal) as session:
        source = await _source(session, principal, space, source_id, lock=True)
        if source.status == "retired":
            raise Problem(409, "invalid_state", "A retired source is not inferred.")
        if request.sample_urls:
            rules.check_urls_in_scope("sample_urls", request.sample_urls, source.allowed_domains)
        if not rules.legal_in_force(source.legal_status, source.legal_review_due_at, _now()):
            # The agent stores at least five originals; no page is stored without it (BR-174).
            raise Problem(
                422,
                "business_rule_violated",
                "The source needs a legal review in force before pages are fetched (BR-174).",
            )
        # The agent runtime opens the Agent Thu thập run with this id; it records the draft
        # version with inferred_by_run_id = this id (UC-14-02 steps 1-3).
        agent_run_id = uuid7()
        _emit(
            session,
            principal,
            "crawl_source",
            source.id,
            "acquire.schema.inference_requested",
            source_id=source.id,
            data_space_id=space,
            agent_run_id=agent_run_id,
            kind=source.kind,
            sample_urls=request.sample_urls or [],
            # The extraction hint is the operator's instruction to the agent, which has nowhere
            # else to read it (no column holds it).
            hints=request.hints,
            has_openapi=source.openapi_url is not None,
        )
        return _accepted(response, f"/v1/runs/{agent_run_id}", agent_run_id, "queued")


@router.get(
    "/v1/crawl-sources/{id}/schemas",
    response_model=Page[CrawlSchemaVersionView],
    **endpoint("GET", "/v1/crawl-sources/{id}/schemas", operation_id="listCrawlSchemas"),
)
async def list_crawl_schemas(
    source_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    status: Literal["draft", "testing", "active", "superseded", "rejected"] | None = None,
) -> Page[CrawlSchemaVersionView]:
    query = select(V).where(V.source_id == source_id)
    if status:
        query = query.where(V.status == status)
    async with transaction(ROLE, principal) as session:
        await _source(session, principal, space, source_id)
        rows = list((await session.scalars(_keyset(query, page, V.created_at, V.id))).all())
    rows, token = _cut(rows, page)
    return Page(
        items=[CrawlSchemaVersionView.model_validate(r) for r in rows], next_page_token=token
    )


@router.post(
    "/v1/crawl-schemas/{id}:test",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/crawl-schemas/{id}:test", operation_id="testCrawlSchema"),
)
async def test_schema(
    version_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    response: Response,
    body: Annotated[SchemaTestRequest | None, Body()] = None,
) -> Accepted:
    request = body or SchemaTestRequest()
    async with transaction(ROLE, principal) as session:
        version, source = await _version(session, principal, space, version_id, lock=True)
        if version.status not in rules.TESTABLE_SCHEMA:
            raise Problem(
                409, "invalid_state", f"A {version.status} schema version is not tested again."
            )
        if request.sample_page_ids is not None:
            wanted = set(request.sample_page_ids)
            found = set(
                (
                    await session.scalars(
                        select(P.id).where(P.id.in_(wanted), P.source_id == source.id)
                    )
                ).all()
            )
            if found != wanted:
                raise rules.invalid(
                    "sample_page_ids", "sample_page_ids are not all pages of this source."
                )
            version.sample_page_ids = list(request.sample_page_ids)
        # A new test: the previous result no longer says anything about this version (BR-151).
        version.status = "testing"
        version.test_required_accuracy = None
        await session.flush()
        agent_run_id = uuid7()
        _emit(
            session,
            principal,
            "crawl_schema_version",
            version.id,
            "acquire.schema.test_requested",
            schema_version_id=version.id,
            source_id=source.id,
            data_space_id=space,
            agent_run_id=agent_run_id,
            sample_page_ids=version.sample_page_ids,
            status=version.status,
        )
        return _accepted(response, f"/v1/runs/{agent_run_id}", agent_run_id, "queued")


@router.post(
    "/v1/crawl-schemas/{id}:approve",
    response_model=CrawlSchemaVersionView,
    **endpoint("POST", "/v1/crawl-schemas/{id}:approve", operation_id="approveCrawlSchema"),
)
async def approve_schema(
    version_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    body: Annotated[SchemaApproveRequest | None, Body()] = None,
) -> CrawlSchemaVersionView:
    now = _now()
    async with transaction(ROLE, principal) as session:
        version, source = await _version(session, principal, space, version_id, lock=True)
        if version.status not in rules.TESTABLE_SCHEMA:
            raise Problem(409, "invalid_state", f"The schema version is {version.status}.")
        if version.test_required_accuracy is None:
            raise Problem(409, "invalid_state", "The schema version has not been tested yet.")
        if version.test_required_accuracy < decimal.Decimal(str(rules.MIN_TEST_ACCURACY)):
            raise Problem(
                422,
                "business_rule_violated",
                f"{version.test_required_accuracy:.2%} of required fields extracted right; "
                "95% is needed (BR-151).",
            )
        if source.owner_id != principal.user_id:
            raise Problem(403, "forbidden", "The source's owner approves its schema (BR-151).")
        superseded = None
        if source.active_schema_version_id is not None:
            superseded = await session.scalar(
                select(V).where(V.id == source.active_schema_version_id).with_for_update()
            )
        if superseded is not None:
            superseded.status = "superseded"
            # One active version per source (unique index): the old one steps down first.
            await session.flush()
        version.status = "active"
        version.approved_by, version.approved_at = principal.user_id, now
        await session.flush()
        previous = source.status
        source.active_schema_version_id = version.id
        if source.status == "draft" and rules.legal_in_force(
            source.legal_status, source.legal_review_due_at, now
        ):
            # BR-151 "nguồn active"; a source without its legal review stays draft (BR-174).
            source.status = "active"
        source.updated_by, source.updated_at = principal.user_id, now
        await session.flush()
        await session.refresh(version)
        await session.refresh(source)
        _emit(
            session,
            principal,
            "crawl_schema_version",
            version.id,
            "acquire.schema.approved",
            schema_version_id=version.id,
            source_id=source.id,
            data_space_id=space,
            version=version.version,
            status=version.status,
            superseded_version_id=superseded.id if superseded else None,
            has_note=bool(body and body.note),
        )
        _status_changed(session, principal, source, previous)
        return CrawlSchemaVersionView.model_validate(version)


@router.post(
    "/v1/crawl-schemas/{id}:revert",
    status_code=202,
    response_model=RevertAccepted,
    **endpoint("POST", "/v1/crawl-schemas/{id}:revert", operation_id="revertCrawlSchema"),
)
async def revert_schema(
    body: SchemaRevertRequest,
    version_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    response: Response,
) -> RevertAccepted:
    now = _now()
    async with transaction(ROLE, principal) as session:
        faulty, source = await _version(session, principal, space, version_id, lock=True)
        if faulty.status != "active":
            raise Problem(
                409, "invalid_state", "Only the source's active schema version is reverted."
            )
        restore = await session.scalar(
            select(V)
            .where(V.source_id == source.id, V.status == "superseded", V.id != faulty.id)
            .order_by(V.approved_at.desc().nulls_last(), V.version.desc())
            .limit(1)
            .with_for_update()
        )
        if restore is None:
            raise Problem(
                409, "invalid_state", "There is no earlier approved schema version to return to."
            )
        if await _running_run(session, source.id):
            raise Problem(409, "invalid_state", "The source has a run in progress.")
        live = and_(E.schema_version_id == faulty.id, E.status != "superseded")
        affected = (
            await session.execute(
                select(R.id, R.status).where(R.id.in_(select(E.run_id).where(live))).order_by(R.id)
            )
        ).all()
        expired = list(
            (
                await session.scalars(
                    select(P.id)
                    .where(
                        P.id.in_(select(E.page_id).where(live)),
                        or_(P.retain_until < now, P.object_key.is_(None)),
                    )
                    .order_by(P.id)
                )
            ).all()
        )
        superseded = (
            await session.execute(
                update(E)
                .where(live)
                .values(status="superseded", updated_at=now, updated_by=principal.user_id)
                .returning(E.id)
            )
        ).all()
        # Rejected, not superseded: superseded means "approved, then replaced", which is what a
        # later revert would return to (BR-152).
        faulty.status = "rejected"
        await session.flush()
        restore.status = "active"
        await session.flush()
        previous = source.status
        source.active_schema_version_id = restore.id
        closed = list(
            (
                await session.scalars(
                    update(D)
                    .where(
                        D.source_id == source.id,
                        D.from_version_id == faulty.id,
                        D.resolution.is_(None),
                    )
                    .values(resolution="reverted", resolved_by=principal.user_id, resolved_at=now)
                    .returning(D.id)
                )
            ).all()
        )
        await _maybe_resume(session, source)
        source.updated_by, source.updated_at = principal.user_id, now
        run = acquire.CrawlRun(
            id=uuid7(),
            org_id=principal.org_id,
            source_id=source.id,
            schema_version_id=restore.id,
            # A re-extraction from the stored originals, not a new fetch (UC-14-03 step 5).
            trigger="retry",
        )
        session.add(run)
        await session.flush()
        await session.refresh(run)
        await session.refresh(source)
        affected_ids = [r.id for r in affected]
        _emit(
            session,
            principal,
            "crawl_schema_version",
            faulty.id,
            "acquire.schema.reverted",
            schema_version_id=faulty.id,
            restored_version_id=restore.id,
            source_id=source.id,
            data_space_id=space,
            run_id=run.id,
            superseded_records=len(superseded),
            affected_run_ids=affected_ids,
            # Published with the faulty version: consolidation publishes a new dataset version and
            # the answers that used the numbers are labelled (UC-14-03 alt. 2).
            republish_run_ids=[r.id for r in affected if r.status == "published"],
            expired_page_ids=expired,
        )
        _emit(
            session,
            principal,
            "crawl_run",
            run.id,
            "acquire.run.queued",
            run_id=run.id,
            source_id=source.id,
            data_space_id=space,
            trigger=run.trigger,
            schema_version_id=restore.id,
            status=run.status,
            reextract_run_ids=affected_ids,
        )
        for drift_id in closed:
            _emit(
                session,
                principal,
                "schema_drift",
                drift_id,
                "acquire.schema.drift_resolved",
                drift_id=drift_id,
                source_id=source.id,
                data_space_id=space,
                resolution="reverted",
                resolved_by=principal.user_id,
            )
        _status_changed(session, principal, source, previous)
        response.headers["Location"] = f"/v1/crawl-runs/{run.id}"
        return RevertAccepted(
            id=run.id,
            status=run.status,
            poll_after_ms=POLL_AFTER_MS,
            restored_version_id=restore.id,
            superseded_records=len(superseded),
            expired_page_ids=expired,
        )


# ---------------------------------------------------------------- schema drift (UC-14-03)


@router.get(
    "/v1/schema-drifts",
    response_model=Page[SchemaDriftItem],
    **endpoint("GET", "/v1/schema-drifts", operation_id="listSchemaDrifts"),
)
async def list_schema_drifts(
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    source_id: uuid.UUID | None = None,
    drift_kind: Literal["structural", "semantic"] | None = None,
    severity: Literal["minor", "major"] | None = None,
    resolution: Annotated[
        Literal["open", "approved_new", "manual_fix", "reverted", "false_alarm"] | None,
        Query(description="'open' lists the drifts nobody has resolved yet"),
    ] = None,
) -> Page[SchemaDriftItem]:
    query = (
        select(D)
        .join(S, S.id == D.source_id)
        .where(D.org_id == principal.org_id, S.data_space_id == space)
    )
    if source_id:
        query = query.where(D.source_id == source_id)
    if drift_kind:
        query = query.where(D.drift_kind == drift_kind)
    if severity:
        query = query.where(D.severity == severity)
    if resolution == "open":
        query = query.where(D.resolution.is_(None))
    elif resolution:
        query = query.where(D.resolution == resolution)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(_keyset(query, page, D.created_at, D.id))).all())
        rows, token = _cut(rows, page)
        proposed_ids = {r.proposed_version_id for r in rows if r.proposed_version_id}
        proposed = {
            v.id: v
            for v in (
                await session.scalars(select(V).where(V.id.in_(proposed_ids)))
                if proposed_ids
                else []
            )
        }
    items = []
    for row in rows:
        item = SchemaDriftItem.model_validate(row)
        version = proposed.get(row.proposed_version_id) if row.proposed_version_id else None
        item.proposed_version = CrawlSchemaVersionView.model_validate(version) if version else None
        items.append(item)
    return Page(items=items, next_page_token=token)


async def _release_quarantined(
    session: AsyncSession,
    principal: Principal,
    source: acquire.CrawlSource,
    drift_id: uuid.UUID,
) -> list[uuid.UUID]:
    """false_alarm (BR-168): the quarantined runs are published, or go back to review when the
    queue, the schema or the random sample still holds them (BR-152, BR-153, UC-14-04 E2)."""
    runs = list(
        (
            await session.scalars(
                select(R)
                .where(R.source_id == source.id, R.status == "quarantined")
                .order_by(R.created_at)
                .with_for_update()
            )
        ).all()
    )
    other_semantic = await session.scalar(
        select(D.id)
        .where(
            D.source_id == source.id,
            D.drift_kind == "semantic",
            D.resolution.is_(None),
            D.id != drift_id,
        )
        .limit(1)
    )
    for run in runs:
        blocking = await session.scalar(
            select(E.id).where(E.run_id == run.id, E.status.in_(("pending", "flagged"))).limit(1)
        )
        approved = await session.scalar(
            select(V.id).where(V.id == run.schema_version_id, V.status.in_(rules.APPROVED_SCHEMA))
        )
        accurate = run.sample_accuracy is None or run.sample_accuracy >= decimal.Decimal(
            str(rules.MIN_SAMPLE_ACCURACY)
        )
        publish = blocking is None and approved is not None and other_semantic is None and accurate
        run.status = "published" if publish else "in_review"
        await session.flush()
        _emit(
            session,
            principal,
            "crawl_run",
            run.id,
            "acquire.run.published" if publish else "acquire.run.status_changed",
            run_id=run.id,
            source_id=source.id,
            data_space_id=source.data_space_id,
            schema_version_id=run.schema_version_id,
            previous_status="quarantined",
            status=run.status,
        )
    return [r.id for r in runs]


@router.post(
    "/v1/schema-drifts/{id}:resolve",
    response_model=SchemaDriftItem,
    **endpoint("POST", "/v1/schema-drifts/{id}:resolve", operation_id="resolveSchemaDrift"),
)
async def resolve_schema_drift(
    body: DriftResolve,
    drift_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> SchemaDriftItem:
    now = _now()
    async with transaction(ROLE, principal) as session:
        source_id = await session.scalar(
            select(D.source_id)
            .join(S, S.id == D.source_id)
            .where(D.id == drift_id, D.org_id == principal.org_id, S.data_space_id == space)
        )
        if source_id is None:
            raise Problem(404, "not_found", "Schema drift not found.")
        source = await _source(session, principal, space, source_id, lock=True)
        drift = await session.scalar(select(D).where(D.id == drift_id).with_for_update())
        if drift is None:
            raise Problem(404, "not_found", "Schema drift not found.")
        if drift.resolution is not None:
            raise Problem(409, "invalid_state", f"The drift is already {drift.resolution}.")
        if body.resolution == "false_alarm" and drift.drift_kind != "semantic":
            raise Problem(
                422,
                "business_rule_violated",
                "false_alarm closes a semantic drift only; a structural change is real (BR-168).",
            )
        version_id = body.proposed_version_id or drift.proposed_version_id
        version = None
        if version_id is not None:
            version = await session.scalar(
                select(V).where(V.id == version_id, V.source_id == source.id)
            )
            if version is None:
                raise rules.invalid(
                    "proposed_version_id", "proposed_version_id is not a version of this source."
                )
        if body.resolution == "approved_new" and (version is None or version.status != "active"):
            raise Problem(
                422,
                "business_rule_violated",
                "approved_new needs the new schema version approved first (BR-152).",
            )
        if (
            body.resolution == "reverted"
            and source.active_schema_version_id == drift.from_version_id
        ):
            raise Problem(
                409,
                "invalid_state",
                "The drifted version is still active; revert it with :revert first.",
            )
        drift.resolution = body.resolution
        drift.resolved_by, drift.resolved_at = principal.user_id, now
        if body.proposed_version_id is not None:
            drift.proposed_version_id = body.proposed_version_id
        await session.flush()
        quarantined: list[uuid.UUID] = []
        if drift.drift_kind == "semantic":
            if body.resolution == "false_alarm":
                quarantined = await _release_quarantined(session, principal, source, drift.id)
            else:
                quarantined = list(
                    (
                        await session.scalars(
                            select(R.id).where(R.source_id == source.id, R.status == "quarantined")
                        )
                    ).all()
                )
        previous = source.status
        await _maybe_resume(session, source, besides_drift=drift.id)
        source.updated_by, source.updated_at = principal.user_id, now
        await session.flush()
        await session.refresh(source)
        await session.refresh(drift)
        # Inbox closes the schema_drift request; with approved_new or manual_fix on a semantic
        # drift the crawler re-extracts the quarantined runs with the new version (BR-168).
        _emit(
            session,
            principal,
            "schema_drift",
            drift.id,
            "acquire.schema.drift_resolved",
            drift_id=drift.id,
            source_id=source.id,
            data_space_id=space,
            drift_kind=drift.drift_kind,
            resolution=drift.resolution,
            proposed_version_id=drift.proposed_version_id,
            quarantined_run_ids=quarantined,
            reextract=drift.drift_kind == "semantic" and body.resolution != "false_alarm",
            resolved_by=principal.user_id,
        )
        _status_changed(session, principal, source, previous)
        item = SchemaDriftItem.model_validate(drift)
        item.proposed_version = CrawlSchemaVersionView.model_validate(version) if version else None
        return item


# ---------------------------------------------------------------- review (UC-14-04)


def _review_reasons(values: list[str] | None) -> list[str]:
    return [v.strip() for value in values or [] for v in value.split(",") if v.strip()]


@router.get(
    "/v1/crawl-runs/{id}/records",
    response_model=Page[ExtractedRecordItem],
    **endpoint("GET", "/v1/crawl-runs/{id}/records", operation_id="listCrawlRunRecords"),
)
async def list_run_records(
    run_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    status: Literal["pending", "flagged", "approved", "corrected", "rejected", "superseded"]
    | None = None,
    review_reasons: Annotated[
        list[str] | None,
        Query(description="Any of these reasons; repeat the parameter or separate with commas"),
    ] = None,
    is_random_sample: bool | None = None,
) -> Page[ExtractedRecordItem]:
    reasons = _review_reasons(review_reasons)
    async with transaction(ROLE, principal) as session:
        run = await _run(session, principal, space, run_id)
        query = select(E, P).join(P, P.id == E.page_id).where(E.run_id == run.id)
        if status:
            query = query.where(E.status == status)
        if reasons:
            query = query.where(E.review_reasons.overlap(reasons))
        if is_random_sample is not None:
            query = query.where(E.is_random_sample.is_(is_random_sample))
        rows = list(
            (await session.execute(_keyset(query, page, E.created_at, E.id, newest_first=False)))
            .tuples()
            .all()
        )
        version_ids = {record.schema_version_id for record, _ in rows}
        fields = dict(
            (await session.execute(select(V.id, V.fields).where(V.id.in_(version_ids))))
            .tuples()
            .all()
            if version_ids
            else []
        )
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    token = (
        encode_page_token(
            {"created_at": rows[-1][0].created_at.isoformat(), "id": str(rows[-1][0].id)}
        )
        if more
        else ""
    )
    items = []
    for record, source_page in rows:
        item = ExtractedRecordItem.model_validate(record)
        item.source_page = SourcePage(
            page_id=source_page.id,
            url=source_page.url,
            object_key=source_page.object_key,
            content_type=source_page.content_type,
            sha256=source_page.sha256,
        )
        item.locators = {
            str(f["name"]): f.get("locator")
            for f in fields.get(record.schema_version_id) or []
            if isinstance(f, dict) and "name" in f
        }
        items.append(item)
    return Page(items=items, next_page_token=token)


def _decision(
    principal: Principal,
    record: acquire.ExtractedRecord,
    decision: str,
    corrected_values: dict[str, Any] | None,
    reason: str | None,
    now: datetime.datetime,
) -> acquire.ReviewDecision:
    record.status = RECORD_STATUS[decision]
    record.updated_by, record.updated_at = principal.user_id, now
    return acquire.ReviewDecision(
        id=uuid7(),
        org_id=principal.org_id,
        record_id=record.id,
        decision=decision,
        # The extraction stays as extracted; the correction lives in the decision (BR-150).
        corrected_values=corrected_values,
        reason=(reason or "").strip() or None,
        decided_by=principal.user_id,
        decided_at=now,
        # BR-153: what a person corrected becomes an example for the extractor.
        used_as_example=decision == "correct",
    )


def _reviewable(run: acquire.CrawlRun) -> None:
    if run.status in rules.CLOSED_RUN:
        raise Problem(409, "invalid_state", f"The run is {run.status}; its records are final.")


@router.post(
    "/v1/extracted-records/{id}:review",
    response_model=RecordReviewResult,
    **endpoint("POST", "/v1/extracted-records/{id}:review", operation_id="reviewExtractedRecord"),
)
async def review_record(
    body: RecordReview,
    record_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> RecordReviewResult:
    now = _now()
    async with transaction(ROLE, principal) as session:
        run_id = await session.scalar(
            select(E.run_id)
            .join(R, R.id == E.run_id)
            .join(S, S.id == R.source_id)
            .where(E.id == record_id, E.org_id == principal.org_id, S.data_space_id == space)
        )
        if run_id is None:
            raise Problem(404, "not_found", "Extracted record not found.")
        # Shared lock on the run: reviews go side by side, a publish waits for them.
        run = await _run(session, principal, space, run_id, lock="share")
        _reviewable(run)
        record = await session.scalar(select(E).where(E.id == record_id).with_for_update())
        if record is None:
            raise Problem(404, "not_found", "Extracted record not found.")
        if record.status == "superseded":
            raise Problem(409, "invalid_state", "The record was superseded by a schema revert.")
        decision = _decision(
            principal, record, body.decision, body.corrected_values, body.reason, now
        )
        session.add(decision)
        await session.flush()
        await session.refresh(record)
        _emit(
            session,
            principal,
            "extracted_record",
            record.id,
            "acquire.record.reviewed",
            record_id=record.id,
            run_id=run.id,
            decision=body.decision,
            status=record.status,
            used_as_example=decision.used_as_example,
        )
        return RecordReviewResult(
            record=ExtractedRecordView.model_validate(record),
            decision=ReviewDecisionView.model_validate(decision),
        )


@router.post(
    "/v1/extracted-records:bulk-review",
    response_model=BulkReviewResult,
    **endpoint(
        "POST", "/v1/extracted-records:bulk-review", operation_id="bulkReviewExtractedRecords"
    ),
)
async def bulk_review(
    body: BulkReview, principal: CurrentPrincipal, space: CurrentDataSpace
) -> BulkReviewResult:
    now = _now()
    async with transaction(ROLE, principal) as session:
        if body.filter is not None:
            run = await _run(session, principal, space, body.filter.run_id, lock="share")
            _reviewable(run)
            query = select(E).where(E.run_id == run.id, E.status.in_(("pending", "flagged")))
            reasons = _review_reasons(body.filter.review_reasons)
            if reasons:
                query = query.where(E.review_reasons.overlap(reasons))
            records = list((await session.scalars(query.order_by(E.id).with_for_update())).all())
        else:
            wanted = set(body.record_ids or [])
            run_ids = set(
                (
                    await session.scalars(
                        select(E.run_id)
                        .join(R, R.id == E.run_id)
                        .join(S, S.id == R.source_id)
                        .where(
                            E.id.in_(wanted),
                            E.org_id == principal.org_id,
                            S.data_space_id == space,
                        )
                    )
                ).all()
            )
            for run_id in sorted(run_ids):
                _reviewable(await _run(session, principal, space, run_id, lock="share"))
            records = list(
                (
                    await session.scalars(
                        select(E)
                        .where(E.id.in_(wanted), E.run_id.in_(run_ids))
                        .order_by(E.id)
                        .with_for_update()
                    )
                ).all()
            )
            if len(records) != len(wanted):
                raise Problem(404, "not_found", "Some records were not found.")
            if any(r.status == "superseded" for r in records):
                raise Problem(
                    409, "invalid_state", "Some records were superseded by a schema revert."
                )
        by_run: dict[uuid.UUID, list[uuid.UUID]] = {}
        for record in records:
            session.add(_decision(principal, record, body.decision, None, body.reason, now))
            by_run.setdefault(record.run_id, []).append(record.id)
        await session.flush()
        status = RECORD_STATUS[body.decision]
        for run_id, ids in by_run.items():
            _emit(
                session,
                principal,
                "crawl_run",
                run_id,
                "acquire.records.bulk_reviewed",
                run_id=run_id,
                decision=body.decision,
                status=status,
                record_ids=ids,
            )
    return BulkReviewResult(
        updated=len(records), by_status={status: len(records)} if records else {}
    )


@router.post(
    "/v1/crawl-runs/{id}:publish",
    response_model=CrawlRunView,
    **endpoint("POST", "/v1/crawl-runs/{id}:publish", operation_id="publishCrawlRun"),
)
async def publish_run(
    run_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> CrawlRunView:
    async with transaction(ROLE, principal) as session:
        run = await _run(session, principal, space, run_id, lock="update")
        if run.status not in rules.PUBLISHABLE_RUN:
            raise Problem(409, "invalid_state", f"A {run.status} run is not published.")
        if run.sample_accuracy is not None and run.sample_accuracy < decimal.Decimal(
            str(rules.MIN_SAMPLE_ACCURACY)
        ):
            raise Problem(
                422,
                "business_rule_violated",
                f"Random-sample accuracy {run.sample_accuracy:.0%} is under 90%; "
                "fix the schema first (UC-14-04).",
            )
        source = await session.scalar(select(S).where(S.id == run.source_id))
        previous = run.status
        run.status = "published"
        run.finished_at = run.finished_at or _now()
        # crawl_run_publish_guard refuses here: blocking records (BR-153), an unapproved schema
        # (BR-152), an open semantic drift of the source (BR-168).
        await session.flush()
        await session.refresh(run)
        _emit(
            session,
            principal,
            "crawl_run",
            run.id,
            "acquire.run.published",
            run_id=run.id,
            source_id=run.source_id,
            data_space_id=source.data_space_id if source else space,
            schema_version_id=run.schema_version_id,
            previous_status=previous,
            status=run.status,
        )
        return CrawlRunView.model_validate(run)


# ---------------------------------------------------------------- consolidation (UC-14-05)


async def _spec(
    session: AsyncSession,
    principal: Principal,
    space: uuid.UUID,
    spec_id: uuid.UUID,
    *,
    lock: bool = False,
) -> acquire.ConsolidationSpec:
    query = select(C).where(C.id == spec_id, C.org_id == principal.org_id, C.data_space_id == space)
    row = await session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise Problem(404, "not_found", "Consolidation spec not found.")
    return row


async def _check_sources(
    session: AsyncSession, space: uuid.UUID, source_ids: list[uuid.UUID]
) -> None:
    wanted = set(source_ids)
    found = set(
        (
            await session.scalars(select(S.id).where(S.id.in_(wanted), S.data_space_id == space))
        ).all()
    )
    if found != wanted:
        raise rules.invalid(
            "source_ids", "Every source must be a crawl source of this Data Space.", "out_of_scope"
        )


async def _check_dataset(
    session: AsyncSession, principal: Principal, space: uuid.UUID, dataset_id: uuid.UUID
) -> None:
    found = await session.scalar(
        select(catalog.Dataset.id).where(
            catalog.Dataset.id == dataset_id,
            catalog.Dataset.org_id == principal.org_id,
            catalog.Dataset.data_space_id == space,
            catalog.Dataset.origin == "consolidated",
        )
    )
    if found is None:
        raise rules.invalid(
            "target_dataset_id",
            "target_dataset_id is not a consolidated dataset of this Data Space.",
        )


async def _consolidation_running(session: AsyncSession, spec_id: uuid.UUID) -> bool:
    return (
        await session.scalar(
            select(CR.id).where(CR.spec_id == spec_id, CR.status == "running").limit(1)
        )
        is not None
    )


@router.get(
    "/v1/consolidation-specs",
    response_model=Page[ConsolidationSpecView],
    **endpoint("GET", "/v1/consolidation-specs", operation_id="listConsolidationSpecs"),
)
async def list_consolidation_specs(
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    entity_type: str | None = None,
    status: Literal["draft", "active", "retired"] | None = None,
) -> Page[ConsolidationSpecView]:
    query = select(C).where(C.org_id == principal.org_id, C.data_space_id == space)
    if entity_type:
        query = query.where(C.entity_type == entity_type)
    if status:
        query = query.where(C.status == status)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(_keyset(query, page, C.created_at, C.id))).all())
    rows, token = _cut(rows, page)
    return Page(
        items=[ConsolidationSpecView.model_validate(r) for r in rows], next_page_token=token
    )


@router.post(
    "/v1/consolidation-specs",
    status_code=201,
    response_model=ConsolidationSpecView,
    **endpoint("POST", "/v1/consolidation-specs", operation_id="createConsolidationSpec"),
)
async def create_consolidation_spec(
    body: ConsolidationSpecCreate, principal: CurrentPrincipal, space: CurrentDataSpace
) -> ConsolidationSpecView:
    async with transaction(ROLE, principal) as session:
        await _check_sources(session, space, body.source_ids)
        if body.target_dataset_id is not None:
            await _check_dataset(session, principal, space, body.target_dataset_id)
        row = acquire.ConsolidationSpec(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=space,
            name=body.name.strip(),
            entity_type=body.entity_type,
            grain=body.grain,
            source_ids=list(dict.fromkeys(body.source_ids)),
            normalization=body.normalization,
            source_priority=body.source_priority,
            aggregates=body.aggregates,
            summary_template=body.summary_template,
            allow_interpolation=body.allow_interpolation,
            target_dataset_id=body.target_dataset_id,
            owner_id=principal.user_id,
            # The trigger takes created_by as the first editor (BR-156).
            last_edited_by=principal.user_id,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        _emit(
            session,
            principal,
            "consolidation_spec",
            row.id,
            "acquire.spec.created",
            spec_id=row.id,
            data_space_id=space,
            entity_type=row.entity_type,
            source_ids=row.source_ids,
            target_dataset_id=row.target_dataset_id,
            status=row.status,
        )
        return ConsolidationSpecView.model_validate(row)


@router.patch(
    "/v1/consolidation-specs/{id}",
    response_model=ConsolidationSpecView,
    **endpoint("PATCH", "/v1/consolidation-specs/{id}", operation_id="patchConsolidationSpec"),
)
async def patch_consolidation_spec(
    body: ConsolidationSpecPatch,
    spec_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> ConsolidationSpecView:
    changes = body.model_dump(exclude_unset=True)
    for field in SPEC_NOT_NULL & changes.keys():
        if changes[field] is None:
            raise rules.invalid(field, f"{field} cannot be null.")
    async with transaction(ROLE, principal) as session:
        row = await _spec(session, principal, space, spec_id, lock=True)
        if row.status == "retired":
            raise Problem(409, "invalid_state", "A retired spec cannot be changed.")
        content = sorted(SPEC_CONTENT & changes.keys())
        if content and await _consolidation_running(session, row.id):
            raise Problem(409, "invalid_state", "A consolidation run of this spec is in progress.")
        if "source_ids" in changes:
            changes["source_ids"] = list(dict.fromkeys(changes["source_ids"]))
            await _check_sources(session, space, changes["source_ids"])
        if changes.get("target_dataset_id") is not None:
            await _check_dataset(session, principal, space, changes["target_dataset_id"])
        if "owner_id" in changes:
            await _check_active_user(session, principal, changes["owner_id"], "owner_id")
        status = changes.get("status", row.status)
        target = changes.get("target_dataset_id", row.target_dataset_id)
        if status == "active" and target is None:
            raise rules.invalid(
                "target_dataset_id", "An active spec publishes into a target dataset."
            )
        if "name" in changes:
            changes["name"] = changes["name"].strip()
        previous = row.status
        for key, value in changes.items():
            setattr(row, key, value)
        # updated_by is who the trigger records as last editor when content changed (BR-156).
        row.updated_by, row.updated_at = principal.user_id, _now()
        await session.flush()
        await session.refresh(row)
        _emit(
            session,
            principal,
            "consolidation_spec",
            row.id,
            "acquire.spec.updated",
            spec_id=row.id,
            data_space_id=space,
            changed=sorted(changes),
            content_changed=bool(content),
            last_edited_by=row.last_edited_by,
            previous_status=previous,
            status=row.status,
        )
        return ConsolidationSpecView.model_validate(row)


@router.post(
    "/v1/consolidation-specs/{id}/runs",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/consolidation-specs/{id}/runs", operation_id="createConsolidationRun"),
)
async def create_consolidation_run(
    spec_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    response: Response,
    body: Annotated[ConsolidationRunCreate | None, Body()] = None,
) -> Accepted:
    request = body or ConsolidationRunCreate()
    async with transaction(ROLE, principal) as session:
        spec = await _spec(session, principal, space, spec_id, lock=True)
        if spec.status != "active":
            raise Problem(409, "invalid_state", f"The spec is {spec.status}, not active.")
        if await _consolidation_running(session, spec.id):
            raise Problem(409, "invalid_state", "A consolidation run of this spec is in progress.")
        if request.input_run_ids is not None:
            wanted = list(dict.fromkeys(request.input_run_ids))
            found = set(
                (
                    await session.scalars(
                        select(R.id).where(R.id.in_(wanted), R.source_id.in_(spec.source_ids))
                    )
                ).all()
            )
            if found != set(wanted):
                raise rules.invalid(
                    "input_run_ids", "input_run_ids are not all runs of the spec's sources."
                )
            inputs = wanted
        else:
            # Without a choice: the latest published run of each source of the spec.
            inputs = list(
                (
                    await session.scalars(
                        select(R.id)
                        .where(R.source_id.in_(spec.source_ids), R.status == "published")
                        .distinct(R.source_id)
                        .order_by(
                            R.source_id,
                            R.finished_at.desc().nulls_last(),
                            R.created_at.desc(),
                        )
                    )
                ).all()
            )
            if not inputs:
                raise Problem(
                    409, "invalid_state", "None of the spec's sources has a published run."
                )
        run = acquire.ConsolidationRun(
            id=uuid7(),
            org_id=principal.org_id,
            spec_id=spec.id,
            input_run_ids=inputs,
            started_at=_now(),
            # The trigger copies the spec's last editor whatever is sent (BR-156).
            spec_editor_id=spec.last_edited_by,
        )
        session.add(run)
        # consolidation_run_guard refuses unpublished input runs here (BR-153).
        await session.flush()
        await session.refresh(run)
        _emit(
            session,
            principal,
            "consolidation_run",
            run.id,
            "acquire.consolidation.started",
            consolidation_run_id=run.id,
            spec_id=spec.id,
            data_space_id=space,
            input_run_ids=run.input_run_ids,
            target_dataset_id=spec.target_dataset_id,
            spec_editor_id=run.spec_editor_id,
            status=run.status,
        )
        return _accepted(response, f"/v1/consolidation-runs/{run.id}", run.id, run.status)


@router.get(
    "/v1/consolidation-runs/{id}",
    response_model=ConsolidationRunView,
    **endpoint("GET", "/v1/consolidation-runs/{id}", operation_id="getConsolidationRun"),
)
async def get_consolidation_run(
    run_id: PathId, principal: CurrentPrincipal, space: CurrentDataSpace
) -> ConsolidationRunView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(CR)
            .join(C, C.id == CR.spec_id)
            .where(CR.id == run_id, CR.org_id == principal.org_id, C.data_space_id == space)
        )
        if row is None:
            raise Problem(404, "not_found", "Consolidation run not found.")
        return ConsolidationRunView.model_validate(row)


# ---------------------------------------------------------------- entity links (UC-14-05, BR-169)


@router.get(
    "/v1/entity-links",
    response_model=Page[EntityLinkView],
    **endpoint("GET", "/v1/entity-links", operation_id="listEntityLinks"),
)
async def list_entity_links(
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    entity_type: str | None = None,
    status: Annotated[
        Literal["pending", "confirmed", "rejected"],
        Query(description="Default pending: the links waiting for a decision"),
    ] = "pending",
    source_id: uuid.UUID | None = None,
) -> Page[EntityLinkView]:
    query = select(L).where(
        L.org_id == principal.org_id, L.data_space_id == space, L.status == status
    )
    if entity_type:
        query = query.where(L.entity_type == entity_type)
    if source_id:
        query = query.where(L.source_id == source_id)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(_keyset(query, page, L.created_at, L.id))).all())
    rows, token = _cut(rows, page)
    return Page(items=[EntityLinkView.model_validate(r) for r in rows], next_page_token=token)


@router.post(
    "/v1/entity-links/{id}:decide",
    response_model=EntityLinkView,
    **endpoint("POST", "/v1/entity-links/{id}:decide", operation_id="decideEntityLink"),
)
async def decide_entity_link(
    body: EntityLinkDecision,
    link_id: PathId,
    principal: CurrentPrincipal,
    space: CurrentDataSpace,
) -> EntityLinkView:
    async with transaction(ROLE, principal) as session:
        link = await session.scalar(
            select(L)
            .where(L.id == link_id, L.org_id == principal.org_id, L.data_space_id == space)
            .with_for_update()
        )
        if link is None:
            raise Problem(404, "not_found", "Entity link not found.")
        if link.status != "pending":
            raise Problem(409, "invalid_state", f"The link is already {link.status}.")
        if body.reference_entity_id is not None:
            reference = await session.scalar(
                select(acquire.ReferenceEntity).where(
                    acquire.ReferenceEntity.id == body.reference_entity_id,
                    acquire.ReferenceEntity.org_id == principal.org_id,
                )
            )
            if reference is None:
                raise rules.invalid(
                    "reference_entity_id", "reference_entity_id is not in the reference catalogue."
                )
            if reference.entity_type != link.entity_type:
                raise rules.invalid(
                    "reference_entity_id",
                    f"The reference entity is a {reference.entity_type}, the link a "
                    f"{link.entity_type} (BR-169).",
                )
            if body.status == "confirmed" and reference.status != "active":
                raise rules.invalid(
                    "reference_entity_id",
                    f"The reference entity is {reference.status}; link to its successor.",
                )
            link.reference_entity_id = reference.id
        link.status = body.status
        link.method = "human"
        link.decided_by = principal.user_id
        link.updated_by, link.updated_at = principal.user_id, _now()
        # entity_link_reference_sync copies canonical_key from the catalogue (BR-169).
        await session.flush()
        await session.refresh(link)
        _emit(
            session,
            principal,
            "entity_link",
            link.id,
            "acquire.entity_link.decided",
            entity_link_id=link.id,
            data_space_id=space,
            source_id=link.source_id,
            entity_type=link.entity_type,
            status=link.status,
            reference_entity_id=link.reference_entity_id,
            canonical_key=link.canonical_key,
        )
        return EntityLinkView.model_validate(link)
