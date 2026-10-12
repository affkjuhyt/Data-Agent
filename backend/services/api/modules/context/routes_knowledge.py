"""Knowledge layer: pages, versions, runs and lint findings (UC-07-10, UC-07-11, UC-07-12).

The database holds the rules (backend/schema/csdl/routines/context.sql): immutable versions and
one open draft per page (BR-164), claims with a source of their own version (BR-161), numbers only
as placeholders (BR-162), the served version's policy (BR-163), certification of external
sources (BR-165), the run's page limit and the dismissal window (BR-166). The routes add the
person checks a CHECK cannot see and the state machine around them.

Reads that serve a page to a reader run with app.knowledge_reader = 'on' plus the caller's Data
Spaces and clearance, so the reader policy decides what exists (a hidden page is a 404, BR-163).
A caller with `steward` in the Data Space reads as the writer: drafts and unserved pages included.

Writing a page's content is Agent Tri thức's work (worker-agent): a run or a save-back is
recorded here as a queued knowledge_run and an event, and answered 202 (A1.6).
"""

from __future__ import annotations

import datetime
import difflib
import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Path, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import context, org, qa
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import Page
from services.api.kernel.problems import Problem
from services.api.modules.context.common import (
    CLEARANCE,
    DEFAULT_CLEARANCE,
    ROLE,
    ContextPage,
    accepted,
    audit,
    emit,
    field_error,
    keyset_page,
    now,
    readable_data_spaces,
    reader_settings,
    require_active_users,
    slugify,
)
from services.api.modules.context.schemas import (
    Accepted,
    CertifyBody,
    ClaimView,
    FigureView,
    FindingResolve,
    FindingView,
    FromAnswer,
    KnowledgeIndexEntry,
    KnowledgePageCreate,
    KnowledgePageView,
    KnowledgeRunCreate,
    KnowledgeRunDetail,
    KnowledgeRunView,
    KnowledgeVersionCreate,
    KnowledgeVersionView,
    LinkView,
    ReasonBody,
    RenderedKnowledgePage,
    RunVersionRef,
    SourceView,
)

router = APIRouter()

INDEX_LIMIT = 20
DEFAULT_PAGE_LIMIT = 20
# BR-166: a dismissed finding stays hidden for 90 days.
DISMISS_DAYS = 90
Render = Literal["template", "filled"]
_NO_CERTIFY = CertifyBody()


@asynccontextmanager
async def _session(principal: Principal, *, reader: bool) -> AsyncIterator[AsyncSession]:
    settings = await reader_settings(principal) if reader else {}
    async with transaction(ROLE, principal, **settings) as session:
        yield session


def _reads_as_reader(principal: Principal) -> bool:
    return "steward" not in principal.capabilities


async def _page(
    session: AsyncSession,
    page_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> context.KnowledgePage:
    query = select(context.KnowledgePage).where(
        context.KnowledgePage.id == page_id,
        context.KnowledgePage.org_id == principal.org_id,
        context.KnowledgePage.data_space_id == data_space,
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Knowledge page not found.")
    return row


async def _version(
    session: AsyncSession,
    version_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> tuple[context.KnowledgePageVersion, context.KnowledgePage]:
    query = (
        select(context.KnowledgePageVersion, context.KnowledgePage)
        .join(
            context.KnowledgePage, context.KnowledgePage.id == context.KnowledgePageVersion.page_id
        )
        .where(
            context.KnowledgePageVersion.id == version_id,
            context.KnowledgePage.org_id == principal.org_id,
            context.KnowledgePage.data_space_id == data_space,
        )
    )
    if lock:
        query = query.with_for_update(of=context.KnowledgePageVersion)
    found = (await session.execute(query)).first()
    if found is None:
        raise Problem(404, "not_found", "Knowledge page version not found.")
    return found[0], found[1]


# ---------------------------------------------------------------- index and pages


@router.get(
    "/v1/knowledge/index",
    response_model=Page[KnowledgeIndexEntry],
    **endpoint("GET", "/v1/knowledge/index", operation_id="getKnowledgeIndex"),
)
async def knowledge_index(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    q: Annotated[str | None, Query(max_length=300)] = None,
    kind: str | None = None,
    metric_code: Annotated[str | None, Query(max_length=100)] = None,
    entity_key: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=INDEX_LIMIT)] = INDEX_LIMIT,
) -> Page[KnowledgeIndexEntry]:
    """The index lines a reader may see (BR-163: a hidden page has no line either). Ranked by
    full-text match on title and summary; the vector half of the RRF (Qdrant knowledge_index) is
    the retrieval worker's and is not merged here."""
    page = context.KnowledgePage
    query = select(page).where(page.org_id == principal.org_id, page.data_space_id == data_space)
    if kind:
        query = query.where(page.kind == kind)
    if metric_code:
        query = query.where(page.kind == "metric", page.subject_ref == metric_code)
    if entity_key:
        query = query.where(page.kind == "entity", page.subject_ref == entity_key)
    if q:
        document = func.to_tsvector("simple", page.title + " " + page.index_summary)
        terms = func.plainto_tsquery("simple", q)
        query = query.where(or_(document.op("@@")(terms), page.title.ilike(f"%{q}%"))).order_by(
            func.ts_rank(document, terms).desc(), page.updated_at.desc()
        )
    else:
        query = query.order_by(page.updated_at.desc())
    # Always as a reader: the planner reads for the person asking.
    async with _session(principal, reader=True) as session:
        rows = (await session.scalars(query.limit(limit))).all()
    return Page(items=[KnowledgeIndexEntry.model_validate(r) for r in rows])


@router.get(
    "/v1/knowledge-pages",
    response_model=Page[KnowledgePageView],
    **endpoint("GET", "/v1/knowledge-pages", operation_id="listKnowledgePages"),
)
async def list_knowledge_pages(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: ContextPage,
    status: str | None = None,
    kind: str | None = None,
    owner_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=300)] = None,
) -> Page[KnowledgePageView]:
    kp = context.KnowledgePage
    query = select(kp).where(kp.org_id == principal.org_id, kp.data_space_id == data_space)
    if status:
        query = query.where(kp.status == status)
    if kind:
        query = query.where(kp.kind == kind)
    if owner_id:
        query = query.where(kp.owner_id == owner_id)
    if q:
        query = query.where(
            or_(kp.title.ilike(f"%{q}%"), kp.slug.ilike(f"%{q}%"), kp.index_summary.ilike(f"%{q}%"))
        )
    async with _session(principal, reader=False) as session:
        items, token = await keyset_page(session, query, kp, page, KnowledgePageView.model_validate)
    return Page(items=items, next_page_token=token)


@router.post(
    "/v1/knowledge-pages",
    status_code=201,
    response_model=KnowledgePageView,
    **endpoint("POST", "/v1/knowledge-pages", operation_id="createKnowledgePage"),
)
async def create_knowledge_page(
    body: KnowledgePageCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> KnowledgePageView:
    async with _session(principal, reader=False) as session:
        await require_active_users(session, principal, {"owner_id": body.owner_id})
        row = context.KnowledgePage(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            kind=body.kind,
            subject_kind=body.subject_kind,
            subject_ref=body.subject_ref,
            slug=body.slug,
            title=body.title,
            index_summary=body.index_summary or "",
            tags=body.tags or [],
            owner_id=body.owner_id,
            status="draft",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        emit(
            session,
            principal,
            "knowledge_page",
            row.id,
            "knowledge_page.created",
            page_id=row.id,
            data_space_id=data_space,
            kind=row.kind,
            status=row.status,
        )
        await session.flush()
        await session.refresh(row)
        return KnowledgePageView.model_validate(row)


async def _version_rows(
    session: AsyncSession, version_id: uuid.UUID
) -> tuple[
    list[context.KnowledgeClaim],
    list[context.KnowledgeFigure],
    list[context.KnowledgeSourceLink],
    list[context.KnowledgeLink],
]:
    claims = (
        await session.scalars(
            select(context.KnowledgeClaim)
            .where(context.KnowledgeClaim.page_version_id == version_id)
            .order_by(context.KnowledgeClaim.anchor)
        )
    ).all()
    figures = (
        await session.scalars(
            select(context.KnowledgeFigure)
            .where(context.KnowledgeFigure.page_version_id == version_id)
            .order_by(context.KnowledgeFigure.token)
        )
    ).all()
    sources = (
        await session.scalars(
            select(context.KnowledgeSourceLink)
            .where(context.KnowledgeSourceLink.page_version_id == version_id)
            .order_by(context.KnowledgeSourceLink.created_at, context.KnowledgeSourceLink.id)
        )
    ).all()
    links = (
        await session.scalars(
            select(context.KnowledgeLink).where(context.KnowledgeLink.from_version_id == version_id)
        )
    ).all()
    return list(claims), list(figures), list(sources), list(links)


def _labels(page: context.KnowledgePage, version: context.KnowledgePageVersion | None) -> list[str]:
    labels = []
    if page.status == "stale":
        labels.append("stale")
    if page.has_external_source:
        # BR-165: an answer built on the page carries the external-source label.
        labels.append("external_source")
    if page.certification_suspended:
        labels.append("certification_suspended")
    elif version is not None and version.status == "certified":
        labels.append("certified")
    return labels


async def _rendered(
    session: AsyncSession,
    page: context.KnowledgePage,
    version: context.KnowledgePageVersion | None,
    *,
    as_of: datetime.date,
    render: str,
    valid_only: bool,
) -> RenderedKnowledgePage:
    out = RenderedKnowledgePage(
        page=KnowledgePageView.model_validate(page),
        version=KnowledgeVersionView.model_validate(version) if version else None,
        as_of=as_of,
        render=render,
        labels=_labels(page, version),
    )
    if version is None:
        return out
    claims, figures, sources, links = await _version_rows(session, version.id)
    if valid_only:
        # BR-161: a claim outside its validity at as_of is not used to answer.
        claims = [
            c
            for c in claims
            if c.valid_from <= as_of and (c.valid_to is None or c.valid_to > as_of)
        ]
    out.claims = [ClaimView.model_validate(c) for c in claims]
    out.figures = [FigureView.model_validate(f) for f in figures]
    out.sources = [SourceView.model_validate(s) for s in sources]
    out.links = [LinkView.model_validate(link) for link in links]
    return out


@router.get(
    "/v1/knowledge-pages/{id}",
    response_model=RenderedKnowledgePage,
    **endpoint("GET", "/v1/knowledge-pages/{id}", operation_id="getKnowledgePage"),
)
async def get_knowledge_page(
    page_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    as_of: datetime.date | None = None,
    render: Render = "template",
) -> RenderedKnowledgePage:
    """The served version with the claims valid at as_of. Figures are returned as bindings; the
    numbers are filled for the reader by query.semantic under the reader's row, column and mask
    policies (BR-162, ADR-10), never stored on the page."""
    reader = _reads_as_reader(principal)
    async with _session(principal, reader=reader) as session:
        page = await _page(session, page_id, principal, data_space)
        version = None
        if page.current_version_id is not None:
            version = await session.scalar(
                select(context.KnowledgePageVersion).where(
                    context.KnowledgePageVersion.id == page.current_version_id
                )
            )
        if reader and version is None:
            # The page passes the reader policy but its served version does not (a source the
            # reader may not see): nothing to show, the same answer as a hidden page.
            raise Problem(404, "not_found", "Knowledge page not found.")
        if reader:
            # Orphan scan input (UC-07-11): when a reader last opened the page.
            page.last_read_at = now()
        return await _rendered(
            session,
            page,
            version,
            as_of=as_of or datetime.date.today(),
            render=render,
            valid_only=True,
        )


@router.get(
    "/v1/knowledge-pages/{id}/versions",
    response_model=Page[KnowledgeVersionView],
    **endpoint(
        "GET", "/v1/knowledge-pages/{id}/versions", operation_id="listKnowledgePageVersions"
    ),
)
async def list_knowledge_page_versions(
    page_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: ContextPage,
    status: str | None = None,
) -> Page[KnowledgeVersionView]:
    async with _session(principal, reader=_reads_as_reader(principal)) as session:
        owner = await _page(session, page_id, principal, data_space)
        version = context.KnowledgePageVersion
        query = select(version).where(version.page_id == owner.id)
        if status:
            query = query.where(version.status == status)
        items, token = await keyset_page(
            session, query, version, page, KnowledgeVersionView.model_validate
        )
    return Page(items=items, next_page_token=token)


@router.get(
    "/v1/knowledge-page-versions/{id}",
    response_model=RenderedKnowledgePage,
    **endpoint("GET", "/v1/knowledge-page-versions/{id}", operation_id="getKnowledgePageVersion"),
)
async def get_knowledge_page_version(
    version_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    render: Render = "template",
    diff: Literal["parent"] | None = None,
) -> RenderedKnowledgePage:
    """Exactly one version, superseded ones included, so an old citation opens what it cited
    (UC-07-10 step 7). A reader still needs every source of that version (BR-163)."""
    async with _session(principal, reader=_reads_as_reader(principal)) as session:
        version, page = await _version(session, version_id, principal, data_space)
        out = await _rendered(
            session,
            page,
            version,
            as_of=datetime.date.today(),
            render=render,
            valid_only=False,
        )
        if diff == "parent" and version.parent_version_id is not None:
            parent = await session.scalar(
                select(context.KnowledgePageVersion.body).where(
                    context.KnowledgePageVersion.id == version.parent_version_id
                )
            )
            if parent is not None:
                out.parent_body = parent
                out.diff = "\n".join(
                    difflib.unified_diff(
                        parent.splitlines(),
                        version.body.splitlines(),
                        fromfile=f"v{version.version - 1}",
                        tofile=f"v{version.version}",
                        lineterm="",
                    )
                )
        return out


# ---------------------------------------------------------------- writing versions


def _content_hash(kind: str, ref: uuid.UUID) -> str:
    # Stand-in when the writer gives no hash of the source part it used (a manual edit): stable
    # per source, so the next ingest compares like with like.
    return hashlib.sha256(f"{kind}:{ref}".encode()).hexdigest()


async def _org_data_spaces(
    session: AsyncSession, principal: Principal, ids: set[uuid.UUID]
) -> set[uuid.UUID]:
    found = await session.scalars(
        select(org.DataSpace.id).where(
            org.DataSpace.id.in_(ids), org.DataSpace.org_id == principal.org_id
        )
    )
    return set(found.all())


async def _write_version(
    session: AsyncSession,
    principal: Principal,
    page: context.KnowledgePage,
    body: KnowledgeVersionCreate,
) -> context.KnowledgePageVersion:
    keys = [s.source_key for s in body.sources]
    if len(keys) != len(set(keys)):
        raise field_error("sources", "source_key must be unique within sources.")
    for i, claim in enumerate(body.claims):
        if claim.source_key not in keys:
            raise field_error(f"claims[{i}].source_key", "source_key names no source.")
    for i, figure in enumerate(body.figures or []):
        if figure.source_key not in keys:
            raise field_error(f"figures[{i}].source_key", "source_key names no source.")
    spaces = {s.data_space_id for s in body.sources if s.data_space_id is not None}
    if spaces - await _org_data_spaces(session, principal, spaces):
        raise field_error("sources", "A source names a Data Space outside this organisation.")
    if body.knowledge_run_id is not None:
        run = await session.scalar(
            select(context.KnowledgeRun.id).where(
                context.KnowledgeRun.id == body.knowledge_run_id,
                context.KnowledgeRun.data_space_id == page.data_space_id,
            )
        )
        if run is None:
            raise field_error(
                "knowledge_run_id", "knowledge_run_id is not a run of this Data Space."
            )
    targets: list[tuple[uuid.UUID, str]] = []
    for i, link in enumerate(body.links or []):
        target_query = select(context.KnowledgePage.id).where(
            context.KnowledgePage.data_space_id == page.data_space_id,
            context.KnowledgePage.status != "retired",
        )
        if link.to_page_id is not None:
            target_query = target_query.where(context.KnowledgePage.id == link.to_page_id)
        else:
            target_query = target_query.where(context.KnowledgePage.slug == link.to_slug)
        target = await session.scalar(target_query)
        if target is None or target == page.id:
            raise field_error(f"links[{i}]", "A link points at a page of this Data Space in use.")
        targets.append((target, link.link_kind))

    # version, parent_version_id and body_sha256 are filled by the guard trigger (BR-164).
    version = context.KnowledgePageVersion(
        id=uuid7(),
        org_id=principal.org_id,
        page_id=page.id,
        status="draft",
        origin=body.origin or ("ingest" if body.knowledge_run_id else "manual"),
        body=body.body,
        summary=body.summary or "",
        change_note=body.change_note,
        change_size=body.change_size or "minor",
        knowledge_run_id=body.knowledge_run_id,
        created_by=principal.user_id,
    )
    session.add(version)
    await session.flush()
    source_ids: dict[str, uuid.UUID] = {}
    for s in body.sources:
        link_id = uuid7()
        source_ids[s.source_key] = link_id
        dataset_version_id = s.dataset_version_id
        if s.source_kind == "dataset_version" and dataset_version_id is None:
            dataset_version_id = s.source_ref
        session.add(
            context.KnowledgeSourceLink(
                id=link_id,
                org_id=principal.org_id,
                page_version_id=version.id,
                source_kind=s.source_kind,
                source_ref=s.source_ref,
                source_label=s.source_label,
                dataset_version_id=dataset_version_id,
                external=s.external,
                content_sha256=s.content_sha256 or _content_hash(s.source_kind, s.source_ref),
                data_space_id=s.data_space_id or page.data_space_id,
                classification=s.classification,
                pii_column_refs=s.pii_column_refs,
            )
        )
    await session.flush()
    for c in body.claims:
        session.add(
            context.KnowledgeClaim(
                id=uuid7(),
                org_id=principal.org_id,
                page_version_id=version.id,
                anchor=c.anchor,
                statement=c.statement,
                subject_key=c.subject_key,
                attribute=c.attribute,
                period=c.period,
                source_link_id=source_ids[c.source_key],
                valid_from=c.valid_from,
                valid_to=c.valid_to,
                figure_condition=c.figure_condition,
            )
        )
    for f in body.figures or []:
        session.add(
            context.KnowledgeFigure(
                id=uuid7(),
                org_id=principal.org_id,
                page_version_id=version.id,
                token=f.token,
                binding_kind=f.binding_kind,
                metric_code=f.metric_code,
                aggregate_code=f.aggregate_code,
                dataset_id=f.dataset_id,
                params=f.params,
                display_format=f.display_format,
                source_link_id=source_ids[f.source_key],
            )
        )
    for target, link_kind in targets:
        session.add(
            context.KnowledgeLink(
                id=uuid7(),
                org_id=principal.org_id,
                from_version_id=version.id,
                from_page_id=page.id,
                to_page_id=target,
                link_kind=link_kind,
            )
        )
    await session.flush()
    await session.refresh(version)
    return version


@router.post(
    "/v1/knowledge-pages/{id}/versions",
    status_code=201,
    response_model=KnowledgeVersionView,
    **endpoint(
        "POST", "/v1/knowledge-pages/{id}/versions", operation_id="createKnowledgePageVersion"
    ),
)
async def create_knowledge_page_version(
    body: KnowledgeVersionCreate,
    page_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> KnowledgeVersionView:
    async with _session(principal, reader=False) as session:
        page = await _page(session, page_id, principal, data_space)
        if page.status == "retired":
            raise Problem(409, "invalid_state", "A retired page takes no new version.")
        version = await _write_version(session, principal, page, body)
        emit(
            session,
            principal,
            "knowledge_page_version",
            version.id,
            "knowledge_page_version.created",
            page_id=page.id,
            version_id=version.id,
            version=version.version,
            data_space_id=data_space,
            origin=version.origin,
            status=version.status,
        )
        return KnowledgeVersionView.model_validate(version)


@router.post(
    "/v1/knowledge-page-versions/{id}:submit",
    response_model=KnowledgeVersionView,
    **endpoint(
        "POST", "/v1/knowledge-page-versions/{id}:submit", operation_id="submitKnowledgePageVersion"
    ),
)
async def submit_knowledge_page_version(
    version_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> KnowledgeVersionView:
    async with _session(principal, reader=False) as session:
        version, page = await _version(session, version_id, principal, data_space, lock=True)
        if version.status != "draft":
            raise Problem(409, "invalid_state", f"A {version.status} version is not submitted.")
        # inbox.proposal is the inbox module's: the id is reserved here and the inbox opens the
        # knowledge_page proposal for the page owner from the event (UC-07-10 step 6).
        version.proposal_id = uuid7()
        version.status = "pending_review"
        # The guard trigger checks claims, placeholders and links now (BR-161, BR-162, BR-167).
        await session.flush()
        emit(
            session,
            principal,
            "knowledge_page_version",
            version.id,
            "knowledge_page_version.submitted",
            page_id=page.id,
            version_id=version.id,
            version=version.version,
            data_space_id=data_space,
            proposal_id=version.proposal_id,
            proposal_kind="knowledge_page",
            target_service="context",
            assignee_id=page.owner_id,
            created_by=version.created_by,
            change_size=version.change_size,
            origin=version.origin,
            status=version.status,
        )
        await session.flush()
        await session.refresh(version)
        return KnowledgeVersionView.model_validate(version)


@router.post(
    "/v1/knowledge-page-versions/{id}:certify",
    response_model=KnowledgeVersionView,
    **endpoint(
        "POST",
        "/v1/knowledge-page-versions/{id}:certify",
        operation_id="certifyKnowledgePageVersion",
    ),
)
async def certify_knowledge_page_version(
    version_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    body: Annotated[CertifyBody, Body()] = _NO_CERTIFY,
) -> KnowledgeVersionView:
    async with _session(principal, reader=False) as session:
        version, page = await _version(session, version_id, principal, data_space, lock=True)
        if version.status != "reviewed":
            raise Problem(
                409,
                "invalid_state",
                f"Only a reviewed version is certified; this one is {version.status}.",
            )
        if principal.user_id in (version.created_by, version.reviewed_by):
            # BR-164: the certifier is a third person, neither the author nor the reviewer.
            raise Problem(
                403,
                "policy_denied",
                "The certifier must be someone other than the author and the reviewer (BR-164).",
            )
        version.status = "certified"
        version.certified_by, version.certified_at = principal.user_id, now()
        # The guard trigger refuses an uncertified external source here (BR-165).
        await session.flush()
        if page.current_version_id == version.id and page.certification_suspended:
            # Certifying again restores the certification effect a revoked source took (BR-165).
            page.certification_suspended = False
            page.updated_by, page.updated_at = principal.user_id, now()
        if body.note:
            audit(
                session,
                principal,
                action="knowledge_page_version.certify",
                object_kind="knowledge_page_version",
                object_id=version.id,
                after={"status": version.status, "note": body.note},
            )
        await session.flush()
        await session.refresh(version)
        return KnowledgeVersionView.model_validate(version)


@router.post(
    "/v1/knowledge-pages/{id}:retire",
    response_model=KnowledgePageView,
    **endpoint("POST", "/v1/knowledge-pages/{id}:retire", operation_id="retireKnowledgePage"),
)
async def retire_knowledge_page(
    body: ReasonBody,
    page_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> KnowledgePageView:
    async with _session(principal, reader=False) as session:
        page = await _page(session, page_id, principal, data_space, lock=True)
        if page.status == "retired":
            raise Problem(409, "invalid_state", "The page is already retired.")
        # Pages are never deleted: history and old citations stay readable (UC-07-11).
        page.status = "retired"
        page.updated_by, page.updated_at = principal.user_id, now()
        emit(
            session,
            principal,
            "knowledge_page",
            page.id,
            "knowledge_page.retired",
            page_id=page.id,
            data_space_id=data_space,
            status=page.status,
        )
        audit(
            session,
            principal,
            action="knowledge_page.retire",
            object_kind="knowledge_page",
            object_id=page.id,
            after={"status": page.status, "reason": body.reason},
        )
        await session.flush()
        await session.refresh(page)
        return KnowledgePageView.model_validate(page)


# ---------------------------------------------------------------- save an answer (UC-07-12)

_NOT_ANSWERS = ("clarification", "refusal")


def _number_check_failures(check: Any) -> list[str]:
    if not isinstance(check, dict):
        return []
    reasons = []
    if int(check.get("untraceable_final") or 0) > 0:
        reasons.append("a number of the answer cannot be traced to a query or compute run")
    if int(check.get("sentences_dropped") or 0) > 0:
        reasons.append("a sentence was dropped because its number could not be traced")
    return reasons


async def _reader_can_see(
    session: AsyncSession, principal: Principal, page: context.KnowledgePage
) -> bool:
    """The BR-163 test of one page for the caller, done in Python because this transaction
    writes (it is not a reader transaction)."""
    spaces = set(await readable_data_spaces(session, principal))
    return (
        set(page.required_data_space_ids) <= spaces
        and CLEARANCE.get(page.classification, 99) <= DEFAULT_CLEARANCE
    )


@router.post(
    "/v1/knowledge-pages:from-answer",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/knowledge-pages:from-answer", operation_id="saveAnswerAsKnowledge"),
)
async def save_answer_as_knowledge(
    body: FromAnswer,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> dict[str, Any]:
    """BR-167 checks, the target page, then a saveback run Agent Tri thức executes: it turns the
    numbers into placeholders and the citations into claims, cites the answer as a source and
    submits the version for review (UC-07-12 steps 2-4). Answered 202 with the run."""
    async with _session(principal, reader=False) as session:
        found = (
            await session.execute(
                select(qa.Answer, qa.Question.text, qa.Conversation.user_id)
                .join(qa.Question, qa.Question.id == qa.Answer.question_id)
                .join(qa.Conversation, qa.Conversation.id == qa.Question.conversation_id)
                .where(
                    qa.Answer.id == body.answer_id,
                    qa.Answer.org_id == principal.org_id,
                    qa.Conversation.data_space_id == data_space,
                )
            )
        ).first()
        if found is None:
            raise Problem(404, "not_found", "Answer not found.")
        answer, question_text, asker = found
        if asker != principal.user_id and "steward" not in principal.capabilities:
            raise Problem(
                403, "policy_denied", "Only the person who asked, or a steward, saves this answer."
            )
        reasons = _number_check_failures(answer.number_check)
        if answer.kind in _NOT_ANSWERS:
            reasons.append(f"a {answer.kind} is not knowledge")
        if answer.kind == "forecast":
            # BR-158: whether the forecast model is released lives in schema forecast, which
            # svc_context cannot read; a forecast answer is refused rather than let through.
            reasons.append("a forecast answer is not saved as knowledge (BR-158)")
        if reasons:
            raise Problem(
                422,
                "business_rule_violated",
                "The answer cannot be saved (BR-167): " + "; ".join(reasons) + ".",
            )
        target: context.KnowledgePage | None = None
        if body.target_page_id is not None:
            target = await session.scalar(
                select(context.KnowledgePage).where(
                    context.KnowledgePage.id == body.target_page_id,
                    context.KnowledgePage.data_space_id == data_space,
                    context.KnowledgePage.status != "retired",
                )
            )
            if target is not None and not await _reader_can_see(session, principal, target):
                target = None
        mode = body.mode or ("append" if target is not None else "new_page")
        if target is None:
            # AC-07-12-05: no right on the target page, or none named: a new page in the
            # caller's Data Space.
            mode = "new_page"
            title = body.title or question_text[:300]
            target = context.KnowledgePage(
                id=uuid7(),
                org_id=principal.org_id,
                data_space_id=data_space,
                kind="topic",
                subject_kind="topic",
                slug=f"{slugify(title)}-{uuid7().hex[-6:]}",
                title=title,
                owner_id=principal.user_id,
                status="draft",
                created_by=principal.user_id,
                updated_by=principal.user_id,
            )
            session.add(target)
            emit(
                session,
                principal,
                "knowledge_page",
                target.id,
                "knowledge_page.created",
                page_id=target.id,
                data_space_id=data_space,
                kind=target.kind,
                status=target.status,
            )
        run = context.KnowledgeRun(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            kind="ingest",
            trigger="saveback",
            source_kind="answer",
            source_ref=answer.id,
            status="queued",
            page_limit=1,
            created_by=principal.user_id,
        )
        session.add(run)
        emit(
            session,
            principal,
            "knowledge_run",
            run.id,
            "knowledge_run.requested",
            run_id=run.id,
            data_space_id=data_space,
            kind=run.kind,
            trigger=run.trigger,
            answer_id=answer.id,
            page_id=target.id,
            mode=mode,
            requested_by=principal.user_id,
            status=run.status,
        )
        if body.note:
            audit(
                session,
                principal,
                action="knowledge_page.save_answer",
                object_kind="knowledge_run",
                object_id=run.id,
                after={"answer_id": answer.id, "page_id": target.id, "note": body.note},
            )
        # One run per Data Space and kind at a time: a second one answers 409 here.
        await session.flush()
    return accepted(response, f"/v1/knowledge-runs/{run.id}", run.id, "queued")


# ---------------------------------------------------------------- runs (UC-07-10, UC-07-11)


@router.post(
    "/v1/knowledge-runs",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/knowledge-runs", operation_id="createKnowledgeRun"),
)
async def create_knowledge_run(
    body: KnowledgeRunCreate,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> dict[str, Any]:
    if body.data_space_id != data_space:
        raise field_error("data_space_id", "data_space_id must be the Data Space of the request.")
    async with _session(principal, reader=False) as session:
        run = context.KnowledgeRun(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            kind=body.kind,
            trigger="manual",
            source_kind=body.source_kind,
            source_ref=body.source_ref,
            status="queued",
            page_limit=body.page_limit or DEFAULT_PAGE_LIMIT,
            created_by=principal.user_id,
        )
        session.add(run)
        emit(
            session,
            principal,
            "knowledge_run",
            run.id,
            "knowledge_run.requested",
            run_id=run.id,
            data_space_id=data_space,
            kind=run.kind,
            trigger=run.trigger,
            source_kind=run.source_kind,
            source_ref=run.source_ref,
            page_limit=run.page_limit,
            status=run.status,
        )
        # BR-166: one run of a kind per Data Space; a second answers 409 conflict.
        await session.flush()
    return accepted(response, f"/v1/knowledge-runs/{run.id}", run.id, "queued")


@router.get(
    "/v1/knowledge-runs",
    response_model=Page[KnowledgeRunView],
    **endpoint("GET", "/v1/knowledge-runs", operation_id="listKnowledgeRuns"),
)
async def list_knowledge_runs(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: ContextPage,
    kind: str | None = None,
    status: str | None = None,
) -> Page[KnowledgeRunView]:
    run = context.KnowledgeRun
    query = select(run).where(run.org_id == principal.org_id, run.data_space_id == data_space)
    if kind:
        query = query.where(run.kind == kind)
    if status:
        query = query.where(run.status == status)
    async with _session(principal, reader=False) as session:
        items, token = await keyset_page(session, query, run, page, KnowledgeRunView.model_validate)
    return Page(items=items, next_page_token=token)


@router.get(
    "/v1/knowledge-runs/{id}",
    response_model=KnowledgeRunDetail,
    **endpoint("GET", "/v1/knowledge-runs/{id}", operation_id="getKnowledgeRun"),
)
async def get_knowledge_run(
    run_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> KnowledgeRunDetail:
    async with _session(principal, reader=False) as session:
        run = await session.scalar(
            select(context.KnowledgeRun).where(
                context.KnowledgeRun.id == run_id,
                context.KnowledgeRun.org_id == principal.org_id,
                context.KnowledgeRun.data_space_id == data_space,
            )
        )
        if run is None:
            raise Problem(404, "not_found", "Knowledge run not found.")
        versions = (
            await session.scalars(
                select(context.KnowledgePageVersion)
                .where(context.KnowledgePageVersion.knowledge_run_id == run.id)
                .order_by(context.KnowledgePageVersion.created_at)
            )
        ).all()
    detail = KnowledgeRunDetail.model_validate(run)
    detail.page_versions = [RunVersionRef.model_validate(v) for v in versions]
    return detail


# ---------------------------------------------------------------- findings (UC-07-11)


@router.get(
    "/v1/knowledge-findings",
    response_model=Page[FindingView],
    **endpoint("GET", "/v1/knowledge-findings", operation_id="listKnowledgeFindings"),
)
async def list_knowledge_findings(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: ContextPage,
    run_id: uuid.UUID | None = None,
    status: str | None = None,
    kind: str | None = None,
    severity: str | None = None,
    page_id: uuid.UUID | None = None,
) -> Page[FindingView]:
    finding = context.KnowledgeLintFinding
    query = select(finding).where(
        finding.org_id == principal.org_id, finding.data_space_id == data_space
    )
    if status:
        query = query.where(finding.status == status)
    else:
        # BR-166: a dismissed finding stays out of sight unless asked for by status.
        query = query.where(finding.status != "dismissed")
    for column, value in (
        (finding.run_id, run_id),
        (finding.kind, kind),
        (finding.severity, severity),
        (finding.page_id, page_id),
    ):
        if value is not None:
            query = query.where(column == value)
    async with _session(principal, reader=False) as session:
        items, token = await keyset_page(session, query, finding, page, FindingView.model_validate)
    return Page(items=items, next_page_token=token)


async def _draft_from_served(
    session: AsyncSession,
    principal: Principal,
    page: context.KnowledgePage,
    finding: context.KnowledgeLintFinding,
) -> uuid.UUID:
    """propose_edit: a lint_fix draft opened from the served version, sources, claims, figures
    and links included. Agent Tri thức writes the fix from it and it goes through review like any
    version; nothing is published without review (UC-07-11 step 6)."""
    served = await session.scalar(
        select(context.KnowledgePageVersion).where(
            context.KnowledgePageVersion.id == page.current_version_id
        )
    )
    if served is None:
        raise Problem(
            422, "business_rule_violated", "The page has no served version to propose an edit to."
        )
    claims, figures, sources, links = await _version_rows(session, served.id)
    draft = context.KnowledgePageVersion(
        id=uuid7(),
        org_id=principal.org_id,
        page_id=page.id,
        parent_version_id=served.id,
        status="draft",
        origin="lint_fix",
        body=served.body,
        summary="",
        change_note=f"lint finding {finding.kind}",
        change_size="minor",
        created_by=principal.user_id,
    )
    session.add(draft)
    await session.flush()
    new_source: dict[uuid.UUID, uuid.UUID] = {}
    for s in sources:
        new_source[s.id] = uuid7()
        session.add(
            context.KnowledgeSourceLink(
                id=new_source[s.id],
                org_id=principal.org_id,
                page_version_id=draft.id,
                source_kind=s.source_kind,
                source_ref=s.source_ref,
                source_label=s.source_label,
                dataset_version_id=s.dataset_version_id,
                external=s.external,
                content_sha256=s.content_sha256,
                data_space_id=s.data_space_id,
                classification=s.classification,
                pii_column_refs=s.pii_column_refs,
            )
        )
    await session.flush()
    for c in claims:
        session.add(
            context.KnowledgeClaim(
                id=uuid7(),
                org_id=principal.org_id,
                page_version_id=draft.id,
                anchor=c.anchor,
                statement=c.statement,
                subject_key=c.subject_key,
                attribute=c.attribute,
                period=c.period,
                source_link_id=new_source[c.source_link_id],
                valid_from=c.valid_from,
                valid_to=c.valid_to,
                figure_condition=c.figure_condition,
            )
        )
    for f in figures:
        session.add(
            context.KnowledgeFigure(
                id=uuid7(),
                org_id=principal.org_id,
                page_version_id=draft.id,
                token=f.token,
                binding_kind=f.binding_kind,
                metric_code=f.metric_code,
                aggregate_code=f.aggregate_code,
                dataset_id=f.dataset_id,
                params=f.params,
                display_format=f.display_format,
                source_link_id=new_source[f.source_link_id],
            )
        )
    for link in links:
        session.add(
            context.KnowledgeLink(
                id=uuid7(),
                org_id=principal.org_id,
                from_version_id=draft.id,
                from_page_id=page.id,
                to_page_id=link.to_page_id,
                link_kind=link.link_kind,
            )
        )
    await session.flush()
    return draft.id


@router.post(
    "/v1/knowledge-findings/{id}:resolve",
    response_model=FindingView,
    **endpoint(
        "POST", "/v1/knowledge-findings/{id}:resolve", operation_id="resolveKnowledgeFinding"
    ),
)
async def resolve_knowledge_finding(
    body: FindingResolve,
    finding_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> FindingView:
    """One of the six fixed actions (BR-166). The page effect of mark_stale and retire_page and
    the finding events are the database's (knowledge_lint_finding_apply)."""
    async with _session(principal, reader=False) as session:
        finding = await session.scalar(
            select(context.KnowledgeLintFinding)
            .where(
                context.KnowledgeLintFinding.id == finding_id,
                context.KnowledgeLintFinding.org_id == principal.org_id,
                context.KnowledgeLintFinding.data_space_id == data_space,
            )
            .with_for_update()
        )
        if finding is None:
            raise Problem(404, "not_found", "Finding not found.")
        if finding.status != "open":
            # UC-07-11 E: a handled finding keeps the action it was handled with.
            raise Problem(409, "conflict", f"The finding is already {finding.status}.")
        page = None
        if body.action in ("propose_edit", "mark_stale", "retire_page"):
            if finding.page_id is None:
                raise field_error("action", f"{body.action} needs a finding about a page.")
            page = await _page(session, finding.page_id, principal, data_space, lock=True)
        stamp = now()
        finding.action = body.action
        finding.action_reason = body.reason
        if body.action == "dismiss":
            finding.status = "dismissed"
            finding.suppressed_until = stamp + datetime.timedelta(days=DISMISS_DAYS)
            finding.resolved_by, finding.resolved_at = principal.user_id, stamp
        elif body.action == "retire_page":
            finding.status = "resolved"
            finding.resolved_by, finding.resolved_at = principal.user_id, stamp
        elif body.action == "reassign":
            # Still open, for the new assignee; the inbox moves the card from the event.
            emit(
                session,
                principal,
                "knowledge_lint_finding",
                finding.id,
                "knowledge_finding.reassigned",
                finding_id=finding.id,
                data_space_id=data_space,
                assignee_id=body.assignee_id,
                hitl_request_id=finding.hitl_request_id,
                status=finding.status,
            )
        else:
            if body.action == "propose_edit" and page is not None:
                finding.page_version_id = await _draft_from_served(
                    session, principal, page, finding
                )
            finding.status = "actioned"
        if body.action == "reassign":
            await require_active_users(session, principal, {"assignee_id": body.assignee_id})
        await session.flush()
        await session.refresh(finding)
        return FindingView.model_validate(finding)
