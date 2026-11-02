"""What other api modules may ask the context module.

ADR-28: modules call each other through public interfaces. The inbox decides a data product's
activation proposal but cannot read context.data_product; it asks here who created the product,
because the creator accepting it is a self-approval (BR-175) even when an editor submitted it.

The inbox also says what a proposal changes in words (inbox/describe.py, P07 "Thay đổi"), and a
proposal's diff carries ids of the semantic layer and the document store. It asks here for their
names instead of reading those schemas itself: svc_context reads them as the caller (RLS keeps
their organisation), a knowledge page only to its readers (BR-163), and only within the Data
Space the caller named, so an id from elsewhere stays unnamed. Datasets and sources are the catalog's to name (catalog/public.py).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import context, document
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction
from services.api.modules.context.common import ROLE, page_readable, readable_data_spaces

NameKind = Literal[
    "metric",
    "dimension",
    # A dimension named by its code: metric_change diffs list allowed dimensions by code.
    "dimension_code",
    "measure",
    "entity",
    "document",
    "data_product",
    "knowledge_page",
]


@dataclass(frozen=True)
class Name:
    name: str
    # The object's code (a metric's TON_KHO).
    code: str | None = None


def _ids(values: Iterable[str]) -> set[uuid.UUID]:
    out = set()
    for value in values:
        try:
            out.add(uuid.UUID(str(value)))
        except ValueError:
            continue
    return out


async def _metrics(session: AsyncSession, space: uuid.UUID, ids: set[uuid.UUID]) -> dict[str, Name]:
    metric, version = context.Metric, context.MetricVersion
    found = {
        row.id: row
        for row in (
            await session.execute(
                select(metric.id, metric.code, metric.current_version).where(
                    metric.id.in_(ids), metric.data_space_id == space
                )
            )
        ).all()
    }
    if not found:
        return {}
    # The name of the version in use, else of the latest one (a metric not certified yet).
    names: dict[uuid.UUID, str] = {}
    for row in (
        await session.execute(
            select(version.metric_id, version.version, version.display_name)
            .where(version.metric_id.in_(found))
            .order_by(version.version.desc())
        )
    ).all():
        if row.version == found[row.metric_id].current_version or row.metric_id not in names:
            names[row.metric_id] = row.display_name
    return {
        str(mid): Name(name=names.get(mid, row.code), code=row.code) for mid, row in found.items()
    }


async def _coded(
    session: AsyncSession, model: Any, space: uuid.UUID, ids: set[uuid.UUID]
) -> dict[str, Name]:
    rows = await session.execute(
        select(model.id, model.name, model.code).where(
            model.id.in_(ids), model.data_space_id == space
        )
    )
    return {str(r.id): Name(name=r.name, code=r.code) for r in rows.all()}


async def _dimension_codes(
    session: AsyncSession, space: uuid.UUID, codes: set[str]
) -> dict[str, Name]:
    dim = context.SemanticDimension
    rows = await session.execute(
        select(dim.name, dim.code).where(dim.code.in_(codes), dim.data_space_id == space)
    )
    return {r.code: Name(name=r.name, code=r.code) for r in rows.all()}


async def _documents(
    session: AsyncSession, space: uuid.UUID, ids: set[uuid.UUID]
) -> dict[str, Name]:
    doc, collection = document.Document, document.Collection
    rows = await session.execute(
        select(doc.id, doc.title, doc.file_name)
        .join(collection, collection.id == doc.collection_id)
        .where(doc.id.in_(ids), collection.data_space_id == space)
    )
    return {str(r.id): Name(name=r.title or r.file_name) for r in rows.all()}


async def _pages(
    session: AsyncSession, principal: Principal, space: uuid.UUID, ids: set[uuid.UUID]
) -> dict[str, Name]:
    """Only the pages the caller may read (BR-163, page_readable). Not through the reader policy:
    that one also hides a page not served yet, which is what a knowledge_page proposal is about."""
    page = context.KnowledgePage
    rows = await session.execute(
        select(
            page.id, page.title, page.slug, page.required_data_space_ids, page.classification
        ).where(page.id.in_(ids), page.data_space_id == space)
    )
    found = rows.all()
    spaces = await readable_data_spaces(session, principal) if found else []
    return {
        str(r.id): Name(name=r.title, code=r.slug)
        for r in found
        if page_readable(r.required_data_space_ids, r.classification, spaces)
    }


async def names_in_data_space(
    principal: Principal, data_space: uuid.UUID, wanted: Mapping[NameKind, Iterable[str]]
) -> dict[tuple[NameKind, str], Name]:
    """The names behind `wanted` (ids as text; codes for dimension_code), keyed by (kind, the
    text asked for). What the caller cannot read, or that is not of the Data Space, is absent."""
    asked = {kind: {str(v) for v in values if v} for kind, values in wanted.items()}
    asked = {kind: values for kind, values in asked.items() if values}
    if not asked:
        return {}
    out: dict[tuple[NameKind, str], Name] = {}
    async with transaction(ROLE, principal) as session:
        for kind, values in asked.items():
            found: dict[str, Name]
            if kind == "dimension_code":
                found = await _dimension_codes(session, data_space, values)
            elif not (ids := _ids(values)):
                continue
            elif kind == "metric":
                found = await _metrics(session, data_space, ids)
            elif kind == "document":
                found = await _documents(session, data_space, ids)
            elif kind == "knowledge_page":
                found = await _pages(session, principal, data_space, ids)
            else:
                model = {
                    "dimension": context.SemanticDimension,
                    "measure": context.SemanticMeasure,
                    "entity": context.SemanticEntity,
                    "data_product": context.DataProduct,
                }[kind]
                found = await _coded(session, model, data_space, ids)
            # Keyed by the text asked for: an id written in capitals or without dashes still finds
            # its row, and the caller looks it up as it wrote it.
            for value in values:
                key = value if kind == "dimension_code" else _canonical(value)
                if key is not None and key in found:
                    out[(kind, value)] = found[key]
    return out


def _canonical(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


async def data_product_awaiting(principal: Principal, proposal_id: uuid.UUID) -> uuid.UUID | None:
    """The creator of the data product this proposal would activate, or None when the proposal
    activates none (a version change, or a product no longer waiting for it)."""
    product = context.DataProduct
    async with transaction(ROLE, principal) as session:
        return await session.scalar(
            select(product.created_by).where(
                product.org_id == principal.org_id,
                product.proposal_id == proposal_id,
                product.status == "pending_review",
            )
        )
