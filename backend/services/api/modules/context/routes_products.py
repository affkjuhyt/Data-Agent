"""Data products (UC-07-13, BR-175, BR-178): the scope of questions an organisation commits to
answer, with named owner and steward and a freshness commitment.

The database holds what it can see (backend/schema/csdl/routines/context.sql): activation needs a
certified metric, a primary dataset with a commitment and point-in-time datasets; the code is
frozen once out of draft; an active product takes no primary-member removal; classification and
the context.data_product.changed event follow every write. The routes add the checks that need the
caller or another schema, and the state machine around them.

Effects on other schemas are events with ids reserved here (D1.2): the eval set at creation, the
eval run of :submit (eval creates and scores it, then opens the data_product proposal), and the
proposal of a change to an active product, whose old version keeps serving until it is decided
(UC-07-13 6a).

POST /v1/data-products is one operation for both layers (docs/v3/06-api-v1.md §1): `layer`
context is this table, `published` the P06 shared product, written by the pipeline module.
"""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, Path, Query, Request, Response
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import catalog, context, document, org, pack
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import Page
from services.api.kernel.problems import FieldError, Problem
from services.api.modules.context.common import (
    ROLE,
    ContextPage,
    accepted,
    audit,
    emit,
    field_error,
    keyset_page,
    now,
    require_active_users,
)
from services.api.modules.context.schemas import (
    CONTEXT_REQUIRED,
    PUBLISHED_ONLY,
    DataProductCreate,
    DataProductDetail,
    DataProductPatch,
    DataProductScope,
    DataProductSubmitted,
    DataProductView,
    DatasetFreshness,
    FreshnessReport,
    MemberCreate,
    MemberView,
    PeriodState,
    ProductUseRequestBody,
    ProductUseRequestView,
    PublishedCatalogProduct,
    PublishedDataProductView,
    ReasonBody,
    ScopeMatch,
    SubmitBody,
)
from services.api.modules.eval import data_product_eval_summary
from services.api.modules.pipeline import (
    create_published_product,
    published_products,
    shared_product_usable,
)

router = APIRouter()

ProductId = Annotated[uuid.UUID, Path(alias="id")]
MIN_SAMPLE_QUESTIONS = 10
MAX_GRACE = datetime.timedelta(days=1)
MAX_PERIODS = 24
# Worst first: the product's state is that of its worst primary dataset.
_STATE_ORDER = ("missing", "overdue", "unknown", "fresh")
_EDITABLE = ("draft",)
# A served product changes only through a new version that goes through review.
_VERSIONED = ("active", "suspended")
_NO_SUBMIT = SubmitBody()


# ---------------------------------------------------------------- helpers


async def _product(
    session: AsyncSession,
    product_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> context.DataProduct:
    query = select(context.DataProduct).where(
        context.DataProduct.id == product_id,
        context.DataProduct.org_id == principal.org_id,
        context.DataProduct.data_space_id == data_space,
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Data product not found.")
    return row


async def _members(session: AsyncSession, product_id: uuid.UUID) -> list[context.DataProductMember]:
    return list(
        (
            await session.scalars(
                select(context.DataProductMember)
                .where(context.DataProductMember.data_product_id == product_id)
                .order_by(context.DataProductMember.created_at, context.DataProductMember.id)
            )
        ).all()
    )


def _worst(states: list[str]) -> str:
    if not states:
        return "unknown"
    return min(states, key=lambda s: _STATE_ORDER.index(s) if s in _STATE_ORDER else 2)


async def _overall_freshness(
    session: AsyncSession, product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not product_ids:
        return {}
    m = context.DataProductMember
    rows = (
        await session.execute(
            select(m.data_product_id, m.freshness_state).where(
                m.data_product_id.in_(product_ids),
                m.member_kind == "dataset",
                m.role == "primary",
            )
        )
    ).all()
    states: dict[uuid.UUID, list[str]] = {}
    for product_id, state in rows:
        states.setdefault(product_id, []).append(state)
    return {pid: _worst(states.get(pid, [])) for pid in product_ids}


def _view(row: context.DataProduct, freshness: str = "unknown") -> DataProductView:
    view = DataProductView.model_validate(row)
    view.freshness_state = freshness
    return view


def _rule(detail: str) -> Problem:
    return Problem(422, "business_rule_violated", detail)


def _distinct_people(
    owner_id: uuid.UUID | None, steward_id: uuid.UUID | None, creator: uuid.UUID | None
) -> None:
    if owner_id is not None and owner_id == steward_id:
        raise field_error("steward_id", "Owner and steward are two different people (BR-175).")
    # The owner approves activation and the approver is not the creator (BR-175): a creator
    # naming themselves owner would leave the product with no one able to activate it.
    if owner_id is not None and owner_id == creator:
        raise field_error("owner_id", "The owner approves activation, so is not the creator.")


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


async def variant_guard(request: Request, principal: CurrentPrincipal) -> None:
    """The capability of the layer asked for: steward for the P07 context product, admin_data
    for the P06 published one. The route gate admits either, so this is what keeps one layer's
    capability from opening the other (catalogue.endpoint)."""
    try:
        body = await request.json()
    except ValueError:
        return  # the body model reports it
    layer = body.get("layer", "context") if isinstance(body, dict) else "context"
    needed = {"context": "steward", "published": "admin_data"}.get(layer)
    if needed is None:
        raise Problem(
            422,
            "validation_failed",
            "layer is context or published.",
            errors=[FieldError(field="layer", code="invalid", message="context or published")],
        )
    if needed not in principal.capabilities:
        raise Problem(403, "forbidden", f"Missing capability: {needed}.")


# ---------------------------------------------------------------- scope catalogue (G5.16)


def _dimension_filters(values: list[str] | None) -> list[tuple[str, str]]:
    out = []
    for item in values or []:
        dimension, sep, value = item.partition(":")
        if not sep or not dimension or not value:
            raise field_error("dimension_values", "Each value is dimension:value.")
        out.append((dimension, value))
    return out


def _match(
    scope: DataProductScope,
    metric_codes: list[str],
    dimensions: list[tuple[str, str]],
    period: datetime.date | None,
) -> ScopeMatch:
    """In scope when every metric, entity and period asked for is inside the product (BR-175)."""
    reasons = [
        f"metric {c} is not in the product" for c in metric_codes if c not in scope.metric_codes
    ]
    entities = {e["dimension"]: e.get("values") or [] for e in scope.scope_entities or []}
    for dimension, value in dimensions:
        if dimension not in entities:
            reasons.append(f"dimension {dimension} is not in the product")
        elif entities[dimension] and value not in entities[dimension]:
            reasons.append(f"{dimension}={value} is outside the product")
    outside = period is not None and (
        period < scope.period_from or (scope.period_to is not None and period > scope.period_to)
    )
    if outside and period is not None:
        reasons.append(f"period {period.isoformat()} is outside the product")
    return ScopeMatch(data_product_id=scope.id, in_scope=not reasons, reasons=reasons)


@router.get(
    "/v1/data-products/index",
    response_model=list[DataProductScope],
    **endpoint("GET", "/v1/data-products/index", operation_id="getDataProductIndex"),
)
async def data_product_index(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    metric_codes: Annotated[list[str] | None, Query(max_length=50)] = None,
    dimension_values: Annotated[
        list[str] | None, Query(max_length=50, description="dimension:value")
    ] = None,
    period: datetime.date | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> list[DataProductScope]:
    dimensions = _dimension_filters(dimension_values)
    dp = context.DataProduct
    query = select(dp).where(
        dp.org_id == principal.org_id, dp.data_space_id == data_space, dp.status == "active"
    )
    if q:
        query = query.where(or_(dp.name.ilike(f"%{q}%"), dp.description.ilike(f"%{q}%")))
    async with transaction(ROLE, principal) as session:
        products = list((await session.scalars(query.order_by(dp.code))).all())
        codes: dict[uuid.UUID, list[str]] = {p.id: [] for p in products}
        if products:
            m = context.DataProductMember
            rows = (
                await session.execute(
                    select(m.data_product_id, context.Metric.code)
                    .join(context.Metric, context.Metric.id == m.member_ref)
                    .where(m.data_product_id.in_(codes), m.member_kind == "metric")
                    .order_by(context.Metric.code)
                )
            ).all()
            for product_id, code in rows:
                codes[product_id].append(code)
    out = []
    for p in products:
        scope = DataProductScope(
            id=p.id,
            code=p.code,
            name=p.name,
            version=p.version,
            metric_codes=codes[p.id],
            scope_entities=p.scope_entities,
            period_grain=p.period_grain,
            period_from=p.period_from,
            period_to=p.period_to,
            out_of_scope_note=p.out_of_scope_note,
            freshness_statement=p.freshness_statement,
            classification=p.classification,
        )
        if metric_codes:
            scope.match = _match(scope, metric_codes, dimensions, period)
        out.append(scope)
    return out


# ---------------------------------------------------------------- products


@router.get(
    "/v1/data-products",
    response_model=Page[DataProductView],
    **endpoint("GET", "/v1/data-products", operation_id="listDataProducts"),
)
async def list_data_products(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: ContextPage,
    status: str | None = None,
    owner_id: uuid.UUID | None = None,
    steward_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> Page[DataProductView]:
    dp = context.DataProduct
    query = select(dp).where(dp.org_id == principal.org_id, dp.data_space_id == data_space)
    if status:
        query = query.where(dp.status == status)
    if owner_id:
        query = query.where(dp.owner_id == owner_id)
    if steward_id:
        query = query.where(dp.steward_id == steward_id)
    if q:
        query = query.where(
            or_(dp.name.ilike(f"%{q}%"), dp.code.ilike(f"%{q}%"), dp.description.ilike(f"%{q}%"))
        )
    async with transaction(ROLE, principal) as session:
        items, token = await keyset_page(session, query, dp, page, _view)
        states = await _overall_freshness(session, [i.id for i in items])
    for item in items:
        item.freshness_state = states.get(item.id, "unknown")
    return Page[DataProductView](items=items, next_page_token=token)


@router.get(
    "/v1/data-products/catalog",
    response_model=Page[PublishedCatalogProduct],
    **endpoint("GET", "/v1/data-products/catalog", operation_id="listPublishedProductCatalog"),
)
async def list_published_product_catalog(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: ContextPage,
) -> Page[PublishedCatalogProduct]:
    """List only published products owned by another Data Space.

    The picker needs ownership and a PII warning, not the product contract; the latter is only
    returned after the owner grants access through the normal access-request workflow.
    """
    products = await published_products(principal, data_space)
    async with transaction(ROLE, principal) as session:
        spaces = dict(
            (
                await session.execute(
                    select(org.DataSpace.id, org.DataSpace.name).where(
                        org.DataSpace.id.in_({p.data_space_id for p in products}),
                        org.DataSpace.deleted_at.is_(None),
                    )
                )
            ).all()
        )
    items = products[: page.page_size]
    return Page(
        items=[
            PublishedCatalogProduct(
                id=row.id,
                code=row.code,
                name=row.name,
                version=str(row.version),
                owner_space={"id": row.data_space_id, "name": spaces[row.data_space_id]},
                has_pii=bool(
                    isinstance(row.contract, dict)
                    and (row.contract.get("has_pii") or row.contract.get("contains_pii"))
                ),
            )
            for row in items
        ],
        next_page_token="",
    )


@router.post(
    "/v1/data-products:request-use",
    response_model=dict[str, list[ProductUseRequestView]],
    status_code=202,
    **endpoint("POST", "/v1/data-products:request-use", operation_id="requestDataProductUse"),
)
async def request_data_product_use(
    body: ProductUseRequestBody,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> dict[str, list[ProductUseRequestView]]:
    """Create pending owner-space access requests for published products."""
    if body.data_space_id != data_space:
        raise Problem(422, "validation_failed", "data_space_id must be the selected Data Space.")
    products = await published_products(principal, data_space, set(body.product_ids))
    if len(products) != len(set(body.product_ids)):
        raise Problem(404, "not_found", "One or more published products are not available.")
    async with transaction(ROLE, principal) as session:
        made: list[ProductUseRequestView] = []
        for row in products:
            request_id = uuid7()
            emit(
                session,
                principal,
                "data_product",
                row.id,
                "data_product.use_requested",
                request_id=request_id,
                product_id=row.id,
                owner_data_space_id=row.data_space_id,
                requester_data_space_id=data_space,
                requester_id=principal.user_id,
                capability="view_aggregate",
                requested_until=(now() + datetime.timedelta(days=90)).date(),
            )
            made.append(ProductUseRequestView(id=request_id, product_id=row.id, status="pending"))
    return {"requests": made}


@router.post(
    "/v1/data-products",
    status_code=201,
    response_model=DataProductView | PublishedDataProductView,
    **endpoint(
        "POST",
        "/v1/data-products",
        operation_id="createDataProduct",
        group=("Lớp ngữ cảnh", "Biến đổi dữ liệu"),
        variant_guard=Depends(variant_guard),
    ),
)
async def create_data_product(
    body: DataProductCreate,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> DataProductView | PublishedDataProductView:
    sent = body.model_fields_set
    if body.layer == "published":
        stray = sorted(sent - {"layer", "code", "name", "owner_id", *PUBLISHED_ONLY})
        if stray:
            raise field_error(stray[0], f"{stray} belong to layer context.")
        if body.contract is None:
            raise field_error("contract", "contract is required for layer published.", "required")
        published = await create_published_product(
            principal,
            data_space,
            code=body.code,
            name=body.name,
            contract=body.contract,
            dataset_id=body.dataset_id,
            data_mart_id=body.data_mart_id,
            owner_id=body.owner_id,
        )
        return PublishedDataProductView.model_validate(published)

    stray = sorted(sent & set(PUBLISHED_ONLY))
    if stray:
        raise field_error(stray[0], f"{stray} belong to layer published.")
    for name in CONTEXT_REQUIRED:
        if getattr(body, name) is None:
            raise field_error(name, f"{name} is required for layer context.", "required")
    _distinct_people(body.owner_id, body.steward_id, principal.user_id)
    if body.period_to is not None and body.period_from and body.period_to < body.period_from:
        raise field_error("period_to", "period_to is before period_from.")
    async with transaction(ROLE, principal) as session:
        await require_active_users(
            session, principal, {"owner_id": body.owner_id, "steward_id": body.steward_id}
        )
        if body.pack_version_id is not None:
            found = await session.scalar(
                select(pack.PackVersion.id).where(
                    pack.PackVersion.id == body.pack_version_id,
                    pack.PackVersion.org_id == principal.org_id,
                )
            )
            if found is None:
                raise field_error("pack_version_id", "Pack version not found.")
        # UNIQUE (data_space_id, code) answers a taken code with 409 conflict.
        stamp = now()
        row = context.DataProduct(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            code=body.code,
            name=body.name,
            description=body.description,
            sample_questions=[q.model_dump(mode="json") for q in body.sample_questions or []],
            scope_entities=[e.model_dump(mode="json") for e in body.scope_entities or []],
            period_grain=body.period_grain,
            period_from=body.period_from,
            period_to=body.period_to,
            out_of_scope_note=body.out_of_scope_note,
            owner_id=body.owner_id,
            steward_id=body.steward_id,
            freshness_statement=body.freshness_statement,
            pack_version_id=body.pack_version_id,
            # eval.eval_set is the eval module's: the id is reserved here and the eval module
            # creates the context eval set holding the sample questions from the event.
            eval_set_id=uuid7(),
            status="draft",
            created_by=principal.user_id,
            updated_by=principal.user_id,
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(row)
        await session.flush()
        emit(
            session,
            principal,
            "data_product",
            row.id,
            "data_product.created",
            data_product_id=row.id,
            data_space_id=data_space,
            code=row.code,
            version=row.version,
            status=row.status,
            eval_set_id=row.eval_set_id,
            eval_set_subject="context",
            pack_version_id=row.pack_version_id,
        )
        await session.refresh(row)
        return _view(row)


@router.get(
    "/v1/data-products/{id}",
    response_model=DataProductDetail,
    **endpoint("GET", "/v1/data-products/{id}", operation_id="getDataProduct"),
)
async def get_data_product(
    product_id: ProductId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> DataProductDetail:
    async with transaction(ROLE, principal) as session:
        row = await _product(session, product_id, principal, data_space)
        # Only the serving version is a row; earlier ones live in the audit trail.
        if version is not None and version != row.version:
            raise Problem(404, "not_found", f"Version {version} of the data product not found.")
        members = await _members(session, row.id)
        freshness = [
            await _dataset_freshness(session, m, now(), None)
            for m in members
            if m.member_kind == "dataset" and m.role == "primary"
        ]
    summary = await data_product_eval_summary(principal, row.eval_set_id, row.id)
    detail = DataProductDetail.model_validate(row)
    detail.freshness_state = _worst([f.state for f in freshness])
    detail.members = [MemberView.model_validate(m) for m in members]
    detail.freshness = freshness
    detail.latest_eval_run = summary.latest_run
    detail.open_error_book_entries = summary.open_error_book_entries
    return detail


@router.post(
    "/v1/data-products/{id}:draft-version",
    response_model=DataProductDetail,
    **endpoint(
        "POST", "/v1/data-products/{id}:draft-version", operation_id="draftDataProductVersion"
    ),
)
async def draft_data_product_version(
    product_id: ProductId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> DataProductDetail:
    """Open an unchanged next-version proposal for the agent/editor to refine."""
    async with transaction(ROLE, principal) as session:
        row = await _product(session, product_id, principal, data_space, lock=True)
        if row.status not in _VERSIONED:
            raise Problem(409, "invalid_state", f"A {row.status} data product has no next version.")
        if row.proposal_id is not None:
            raise Problem(409, "invalid_state", "A change of this data product is in review.")
        row.proposal_id = uuid7()
        row.updated_by = principal.user_id
        row.updated_at = now()
        emit(
            session,
            principal,
            "data_product",
            row.id,
            "data_product.change_proposed",
            data_product_id=row.id,
            data_space_id=data_space,
            version=row.version + 1,
            proposal_id=row.proposal_id,
            proposal_kind="data_product",
            target_service="context",
            assignee_id=row.owner_id,
            created_by=principal.user_id,
            changes={},
        )
        await session.flush()
        await session.refresh(row)
        draft = _view(row)
        draft.version = row.version + 1
        draft.status = "pending_review"
    return DataProductDetail(**draft.model_dump())


@router.patch(
    "/v1/data-products/{id}",
    response_model=DataProductView,
    **endpoint("PATCH", "/v1/data-products/{id}", operation_id="updateDataProduct"),
)
async def update_data_product(
    product_id: ProductId,
    body: DataProductPatch,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> DataProductView:
    changes = body.model_dump(exclude_unset=True)
    for name in ("name", "description", "period_from", "owner_id", "steward_id"):
        if name in changes and changes[name] is None:
            raise field_error(name, f"{name} cannot be cleared.")
    if not changes:
        raise field_error("body", "Nothing to change.")
    async with transaction(ROLE, principal) as session:
        row = await _product(session, product_id, principal, data_space, lock=True)
        owner = changes.get("owner_id", row.owner_id)
        steward = changes.get("steward_id", row.steward_id)
        _distinct_people(owner, steward, row.created_by)
        start = changes.get("period_from", row.period_from)
        end = changes.get("period_to", row.period_to)
        if end is not None and end < start:
            raise field_error("period_to", "period_to is before period_from.")
        await require_active_users(
            session,
            principal,
            {k: changes[k] for k in ("owner_id", "steward_id") if k in changes},
        )
        if row.status in _EDITABLE:
            for name, value in changes.items():
                if name in ("sample_questions", "scope_entities"):
                    value = [v.model_dump(mode="json") for v in getattr(body, name) or []]
                setattr(row, name, value)
            row.updated_by = principal.user_id
            row.updated_at = now()
            await session.flush()
            await session.refresh(row)
            return _view(row)
        if row.status not in _VERSIONED:
            raise Problem(409, "invalid_state", f"A {row.status} data product is not edited.")
        if row.proposal_id is not None:
            raise Problem(409, "invalid_state", "A change of this data product is in review.")
        # UC-07-13 6a: the change is version + 1 through review; the served row stays as it is
        # until the proposal is accepted, when the context consumer applies it.
        proposed = {
            k: (
                [v.model_dump(mode="json") for v in getattr(body, k) or []]
                if k in ("sample_questions", "scope_entities")
                else v
            )
            for k, v in changes.items()
        }
        row.proposal_id = uuid7()
        row.updated_by = principal.user_id
        row.updated_at = now()
        emit(
            session,
            principal,
            "data_product",
            row.id,
            "data_product.change_proposed",
            data_product_id=row.id,
            data_space_id=data_space,
            version=row.version + 1,
            proposal_id=row.proposal_id,
            proposal_kind="data_product",
            target_service="context",
            assignee_id=row.owner_id,
            created_by=principal.user_id,
            changes=_jsonable(proposed),
        )
        await session.flush()
        await session.refresh(row)
        draft = _view(row)
    for name, value in proposed.items():
        setattr(draft, name, value)
    draft.version = row.version + 1
    draft.status = "pending_review"
    return draft


# ---------------------------------------------------------------- members


async def _check_member(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    body: MemberCreate,
    role: str,
    sla_kind: str,
) -> str:
    """The member exists in this Data Space and may be one (BR-175, UC-07-13 2a, 2b); returns
    its classification."""
    kind, ref = body.member_kind, body.member_ref
    if kind == "dataset":
        ds = await session.scalar(
            select(catalog.Dataset).where(
                catalog.Dataset.id == ref,
                catalog.Dataset.org_id == principal.org_id,
                catalog.Dataset.data_space_id == data_space,
                catalog.Dataset.deleted_at.is_(None),
            )
        )
        if ds is None:
            raise field_error("member_ref", "Dataset not found in this Data Space.")
        versioned = await session.scalar(
            select(func.count())
            .select_from(catalog.DatasetVersion)
            .where(catalog.DatasetVersion.dataset_id == ref)
        )
        if ds.history_kind == "none" and not versioned:
            raise _rule(
                "The dataset cannot be read as of a date: load it with versions first (BR-175)."
            )
        if role == "primary" and sla_kind == "none":
            raise _rule("A primary dataset needs a freshness commitment (BR-175).")
        # BR-180 routes models on the product's classification: a dataset with personal-data
        # columns is never below confidential; `public` needs AC06's confirmation, not this route.
        personal = await session.scalar(
            select(func.count())
            .select_from(catalog.ColumnMeta)
            .where(
                catalog.ColumnMeta.dataset_id == ref,
                catalog.ColumnMeta.removed_in_version.is_(None),
                catalog.ColumnMeta.pii_label.is_not(None),
            )
        )
        return "confidential" if personal else "internal"
    if kind == "metric":
        metric = await session.scalar(
            select(context.Metric).where(
                context.Metric.id == ref,
                context.Metric.org_id == principal.org_id,
                context.Metric.data_space_id == data_space,
            )
        )
        if metric is None:
            raise field_error("member_ref", "Metric not found in this Data Space.")
        certified = await session.scalar(
            select(context.MetricVersion.id).where(
                context.MetricVersion.metric_id == metric.id,
                context.MetricVersion.version == metric.current_version,
                context.MetricVersion.status == "certified",
            )
        )
        if certified is None:
            raise _rule("The metric's current version is not certified (BR-175).")
        return "internal"
    if kind == "knowledge_page":
        page = await session.scalar(
            select(context.KnowledgePage).where(
                context.KnowledgePage.id == ref,
                context.KnowledgePage.org_id == principal.org_id,
                context.KnowledgePage.data_space_id == data_space,
                context.KnowledgePage.status != "retired",
            )
        )
        if page is None:
            raise field_error("member_ref", "Knowledge page not found in this Data Space.")
        return page.classification
    if kind == "document_collection":
        policy = await session.scalar(
            select(document.Collection.pii_policy).where(
                document.Collection.id == ref,
                document.Collection.org_id == principal.org_id,
                document.Collection.data_space_id == data_space,
                document.Collection.deleted_at.is_(None),
            )
        )
        if policy is None:
            raise field_error("member_ref", "Document collection not found in this Data Space.")
        # A collection that keeps personal data for authorised readers holds it unmasked.
        return "confidential" if policy == "keep_for_authorized" else "internal"
    if not await shared_product_usable(principal, data_space, ref):
        raise field_error(
            "member_ref", "No published shared product of or for this Data Space has that id."
        )
    return "internal"


def _sla(body: MemberCreate, kind: str, role: str) -> dict[str, Any]:
    sla_kind = body.sla_kind or "none"
    if kind != "dataset" and sla_kind != "none":
        raise field_error("sla_kind", "Only a dataset carries a freshness commitment.")
    if sla_kind == "schedule" and not (body.sla_cron or body.sla_event_calendar_code):
        raise field_error("sla_cron", "A schedule needs sla_cron or sla_event_calendar_code.")
    if sla_kind == "max_age" and body.sla_max_age is None:
        raise field_error("sla_max_age", "max_age needs sla_max_age.", "required")
    if body.sla_grace is not None and not datetime.timedelta(0) <= body.sla_grace <= MAX_GRACE:
        raise field_error("sla_grace", "sla_grace is between 0 and 1 day.")
    values: dict[str, Any] = {
        "role": role,
        "sla_kind": sla_kind,
        "sla_cron": body.sla_cron.strip() if body.sla_cron else None,
        "sla_event_calendar_code": body.sla_event_calendar_code,
        "sla_work_calendar_id": body.sla_work_calendar_id,
        "sla_max_age": body.sla_max_age,
        "as_of_col": body.as_of_col,
    }
    if body.sla_timezone:
        values["sla_timezone"] = body.sla_timezone
    if body.sla_grace is not None:
        values["sla_grace"] = body.sla_grace
    return values


@router.post(
    "/v1/data-products/{id}/members",
    status_code=201,
    response_model=MemberView,
    **endpoint("POST", "/v1/data-products/{id}/members", operation_id="addDataProductMember"),
)
async def add_data_product_member(
    product_id: ProductId,
    body: MemberCreate,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> MemberView:
    role = body.role or "primary"
    async with transaction(ROLE, principal) as session:
        row = await _product(session, product_id, principal, data_space, lock=True)
        if row.status == "retired":
            raise _rule("A retired data product takes no member changes (BR-175).")
        if row.status in _VERSIONED and role == "primary":
            # A new primary member changes the commitment: a new version (UC-07-13 6a).
            raise _rule("An active data product takes a primary member only in a new version.")
        values = _sla(body, body.member_kind, role)
        classification = await _check_member(
            session, principal, data_space, body, role, values["sla_kind"]
        )
        if body.sla_work_calendar_id is not None:
            calendar = await session.scalar(
                select(org.WorkCalendar.id).where(
                    org.WorkCalendar.id == body.sla_work_calendar_id,
                    org.WorkCalendar.org_id == principal.org_id,
                )
            )
            if calendar is None:
                raise field_error("sla_work_calendar_id", "Work calendar not found.")
        duplicate = await session.scalar(
            select(context.DataProductMember.id).where(
                context.DataProductMember.data_product_id == row.id,
                context.DataProductMember.member_kind == body.member_kind,
                context.DataProductMember.member_ref == body.member_ref,
            )
        )
        if duplicate is not None:
            raise Problem(409, "conflict", "Already a member of this data product.")
        stamp = now()
        member = context.DataProductMember(
            id=uuid7(),
            org_id=principal.org_id,
            data_product_id=row.id,
            member_kind=body.member_kind,
            member_ref=body.member_ref,
            classification=classification,
            created_by=principal.user_id,
            updated_by=principal.user_id,
            created_at=stamp,
            updated_at=stamp,
            **values,
        )
        session.add(member)
        await session.flush()
        # A3: next_due_at and freshness_state are computed now, by the database's own rule.
        await session.execute(
            text("SELECT context.data_product_freshness_refresh(:id)"), {"id": member.id}
        )
        await session.refresh(member)
        return MemberView.model_validate(member)


@router.delete(
    "/v1/data-products/{id}/members/{member_id}",
    status_code=204,
    **endpoint(
        "DELETE",
        "/v1/data-products/{id}/members/{member_id}",
        operation_id="removeDataProductMember",
    ),
)
async def remove_data_product_member(
    product_id: ProductId,
    member_id: uuid.UUID,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> Response:
    async with transaction(ROLE, principal) as session:
        row = await _product(session, product_id, principal, data_space, lock=True)
        member = await session.scalar(
            select(context.DataProductMember).where(
                context.DataProductMember.id == member_id,
                context.DataProductMember.data_product_id == row.id,
            )
        )
        if member is None:
            raise Problem(404, "not_found", "Member not found.")
        # The guard trigger refuses a primary member of an active product (422).
        await session.delete(member)
        await session.flush()
    return Response(status_code=204)


# ---------------------------------------------------------------- lifecycle


async def _submit_failures(session: AsyncSession, row: context.DataProduct) -> list[str]:
    """What BR-175 asks of activation that is known before the eval run."""
    failures = []
    answered = [q for q in row.sample_questions or [] if q.get("expected") is not None]
    if len(answered) < MIN_SAMPLE_QUESTIONS:
        failures.append(
            f"{len(answered)} sample questions with an expected answer, at least "
            f"{MIN_SAMPLE_QUESTIONS} are needed"
        )
    members = await _members(session, row.id)
    metric_ids = [m.member_ref for m in members if m.member_kind == "metric"]
    certified = 0
    if metric_ids:
        certified = (
            await session.scalar(
                select(func.count())
                .select_from(context.Metric)
                .join(
                    context.MetricVersion,
                    (context.MetricVersion.metric_id == context.Metric.id)
                    & (context.MetricVersion.version == context.Metric.current_version),
                )
                .where(
                    context.Metric.id.in_(metric_ids), context.MetricVersion.status == "certified"
                )
            )
            or 0
        )
    if not certified:
        failures.append("no member metric has a certified current version")
    if not any(
        m.member_kind == "dataset" and m.role == "primary" and m.sla_kind != "none" for m in members
    ):
        failures.append("no primary dataset with a freshness commitment")
    return failures


@router.post(
    "/v1/data-products/{id}:submit",
    status_code=202,
    response_model=DataProductSubmitted,
    **endpoint("POST", "/v1/data-products/{id}:submit", operation_id="submitDataProduct"),
)
async def submit_data_product(
    product_id: ProductId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
    body: Annotated[SubmitBody, Body()] = _NO_SUBMIT,
) -> dict[str, Any]:
    async with transaction(ROLE, principal) as session:
        row = await _product(session, product_id, principal, data_space, lock=True)
        if row.status != "draft":
            raise Problem(409, "invalid_state", f"A {row.status} data product is not submitted.")
        failures = await _submit_failures(session, row)
        if failures:
            raise _rule("Cannot submit: " + "; ".join(failures) + " (BR-175).")
        # The eval module runs the product's eval set under this id; at 95% or more on 10 or
        # more questions it moves the product to pending_review and opens the data_product
        # proposal for the owner (UC-07-13 step 5, E2).
        eval_run_id = uuid7()
        row.updated_by = principal.user_id
        row.updated_at = now()
        emit(
            session,
            principal,
            "data_product",
            row.id,
            "data_product.submitted",
            data_product_id=row.id,
            data_space_id=data_space,
            version=row.version,
            eval_set_id=row.eval_set_id,
            eval_run_id=eval_run_id,
            sample_questions=row.sample_questions,
            min_pass_rate=0.95,
            min_items=MIN_SAMPLE_QUESTIONS,
            proposal_kind="data_product",
            assignee_id=row.owner_id,
            created_by=row.created_by,
            status=row.status,
        )
        if body.note:
            audit(
                session,
                principal,
                action="data_product.submit",
                object_kind="data_product",
                object_id=row.id,
                after={"note": body.note, "eval_run_id": str(eval_run_id)},
            )
        out = accepted(response, f"/v1/eval-runs/{eval_run_id}", eval_run_id, "queued")
    out["eval_run_id"] = eval_run_id
    return out


async def _set_status(
    product_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    allowed: tuple[str, ...],
    status: str,
    reason: str,
    action: str,
) -> DataProductView:
    async with transaction(ROLE, principal) as session:
        row = await _product(session, product_id, principal, data_space, lock=True)
        if row.status not in allowed:
            raise Problem(409, "conflict", f"The data product is {row.status}.")
        before = row.status
        row.status = status
        if status == "suspended":
            row.suspended_reason = "manual"
        row.updated_by = principal.user_id
        row.updated_at = now()
        # context.data_product.changed is written by the table's trigger; the person's reason
        # goes to the audit trail, not into the domain event.
        audit(
            session,
            principal,
            action=action,
            object_kind="data_product",
            object_id=row.id,
            after={"from": before, "to": status, "reason": reason},
        )
        await session.flush()
        await session.refresh(row)
        states = await _overall_freshness(session, [row.id])
        return _view(row, states[row.id])


@router.post(
    "/v1/data-products/{id}:suspend",
    response_model=DataProductView,
    **endpoint("POST", "/v1/data-products/{id}:suspend", operation_id="suspendDataProduct"),
)
async def suspend_data_product(
    product_id: ProductId,
    body: ReasonBody,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> DataProductView:
    return await _set_status(
        product_id,
        principal,
        data_space,
        allowed=("active",),
        status="suspended",
        reason=body.reason,
        action="data_product.suspend",
    )


@router.post(
    "/v1/data-products/{id}:retire",
    response_model=DataProductView,
    **endpoint("POST", "/v1/data-products/{id}:retire", operation_id="retireDataProduct"),
)
async def retire_data_product(
    product_id: ProductId,
    body: ReasonBody,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> DataProductView:
    # Retired is final but not deleted: receipts of old answers still read it (UC-07-13).
    return await _set_status(
        product_id,
        principal,
        data_space,
        allowed=("draft", "pending_review", "active", "suspended"),
        status="retired",
        reason=body.reason,
        action="data_product.retire",
    )


# ---------------------------------------------------------------- freshness (BR-178)


async def _deadline(
    session: AsyncSession, member_id: uuid.UUID, bound: datetime.datetime, *, next_: bool
) -> datetime.datetime | None:
    value: datetime.datetime | None = await session.scalar(
        text("SELECT context.data_product_sla_deadline(:id, :bound, :next)"),
        {"id": member_id, "bound": bound, "next": next_},
    )
    return value


def _local_day(at: datetime.datetime, tz: str) -> datetime.datetime:
    local = at.astimezone(ZoneInfo(tz))
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


async def _dataset_freshness(
    session: AsyncSession,
    member: context.DataProductMember,
    at: datetime.datetime,
    periods: int | None,
) -> DatasetFreshness:
    """The state of context.data_product_freshness_refresh, computed without writing, so a
    reader can ask it for any instant (?at)."""
    due = next_due = expected = None
    state = "unknown"
    history: list[PeriodState] | None = None
    if member.sla_kind == "schedule":
        bound = at - member.sla_grace
        due = await _deadline(session, member.id, bound, next_=False)
        next_due = await _deadline(session, member.id, bound, next_=True)
        if due is not None:
            expected = _local_day(due, member.sla_timezone)
            if member.last_as_of is None:
                state = "missing"
            else:
                state = "fresh" if member.last_as_of >= expected else "overdue"
        if periods:
            history = []
            cursor = due
            while cursor is not None and len(history) < periods:
                period = _local_day(cursor, member.sla_timezone)
                shown = member.last_as_of is not None and member.last_as_of >= period
                history.append(
                    PeriodState(period=period.date(), state="shown" if shown else "masked_stale")
                )
                cursor = await _deadline(
                    session, member.id, cursor - datetime.timedelta(seconds=1), next_=False
                )
            if next_due is not None:
                # The coming period is not due yet: CHƯA CÓ unless it is already loaded.
                period = _local_day(next_due, member.sla_timezone)
                loaded = member.last_as_of is not None and member.last_as_of >= period
                history.insert(
                    0,
                    PeriodState(period=period.date(), state="shown" if loaded else "not_available"),
                )
                history = history[:periods]
    elif member.sla_kind == "max_age" and member.sla_max_age is not None:
        expected = at - member.sla_max_age - member.sla_grace
        if member.last_as_of is not None:
            next_due = member.last_as_of + member.sla_max_age + member.sla_grace
            state = "fresh" if member.last_as_of >= expected else "overdue"
        else:
            state = "missing"
    return DatasetFreshness(
        member_id=member.id,
        dataset_id=member.member_ref,
        sla_kind=member.sla_kind,
        last_as_of=member.last_as_of,
        last_loaded_at=member.last_loaded_at,
        expected_as_of=expected,
        due_at=due,
        next_due_at=next_due,
        state=state,
        periods=history,
    )


@router.get(
    "/v1/data-products/{id}/freshness",
    response_model=FreshnessReport,
    **endpoint("GET", "/v1/data-products/{id}/freshness", operation_id="getDataProductFreshness"),
)
async def get_data_product_freshness(
    product_id: ProductId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    at: datetime.datetime | None = None,
    periods: Annotated[int | None, Query(ge=1, le=MAX_PERIODS)] = None,
) -> FreshnessReport:
    instant = at or now()
    if instant.tzinfo is None:
        raise field_error("at", "at needs a time zone, e.g. 2026-07-02T16:45:00+07:00.")
    async with transaction(ROLE, principal) as session:
        row = await _product(session, product_id, principal, data_space)
        members = [
            m
            for m in await _members(session, row.id)
            if m.member_kind == "dataset" and m.role == "primary"
        ]
        datasets = [await _dataset_freshness(session, m, instant, periods) for m in members]
    return FreshnessReport(data_product_id=row.id, at=instant, datasets=datasets)
