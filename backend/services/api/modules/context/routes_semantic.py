"""Semantic layer: metrics and their versions (UC-07-01), relationships, synonyms and examples
(UC-07-02), the business glossary and personal readings (UC-07-09).
Everything a steward declares starts unapproved (draft or pending_review) and becomes usable only
through review (UC-07-03): the outbox event of each create is what the checker, the evaluation
and the inbox act on. Compiling and running a metric is the semantic compiler's job; :preview
records the request and answers 202.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Path, Query, Response
from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import catalog, context
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import Page, encode_page_token, invalid_page_token
from services.api.kernel.problems import Problem
from services.api.modules.context.common import (
    ROLE,
    ContextPage,
    accepted,
    emit,
    field_error,
    keyset_page,
    normalize,
    now,
    require_active_users,
)
from services.api.modules.context.schemas import (
    Accepted,
    ExampleCreate,
    ExampleView,
    MetricCreate,
    MetricDetail,
    MetricPreview,
    MetricUsage,
    MetricVersionCreate,
    MetricVersionView,
    MetricView,
    RelationshipCreate,
    RelationshipView,
    SynonymCreate,
    SynonymView,
    TermCreate,
    TermPreferencesPut,
    TermView,
    UnmappedPhraseView,
    UserTermPrefView,
)

router = APIRouter()

# UC-07-01 step 3: the preview runs over the last six periods unless asked otherwise.
PREVIEW_PERIODS = 6
PREVIEWABLE = ("draft", "testing", "broken")
_NO_PREVIEW = MetricPreview()


async def _datasets_in_space(
    session: AsyncSession, ids: set[uuid.UUID], data_space: uuid.UUID
) -> set[uuid.UUID]:
    found = await session.scalars(
        select(catalog.Dataset.id).where(
            catalog.Dataset.id.in_(ids), catalog.Dataset.data_space_id == data_space
        )
    )
    return set(found.all())


# ---------------------------------------------------------------- metrics (UC-07-01)


async def _metric(
    session: AsyncSession,
    metric_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> context.Metric:
    query = select(context.Metric).where(
        context.Metric.id == metric_id,
        context.Metric.org_id == principal.org_id,
        context.Metric.data_space_id == data_space,
    )
    if lock:
        # Serialises version numbers of concurrent writers of one metric.
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Metric not found.")
    return row


async def _metric_views(
    session: AsyncSession, rows: list[context.Metric]
) -> list[MetricView]:
    if not rows:
        return []
    versions = (
        await session.scalars(
            select(context.MetricVersion)
            .where(context.MetricVersion.metric_id.in_([r.id for r in rows]))
            .order_by(context.MetricVersion.version)
        )
    ).all()
    by_metric: dict[uuid.UUID, list[context.MetricVersion]] = {}
    for v in versions:
        by_metric.setdefault(v.metric_id, []).append(v)
    out = []
    for row in rows:
        mine = by_metric.get(row.id, [])
        current = next((v for v in mine if v.version == row.current_version), None)
        shown = current or (mine[-1] if mine else None)
        view = MetricView.model_validate(row)
        view.display_name = shown.display_name if shown else None
        view.certified = any(v.status == "certified" for v in mine)
        out.append(view)
    return out


def _certified_version() -> Any:
    return exists().where(
        context.MetricVersion.metric_id == context.Metric.id,
        context.MetricVersion.status == "certified",
    )


@router.get(
    "/v1/metrics",
    response_model=Page[MetricView],
    **endpoint("GET", "/v1/metrics", operation_id="listMetrics"),
)
async def list_metrics(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: ContextPage,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status: str | None = None,
    certified: bool | None = None,
) -> Page[MetricView]:
    metric = context.Metric
    query = select(metric).where(metric.org_id == principal.org_id, metric.data_space_id == data_space)
    if status:
        query = query.where(metric.status == status)
    if certified is not None:
        query = query.where(_certified_version() if certified else ~_certified_version())
    if q:
        named = exists().where(
            context.MetricVersion.metric_id == metric.id,
            context.MetricVersion.display_name.ilike(f"%{q}%"),
        )
        query = query.where(or_(metric.code.ilike(f"%{q}%"), named))
    async with transaction(ROLE, principal) as session:
        rows, token = await keyset_page(session, query, metric, page, lambda r: r)
        items = await _metric_views(session, rows)
    return Page(items=items, next_page_token=token)


def _version_row(
    principal: Principal, metric: context.Metric, version: int, body: MetricVersionCreate
) -> context.MetricVersion:
    values: dict[str, Any] = body.model_dump(exclude_none=True)
    return context.MetricVersion(
        id=uuid7(),
        org_id=principal.org_id,
        metric_id=metric.id,
        version=version,
        status="draft",
        **values,
    )


async def _check_dimensions(
    session: AsyncSession, body: MetricVersionCreate, data_space: uuid.UUID
) -> None:
    wanted = set(body.allowed_dimensions or [])
    if not wanted:
        return
    found = set(
        (
            await session.scalars(
                select(context.SemanticDimension.id).where(
                    context.SemanticDimension.id.in_(wanted),
                    context.SemanticDimension.data_space_id == data_space,
                )
            )
        ).all()
    )
    if wanted - found:
        raise field_error(
            "allowed_dimensions", "allowed_dimensions names a dimension outside this Data Space."
        )


@router.post(
    "/v1/metrics",
    status_code=201,
    response_model=MetricDetail,
    **endpoint("POST", "/v1/metrics", operation_id="createMetric"),
)
async def create_metric(
    body: MetricCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> MetricDetail:
    async with transaction(ROLE, principal) as session:
        await require_active_users(session, principal, {"owner_id": body.owner_id})
        if body.canonical_dataset_id is not None and not await _datasets_in_space(
            session, {body.canonical_dataset_id}, data_space
        ):
            raise field_error(
                "canonical_dataset_id", "canonical_dataset_id is not a dataset of this Data Space."
            )
        row = context.Metric(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            code=body.code,
            owner_id=body.owner_id,
            consumer_group=body.consumer_group,
            canonical_dataset_id=body.canonical_dataset_id,
            status="draft",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        # Unique (data_space_id, code) answers 409 here, before the version is written.
        await session.flush()
        versions: list[context.MetricVersion] = []
        if body.definition is not None:
            await _check_dimensions(session, body.definition, data_space)
            versions.append(_version_row(principal, row, 1, body.definition))
            session.add(versions[0])
        emit(
            session,
            principal,
            "metric",
            row.id,
            "metric.created",
            metric_id=row.id,
            data_space_id=data_space,
            code=row.code,
            status=row.status,
            version_id=versions[0].id if versions else None,
        )
        await session.flush()
        await session.refresh(row)
        for v in versions:
            await session.refresh(v)
        detail = MetricDetail.model_validate(row)
        detail.versions = [MetricVersionView.model_validate(v) for v in versions]
        detail.display_name = versions[0].display_name if versions else None
        return detail


@router.get(
    "/v1/metrics/{id}",
    response_model=MetricDetail,
    **endpoint("GET", "/v1/metrics/{id}", operation_id="getMetric"),
)
async def get_metric(
    metric_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> MetricDetail:
    async with transaction(ROLE, principal) as session:
        row = await _metric(session, metric_id, principal, data_space)
        view = (await _metric_views(session, [row]))[0]
        versions = (
            await session.scalars(
                select(context.MetricVersion)
                .where(context.MetricVersion.metric_id == row.id)
                .order_by(context.MetricVersion.version.desc())
            )
        ).all()
        products = (
            await session.scalars(
                select(context.DataProductMember.data_product_id).where(
                    context.DataProductMember.member_kind == "metric",
                    context.DataProductMember.member_ref == row.id,
                )
            )
        ).all()
        pages = (
            await session.scalars(
                select(context.KnowledgePage.id).where(
                    context.KnowledgePage.data_space_id == data_space,
                    context.KnowledgePage.kind == "metric",
                    context.KnowledgePage.subject_ref == row.code,
                )
            )
        ).all()
    # Agents, artifacts and reports that use the metric live in schemas svc_context cannot read
    # (agent, workspace, monitor); their owners answer for them.
    return MetricDetail(
        **view.model_dump(),
        versions=[MetricVersionView.model_validate(v) for v in versions],
        usage=MetricUsage(data_product_ids=list(products), knowledge_page_ids=list(pages)),
    )


@router.post(
    "/v1/metrics/{id}/versions",
    status_code=201,
    response_model=MetricVersionView,
    **endpoint("POST", "/v1/metrics/{id}/versions", operation_id="createMetricVersion"),
)
async def create_metric_version(
    body: MetricVersionCreate,
    metric_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> MetricVersionView:
    async with transaction(ROLE, principal) as session:
        metric = await _metric(session, metric_id, principal, data_space, lock=True)
        if metric.status == "retired":
            raise Problem(409, "invalid_state", "The metric is retired.")
        if metric.pack_metric_id is not None:
            # UC-07-01 2a: a pack metric's formula is locked; changes are overrides (UC-09-03).
            raise Problem(
                422,
                "business_rule_violated",
                "This metric comes from an industry pack: its formula is locked; create an override instead (UC-09-03).",
            )
        await _check_dimensions(session, body, data_space)
        latest = await session.scalar(
            select(func.max(context.MetricVersion.version)).where(
                context.MetricVersion.metric_id == metric.id
            )
        )
        # BR-26: a change of formula is a new version; the certified one stays as it is.
        row = _version_row(principal, metric, (latest or 0) + 1, body)
        session.add(row)
        metric.updated_by, metric.updated_at = principal.user_id, now()
        emit(
            session,
            principal,
            "metric_version",
            row.id,
            "metric_version.created",
            metric_id=metric.id,
            version_id=row.id,
            version=row.version,
            data_space_id=data_space,
            status=row.status,
        )
        await session.flush()
        await session.refresh(row)
        return MetricVersionView.model_validate(row)


@router.post(
    "/v1/metric-versions/{id}:preview",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/metric-versions/{id}:preview", operation_id="previewMetricVersion"),
)
async def preview_metric_version(
    version_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
    body: Annotated[MetricPreview, Body()] = _NO_PREVIEW,
) -> dict[str, Any]:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(context.MetricVersion)
            .join(context.Metric, context.Metric.id == context.MetricVersion.metric_id)
            .where(
                context.MetricVersion.id == version_id,
                context.Metric.org_id == principal.org_id,
                context.Metric.data_space_id == data_space,
            )
            .with_for_update(of=context.MetricVersion)
        )
        if row is None:
            raise Problem(404, "not_found", "Metric version not found.")
        if row.status not in PREVIEWABLE:
            raise Problem(409, "invalid_state", f"A {row.status} version is not previewed.")
        # eval.eval_run is the eval module's: the id is reserved here and the semantic compiler
        # and evaluation create and fill the run from the event (UC-07-01 step 3).
        eval_run_id = uuid7()
        previous, row.status = row.status, "testing"
        emit(
            session,
            principal,
            "metric_version",
            row.id,
            "metric_version.preview_requested",
            metric_id=row.metric_id,
            version_id=row.id,
            data_space_id=data_space,
            eval_run_id=eval_run_id,
            periods=body.periods or PREVIEW_PERIODS,
            previous_status=previous,
            status=row.status,
        )
    return accepted(response, f"/v1/eval-runs/{eval_run_id}", eval_run_id, "queued")


# ---------------------------------------------------------------- relationships (UC-07-02)


@router.post(
    "/v1/relationships",
    status_code=201,
    response_model=RelationshipView,
    **endpoint("POST", "/v1/relationships", operation_id="createRelationship"),
)
async def create_relationship(
    body: RelationshipCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> RelationshipView:
    async with transaction(ROLE, principal) as session:
        wanted = {body.from_dataset_id, body.to_dataset_id}
        if wanted - await _datasets_in_space(session, wanted, data_space):
            raise field_error("from_dataset_id", "Both datasets must belong to this Data Space.")
        # BR-100: a declared relationship waits for review before the compiler joins on it.
        row = context.Relationship(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            from_dataset_id=body.from_dataset_id,
            from_columns=body.from_columns,
            to_dataset_id=body.to_dataset_id,
            to_columns=body.to_columns,
            cardinality=body.cardinality,
            origin="declared",
            status="pending_review",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        emit(
            session,
            principal,
            "relationship",
            row.id,
            "relationship.created",
            relationship_id=row.id,
            data_space_id=data_space,
            status=row.status,
        )
        await session.flush()
        await session.refresh(row)
        return RelationshipView.model_validate(row)


# ---------------------------------------------------------------- synonyms (UC-07-02, UC-07-09)

_TARGETS: dict[str, Any] = {
    "metric": context.Metric,
    "dimension": context.SemanticDimension,
    "value": context.SemanticDimension,
    "measure": context.SemanticMeasure,
    "entity": context.SemanticEntity,
}


async def _target_exists(
    session: AsyncSession, kind: str, target_id: uuid.UUID, data_space: uuid.UUID
) -> bool:
    if kind == "column":
        # Columns are catalog rows; svc_context reads them through catalog_read.
        return bool(
            await session.scalar(
                select(
                    exists().where(
                        catalog.ColumnMeta.id == target_id,
                        catalog.ColumnMeta.dataset_id == catalog.Dataset.id,
                        catalog.Dataset.data_space_id == data_space,
                    )
                )
            )
        )
    model = _TARGETS[kind]
    return bool(
        await session.scalar(
            select(exists().where(model.id == target_id, model.data_space_id == data_space))
        )
    )


async def _make_ambiguous(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    body: SynonymCreate,
    norm: str,
    existing: context.Synonym,
) -> uuid.UUID:
    """UC-07-02 E1: the phrase already means something else. Nothing is overwritten; the
    glossary term of that phrase holds both meanings and goes back to review (UC-07-09)."""
    meaning_new = {
        "target_kind": body.target_kind,
        "target_id": str(body.target_id),
        "target_value": body.target_value,
    }
    meaning_old = {
        "target_kind": existing.target_kind,
        "target_id": str(existing.target_id),
        "target_value": existing.target_value,
    }
    term = await session.scalar(
        select(context.GlossaryTerm)
        .where(context.GlossaryTerm.data_space_id == data_space, context.GlossaryTerm.term_norm == norm)
        .with_for_update()
    )
    if term is None:
        term = context.GlossaryTerm(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            term=body.phrase,
            term_norm=norm,
            definition=body.phrase,
            meanings=[meaning_old, meaning_new],
            default_meaning=0,
            status="pending_review",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(term)
        event = "glossary_term.created"
    else:
        meanings = list(term.meanings or [])
        known = {(m.get("target_kind"), m.get("target_id")) for m in meanings if isinstance(m, dict)}
        for meaning in (meaning_old, meaning_new):
            if (meaning["target_kind"], meaning["target_id"]) not in known:
                meanings.append(meaning)
        term.meanings = meanings
        if term.default_meaning is None:
            term.default_meaning = 0
        term.status = "pending_review"
        term.updated_by, term.updated_at = principal.user_id, now()
        event = "glossary_term.changed"
    emit(
        session,
        principal,
        "glossary_term",
        term.id,
        event,
        glossary_term_id=term.id,
        data_space_id=data_space,
        status=term.status,
        ambiguous=True,
    )
    return term.id


@router.post(
    "/v1/synonyms",
    status_code=201,
    response_model=SynonymView,
    **endpoint("POST", "/v1/synonyms", operation_id="createSynonym"),
)
async def create_synonym(
    body: SynonymCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> SynonymView:
    norm = normalize(body.phrase)
    if not norm:
        raise field_error("phrase", "phrase is empty once normalised.")
    async with transaction(ROLE, principal) as session:
        if not await _target_exists(session, body.target_kind, body.target_id, data_space):
            raise field_error("target_id", "target_id names nothing of that kind in this Data Space.")
        same = await session.scalar(
            select(context.Synonym).where(
                context.Synonym.data_space_id == data_space,
                context.Synonym.phrase_norm == norm,
                context.Synonym.target_kind == body.target_kind,
                context.Synonym.target_id == body.target_id,
                context.Synonym.status != "rejected",
            )
        )
        if same is not None:
            raise Problem(409, "conflict", "This phrase already points at that target.")
        other = await session.scalar(
            select(context.Synonym)
            .where(
                context.Synonym.data_space_id == data_space,
                context.Synonym.phrase_norm == norm,
                context.Synonym.status == "active",
            )
            .limit(1)
        )
        row = context.Synonym(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            phrase=body.phrase,
            phrase_norm=norm,
            target_kind=body.target_kind,
            target_id=body.target_id,
            target_value=body.target_value,
            origin="manual",
            status="pending_review",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        term_id = (
            await _make_ambiguous(session, principal, data_space, body, norm, other)
            if other is not None
            else None
        )
        emit(
            session,
            principal,
            "synonym",
            row.id,
            "synonym.created",
            synonym_id=row.id,
            data_space_id=data_space,
            status=row.status,
            glossary_term_id=term_id,
        )
        await session.flush()
        await session.refresh(row)
        view = SynonymView.model_validate(row)
        view.glossary_term_id = term_id
        return view


# ---------------------------------------------------------------- examples (UC-07-02)

# A sample query is read-only: one SELECT (or WITH … SELECT) statement. Whether it runs and
# matches its metrics is the checker's job before review (UC-07-02 step 2).
_READ_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_WRITES = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|call)\b",
    re.IGNORECASE,
)


@router.post(
    "/v1/examples",
    status_code=201,
    response_model=ExampleView,
    **endpoint("POST", "/v1/examples", operation_id="createExample"),
)
async def create_example(
    body: ExampleCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ExampleView:
    sql = body.sql.strip().rstrip(";").strip()
    if not _READ_ONLY.match(sql) or ";" in sql or _WRITES.search(sql):
        raise field_error("sql", "sql must be one read-only SELECT statement.")
    metric_ids = list(dict.fromkeys(body.metric_ids or []))
    async with transaction(ROLE, principal) as session:
        if metric_ids:
            found = set(
                (
                    await session.scalars(
                        select(context.Metric.id).where(
                            context.Metric.id.in_(metric_ids),
                            context.Metric.data_space_id == data_space,
                        )
                    )
                ).all()
            )
            if set(metric_ids) - found:
                raise field_error("metric_ids", "metric_ids names a metric outside this Data Space.")
        # BR-118: an example is a model sample only once reviewed and still running; it starts
        # as a draft the checker runs before it goes to review (AC-07-02-02).
        row = context.ExampleQuery(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            question=body.question,
            sql=sql,
            metric_ids=metric_ids,
            origin="manual",
            status="draft",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        emit(
            session,
            principal,
            "example_query",
            row.id,
            "example_query.created",
            example_query_id=row.id,
            data_space_id=data_space,
            status=row.status,
        )
        await session.flush()
        await session.refresh(row)
        return ExampleView.model_validate(row)


# ---------------------------------------------------------------- glossary (UC-07-09)


@router.get(
    "/v1/terms",
    response_model=Page[TermView],
    **endpoint("GET", "/v1/terms", operation_id="listTerms"),
)
async def list_terms(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: ContextPage,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status: str | None = None,
) -> Page[TermView]:
    term = context.GlossaryTerm
    query = select(term).where(term.org_id == principal.org_id, term.data_space_id == data_space)
    if status:
        query = query.where(term.status == status)
    if q:
        query = query.where(
            or_(term.term_norm.contains(normalize(q)), term.definition.ilike(f"%{q}%"))
        )
    async with transaction(ROLE, principal) as session:
        items, token = await keyset_page(session, query, term, page, TermView.model_validate)
    return Page(items=items, next_page_token=token)


@router.post(
    "/v1/terms",
    status_code=201,
    response_model=TermView,
    **endpoint("POST", "/v1/terms", operation_id="createTerm"),
)
async def create_term(
    body: TermCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> TermView:
    norm = normalize(body.term)
    if not norm:
        raise field_error("term", "term is empty once normalised.")
    async with transaction(ROLE, principal) as session:
        row = context.GlossaryTerm(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            term=body.term,
            term_norm=norm,
            definition=body.definition,
            meanings=body.meanings or [],
            default_meaning=body.default_meaning,
            legal_basis=body.legal_basis,
            status="pending_review",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        emit(
            session,
            principal,
            "glossary_term",
            row.id,
            "glossary_term.created",
            glossary_term_id=row.id,
            data_space_id=data_space,
            status=row.status,
        )
        await session.flush()
        await session.refresh(row)
        return TermView.model_validate(row)


@router.get(
    "/v1/unmapped-phrases",
    response_model=Page[UnmappedPhraseView],
    **endpoint("GET", "/v1/unmapped-phrases", operation_id="listUnmappedPhrases"),
)
async def list_unmapped_phrases(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: ContextPage,
    status: str | None = None,
    min_count: Annotated[int | None, Query(ge=1)] = None,
) -> Page[UnmappedPhraseView]:
    phrase = context.UnmappedPhrase
    # The most asked first (UC-07-09 step 3); open ones unless another status is asked for.
    query = select(phrase).where(
        phrase.org_id == principal.org_id,
        phrase.data_space_id == data_space,
        phrase.status == (status or "open"),
    )
    if min_count is not None:
        query = query.where(phrase.ask_count >= min_count)
    if page.cursor:
        try:
            after = (int(page.cursor["ask_count"]), uuid.UUID(page.cursor["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(
            or_(
                phrase.ask_count < after[0],
                (phrase.ask_count == after[0]) & (phrase.id < after[1]),
            )
        )
    query = query.order_by(phrase.ask_count.desc(), phrase.id.desc()).limit(page.page_size + 1)
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    token = {"ask_count": rows[-1].ask_count, "id": str(rows[-1].id)} if more else None
    return Page(
        items=[UnmappedPhraseView.model_validate(r) for r in rows],
        next_page_token=encode_page_token(token),
    )


@router.put(
    "/v1/me/term-preferences",
    response_model=list[UserTermPrefView],
    **endpoint("PUT", "/v1/me/term-preferences", operation_id="putTermPreferences"),
)
async def put_term_preferences(
    body: TermPreferencesPut, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> list[UserTermPrefView]:
    """Replaces the caller's personal readings in this Data Space (UC-07-09 4a). They apply to
    the caller's own questions only, never to published reports, closed periods or shared
    answers (BR-123): the planner reads them, nothing else does."""
    wanted: dict[uuid.UUID, int] = {}
    for pref in body.preferences:
        if pref.glossary_term_id in wanted:
            raise field_error("preferences", "A term appears twice in preferences.")
        wanted[pref.glossary_term_id] = pref.meaning_index
    async with transaction(ROLE, principal) as session:
        terms = {
            t.id: t
            for t in (
                await session.scalars(
                    select(context.GlossaryTerm).where(
                        context.GlossaryTerm.id.in_(list(wanted)),
                        context.GlossaryTerm.data_space_id == data_space,
                        context.GlossaryTerm.status == "active",
                    )
                )
            ).all()
        }
        for term_id, index in wanted.items():
            term = terms.get(term_id)
            if term is None:
                raise field_error(
                    "preferences", "A preference names a term that is not active in this Data Space."
                )
            if index >= len(term.meanings or []):
                raise field_error("preferences", "meaning_index is not a meaning of that term.")
        await session.execute(
            delete(context.UserTermPref).where(
                context.UserTermPref.user_id == principal.user_id,
                context.UserTermPref.data_space_id == data_space,
            )
        )
        rows = [
            context.UserTermPref(
                id=uuid7(),
                org_id=principal.org_id,
                data_space_id=data_space,
                user_id=principal.user_id,
                glossary_term_id=term_id,
                meaning_index=index,
                created_by=principal.user_id,
                updated_by=principal.user_id,
            )
            for term_id, index in wanted.items()
        ]
        session.add_all(rows)
        emit(
            session,
            principal,
            "user_term_pref",
            principal.user_id,
            "user_term_pref.replaced",
            user_id=principal.user_id,
            data_space_id=data_space,
            glossary_term_ids=list(wanted),
        )
        await session.flush()
        for row in rows:
            await session.refresh(row)
        return [UserTermPrefView.model_validate(r) for r in rows]
