"""Semantic layer: metrics and their versions (UC-07-01), the entity model, relationships, synonyms
and examples (UC-07-02), the business glossary and personal readings (UC-07-09).
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
from sqlalchemy import delete, exists, func, literal, or_, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

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
    DatasetRef,
    EntityDetail,
    EntityDimension,
    EntityMeasure,
    EntityMetric,
    EntityRelationship,
    EntityView,
    ExampleCreate,
    ExamplePatch,
    ExampleView,
    MetricCreate,
    MetricDetail,
    MetricPatch,
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


async def _metric_views(session: AsyncSession, rows: list[context.Metric]) -> list[MetricView]:
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
    query = select(metric).where(
        metric.org_id == principal.org_id, metric.data_space_id == data_space
    )
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
        valid_from=now(),
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
            # metric_version has no author column: the worker reads it here (maker-checker).
            created_by=principal.user_id,
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


@router.patch(
    "/v1/metrics/{id}",
    response_model=MetricView,
    **endpoint("PATCH", "/v1/metrics/{id}", operation_id="updateMetric"),
)
async def update_metric(
    body: MetricPatch,
    metric_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> MetricView:
    async with transaction(ROLE, principal) as session:
        row = await _metric(session, metric_id, principal, data_space, lock=True)
        if body.code is not None and body.code != row.code:
            raise Problem(409, "immutable", "A metric code cannot change after creation.")
        if body.consumer_group is not None:
            row.consumer_group = body.consumer_group
        row.updated_by, row.updated_at = principal.user_id, now()
        emit(
            session,
            principal,
            "metric",
            row.id,
            "metric.updated",
            metric_id=row.id,
            data_space_id=data_space,
            changed=list(body.model_dump(exclude_unset=True)),
        )
        await session.flush()
        return (await _metric_views(session, [row]))[0]


@router.delete(
    "/v1/metrics/{id}",
    status_code=204,
    **endpoint("DELETE", "/v1/metrics/{id}", operation_id="deleteMetric"),
)
async def delete_metric(
    metric_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> Response:
    async with transaction(ROLE, principal) as session:
        row = await _metric(session, metric_id, principal, data_space, lock=True)
        if row.in_filed_report:
            raise Problem(
                409,
                "in_use",
                "A metric used by a filed report cannot be deleted; retire it instead.",
            )
        if row.status == "retired":
            raise Problem(409, "invalid_state", "The metric is already retired.")
        row.status = "retired"
        row.updated_by, row.updated_at = principal.user_id, now()
        emit(
            session,
            principal,
            "metric",
            row.id,
            "metric.retired",
            metric_id=row.id,
            data_space_id=data_space,
        )
    return Response(status_code=204)


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
            created_by=principal.user_id,
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
            # The worker opens the metric_change proposal as the steward who asked (maker-checker).
            requested_by=principal.user_id,
        )
    return accepted(response, f"/v1/eval-runs/{eval_run_id}", eval_run_id, "queued")


# ---------------------------------------------------------------- entity model (UC-07-02)


def _dataset_name(row: catalog.Dataset) -> str:
    return row.display_name or f"{row.schema_name}.{row.table_name}"


async def _entity_views(
    session: AsyncSession, rows: list[context.SemanticEntity], data_space: uuid.UUID
) -> list[EntityView]:
    """Each entity with its primary table and the relationships of that table, the other end
    named by the entity declared on it (svc_context reads catalog through catalog_read)."""
    if not rows:
        return []
    tables = {r.primary_dataset_id for r in rows}
    relationships = (
        await session.scalars(
            select(context.Relationship)
            .where(
                context.Relationship.data_space_id == data_space,
                context.Relationship.status != "rejected",
                or_(
                    context.Relationship.from_dataset_id.in_(tables),
                    context.Relationship.to_dataset_id.in_(tables),
                ),
            )
            .order_by(context.Relationship.created_at, context.Relationship.id)
        )
    ).all()
    touched = tables | {r.from_dataset_id for r in relationships}
    touched |= {r.to_dataset_id for r in relationships}
    entity_of = dict(
        (
            await session.execute(
                select(context.SemanticEntity.primary_dataset_id, context.SemanticEntity.code)
                .where(
                    context.SemanticEntity.data_space_id == data_space,
                    context.SemanticEntity.primary_dataset_id.in_(touched),
                    context.SemanticEntity.status != "retired",
                )
                .order_by(context.SemanticEntity.created_at)
            )
        )
        .tuples()
        .all()
    )
    datasets = {
        d.id: d
        for d in (
            await session.scalars(
                select(catalog.Dataset).where(
                    catalog.Dataset.id.in_(tables),
                    catalog.Dataset.data_space_id == data_space,
                    catalog.Dataset.deleted_at.is_(None),
                )
            )
        ).all()
    }
    views = []
    for row in rows:
        view = EntityView.model_validate(row)
        table = datasets.get(row.primary_dataset_id)
        view.primary_dataset = DatasetRef(id=table.id, name=_dataset_name(table)) if table else None
        view.relationships = [
            EntityRelationship(
                id=r.id,
                from_dataset_id=r.from_dataset_id,
                from_columns=r.from_columns,
                from_entity=entity_of.get(r.from_dataset_id),
                to_dataset_id=r.to_dataset_id,
                to_columns=r.to_columns,
                to_entity=entity_of.get(r.to_dataset_id),
                cardinality=r.cardinality,
                origin=r.origin,
                status=r.status,
            )
            for r in relationships
            if row.primary_dataset_id in (r.from_dataset_id, r.to_dataset_id)
        ]
        views.append(view)
    return views


@router.get(
    "/v1/entities",
    response_model=Page[EntityView],
    **endpoint("GET", "/v1/entities", operation_id="listEntities"),
)
async def list_entities(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: ContextPage,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status: Annotated[str | None, Query(max_length=40)] = None,
) -> Page[EntityView]:
    entity = context.SemanticEntity
    query = select(entity).where(
        entity.org_id == principal.org_id, entity.data_space_id == data_space
    )
    if status:
        query = query.where(entity.status == status)
    if q:
        query = query.where(or_(entity.code.ilike(f"%{q}%"), entity.name.ilike(f"%{q}%")))
    async with transaction(ROLE, principal) as session:
        rows, token = await keyset_page(session, query, entity, page, lambda r: r)
        items = await _entity_views(session, rows, data_space)
    return Page(items=items, next_page_token=token)


@router.get(
    "/v1/entities/{key}",
    response_model=EntityDetail,
    **endpoint("GET", "/v1/entities/{key}", operation_id="getEntity"),
)
async def get_entity(
    key: Annotated[str, Path(max_length=200)],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> EntityDetail:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(context.SemanticEntity).where(
                context.SemanticEntity.org_id == principal.org_id,
                context.SemanticEntity.data_space_id == data_space,
                context.SemanticEntity.code == key,
            )
        )
        if row is None:
            raise Problem(404, "not_found", "Entity not found.")
        (view,) = await _entity_views(session, [row], data_space)
        dimensions = (
            await session.scalars(
                select(context.SemanticDimension)
                .where(context.SemanticDimension.entity_id == row.id)
                .order_by(context.SemanticDimension.code)
            )
        ).all()
        measures = (
            await session.scalars(
                select(context.SemanticMeasure)
                .where(context.SemanticMeasure.entity_id == row.id)
                .order_by(context.SemanticMeasure.code)
            )
        ).all()
        metric, version = context.Metric, context.MetricVersion
        uses = [metric.canonical_dataset_id == row.primary_dataset_id]
        if dimensions:
            # A metric of another table that is broken down by one of the entity's dimensions
            # (allowed_dimensions of its current version) uses the entity too.
            # Its own alias: the outer query joins metric_version too, and would correlate it away.
            current = aliased(context.MetricVersion)
            uses.append(
                exists().where(
                    current.metric_id == metric.id,
                    current.version == metric.current_version,
                    or_(
                        *(
                            literal(d.id, UUID(as_uuid=True))
                            == func.any(current.allowed_dimensions)
                            for d in dimensions
                        )
                    ),
                )
            )
        metrics = (
            await session.execute(
                select(metric.id, metric.code, version.display_name, metric.status)
                .outerjoin(
                    version,
                    (version.metric_id == metric.id) & (version.version == metric.current_version),
                )
                .where(
                    metric.org_id == principal.org_id,
                    metric.data_space_id == data_space,
                    metric.status != "retired",
                    or_(*uses),
                )
                .order_by(metric.code)
            )
        ).all()
    return EntityDetail(
        **view.model_dump(),
        dimensions=[EntityDimension.model_validate(d, from_attributes=True) for d in dimensions],
        measures=[EntityMeasure.model_validate(m, from_attributes=True) for m in measures],
        metrics=[
            EntityMetric(id=m_id, code=code, display_name=name, status=status)
            for m_id, code, name, status in metrics
        ],
    )


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
        .where(
            context.GlossaryTerm.data_space_id == data_space, context.GlossaryTerm.term_norm == norm
        )
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
        known = {
            (m.get("target_kind"), m.get("target_id")) for m in meanings if isinstance(m, dict)
        }
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


@router.get(
    "/v1/synonyms",
    response_model=Page[SynonymView],
    **endpoint("GET", "/v1/synonyms", operation_id="listSynonyms"),
)
async def list_synonyms(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: ContextPage,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status: Annotated[str | None, Query(max_length=40)] = None,
    target_kind: Annotated[str | None, Query(max_length=40)] = None,
    target_id: uuid.UUID | None = None,
) -> Page[SynonymView]:
    synonym = context.Synonym
    query = select(synonym).where(
        synonym.org_id == principal.org_id, synonym.data_space_id == data_space
    )
    for column, value in (
        (synonym.status, status),
        (synonym.target_kind, target_kind),
        (synonym.target_id, target_id),
    ):
        if value is not None:
            query = query.where(column == value)
    if q:
        # Matched on the normalised phrase, as the planner matches a question (D4.5).
        query = query.where(synonym.phrase_norm.contains(normalize(q)))
    async with transaction(ROLE, principal) as session:
        items, token = await keyset_page(session, query, synonym, page, SynonymView.model_validate)
    return Page(items=items, next_page_token=token)


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
            raise field_error(
                "target_id", "target_id names nothing of that kind in this Data Space."
            )
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


def _read_only_sql(sql: str) -> str:
    """The statement without its trailing semicolon; 422 unless it is one read-only SELECT
    (BR-22)."""
    sql = sql.strip().rstrip(";").strip()
    if not _READ_ONLY.match(sql) or ";" in sql or _WRITES.search(sql):
        raise field_error("sql", "sql must be one read-only SELECT statement.")
    return sql


@router.get(
    "/v1/examples",
    response_model=Page[ExampleView],
    **endpoint("GET", "/v1/examples", operation_id="listExamples"),
)
async def list_examples(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: ContextPage,
    status: Annotated[str | None, Query(max_length=40)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    origin: Annotated[str | None, Query(max_length=40)] = None,
) -> Page[ExampleView]:
    example = context.ExampleQuery
    query = select(example).where(
        example.org_id == principal.org_id, example.data_space_id == data_space
    )
    for column, value in ((example.status, status), (example.origin, origin)):
        if value is not None:
            query = query.where(column == value)
    if q:
        query = query.where(example.question.ilike(f"%{q}%"))
    async with transaction(ROLE, principal) as session:
        items, token = await keyset_page(session, query, example, page, ExampleView.model_validate)
    return Page(items=items, next_page_token=token)


@router.post(
    "/v1/examples",
    status_code=201,
    response_model=ExampleView,
    **endpoint("POST", "/v1/examples", operation_id="createExample"),
)
async def create_example(
    body: ExampleCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> ExampleView:
    sql = _read_only_sql(body.sql)
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
                raise field_error(
                    "metric_ids", "metric_ids names a metric outside this Data Space."
                )
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


# A confirmed example is one the checker has run (pending_review) or one already confirmed; a
# draft has not been run yet and a stale one no longer runs (BR-118).
_CONFIRMABLE = ("pending_review", "active")


@router.patch(
    "/v1/examples/{id}",
    response_model=ExampleView,
    **endpoint("PATCH", "/v1/examples/{id}", operation_id="updateExample"),
)
async def update_example(
    body: ExamplePatch,
    example_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> ExampleView:
    changes = body.model_dump(exclude_unset=True)
    for name in ("question", "sql", "status"):
        if name in changes and changes[name] is None:
            raise field_error(name, f"{name} cannot be null.")
    sql = _read_only_sql(body.sql) if body.sql is not None else None
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(context.ExampleQuery)
            .where(
                context.ExampleQuery.id == example_id,
                context.ExampleQuery.org_id == principal.org_id,
                context.ExampleQuery.data_space_id == data_space,
            )
            .with_for_update()
        )
        if row is None:
            raise Problem(404, "not_found", "Example not found.")
        previous = row.status
        rerun = sql is not None and sql != row.sql
        if rerun and body.status == "active":
            # The api has no warehouse: a new SQL is run by the checker first, then confirmed.
            raise field_error("status", "A changed SQL runs again before it is confirmed.")
        if body.status == "active" and row.status not in _CONFIRMABLE:
            raise Problem(
                409,
                "invalid_state",
                f"The example is {row.status}; it is confirmed once the checker has run its SQL.",
            )
        if body.question is not None:
            row.question = body.question
        if sql is not None and rerun:
            # Out of the standard set until the checker has run the new SQL (BR-118): it goes
            # back to draft, as a new example does, and the checker acts on the event below.
            row.sql, row.status, row.last_verified_at = sql, "draft", None
        if body.status is not None:
            row.status = body.status
        row.updated_by, row.updated_at = principal.user_id, now()
        emit(
            session,
            principal,
            "example_query",
            row.id,
            "example_query.updated",
            example_query_id=row.id,
            data_space_id=data_space,
            changed=sorted(changes),
            previous_status=previous,
            status=row.status,
            rerun_sql=rerun,
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
                    "preferences",
                    "A preference names a term that is not active in this Data Space.",
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
