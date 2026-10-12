"""What other api modules may ask the pipeline module.

ADR-28: modules call each other through public interfaces, not through each other's tables.
POST /v1/data-products is one route (served by the context module, decided 11/10/2026); with
layer "published" (P06, UC-06-06) it lands here, on svc_pipeline's own connection.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, literal, or_, select

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import org
from libs.dataagent_db.models import pipeline as pl
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction
from services.api.kernel.problems import Problem
from services.api.modules.pipeline import rules
from services.api.modules.pipeline.schemas import DataProductView
from services.api.modules.pipeline.store import ROLE, emit, invalid, now, require_datasets


async def create_published_product(
    principal: Principal,
    data_space: uuid.UUID,
    *,
    code: str,
    name: str,
    contract: dict[str, Any],
    dataset_id: uuid.UUID | None,
    data_mart_id: uuid.UUID | None,
    owner_id: uuid.UUID,
) -> dict[str, Any]:
    """Drafts a P06 shared data product (pipeline.data_product) and returns it as JSON.

    A code that already has a published version gets version + 1; when the new contract breaks
    the old one, breaking_notice_at starts the 30-day notice now (UC-06-06 4a, BR-60) and
    :publish refuses until it has run out. A second draft of the same code is a 409."""
    if dataset_id is None and data_mart_id is None:
        raise invalid("dataset_id", "A data product is built on a dataset or a data mart.")
    errors = rules.contract_errors(contract)
    if errors:
        raise invalid("contract", "; ".join(errors))
    if owner_id == principal.user_id:
        # UC-06-06 step 4: the owner approves what the drafter wrote, so they are two people.
        raise invalid("owner_id", "The owner approves publishing, so is not the drafter.")
    if not code.strip() or not name.strip():
        raise invalid("code" if not code.strip() else "name", "code and name are required.")
    async with transaction(ROLE, principal) as session:
        owner = await session.scalar(
            select(org.AppUser.id).where(
                org.AppUser.id == owner_id,
                org.AppUser.org_id == principal.org_id,
                org.AppUser.status == "active",
            )
        )
        if owner is None:
            raise invalid("owner_id", "owner_id is not an active user of this organisation.")
        if dataset_id is not None:
            await require_datasets(session, principal, data_space, [dataset_id], "dataset_id")
        if data_mart_id is not None:
            mart = await session.scalar(
                select(pl.DataMart).where(
                    pl.DataMart.id == data_mart_id,
                    pl.DataMart.org_id == principal.org_id,
                    pl.DataMart.data_space_id == data_space,
                )
            )
            if mart is None or mart.status == "retired":
                raise invalid("data_mart_id", "The data mart is not in this Data Space.")
        versions = list(
            (
                await session.scalars(
                    select(pl.DataProduct)
                    .where(
                        pl.DataProduct.org_id == principal.org_id,
                        pl.DataProduct.data_space_id == data_space,
                        pl.DataProduct.code == code,
                    )
                    .order_by(pl.DataProduct.version.desc())
                    # Two drafts of one code at once would race to the same version.
                    .with_for_update()
                )
            ).all()
        )
        if any(v.status == "draft" for v in versions):
            raise Problem(409, "conflict", f"Data product {code!r} already has a draft version.")
        latest = next((v for v in versions if v.status in ("published", "deprecated")), None)
        stamp = now()
        product = pl.DataProduct(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            code=code,
            name=name,
            version=(versions[0].version + 1) if versions else 1,
            dataset_id=dataset_id,
            data_mart_id=data_mart_id,
            contract=contract,
            owner_id=owner_id,
            consumers=list(latest.consumers) if latest else [],
            status="draft",
            breaking_notice_at=(
                stamp if latest and rules.breaking_changes(latest.contract, contract) else None
            ),
            created_by=principal.user_id,
            updated_by=principal.user_id,
            created_at=stamp,
            updated_at=stamp,
        )
        session.add(product)
        emit(
            session,
            principal,
            "data_product",
            product.id,
            "data_product.created",
            data_product_id=product.id,
            data_space_id=data_space,
            code=code,
            version=product.version,
            status=product.status,
            # BR-60: consumers of the previous version are told now, 30 days ahead.
            breaking=product.breaking_notice_at is not None,
            consumers=product.consumers,
        )
        await session.flush()
        view = DataProductView.model_validate(product)
    out: dict[str, Any] = jsonable_encoder(view)
    return out


async def shared_product_usable(
    principal: Principal, data_space: uuid.UUID, product_id: uuid.UUID
) -> bool:
    """A published P06 product the Data Space provides or has subscribed to, so a P07 data
    product there may take it as a shared_product member (UC-07-13)."""
    async with transaction(ROLE, principal) as session:
        found = await session.scalar(
            select(pl.DataProduct.id).where(
                pl.DataProduct.id == product_id,
                pl.DataProduct.org_id == principal.org_id,
                pl.DataProduct.status == "published",
                or_(
                    pl.DataProduct.data_space_id == data_space,
                    literal(data_space) == func.any(pl.DataProduct.consumers),
                ),
            )
        )
    return found is not None
