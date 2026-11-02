"""What other api modules may ask the context module.

ADR-28: modules call each other through public interfaces. The inbox decides a data product's
activation proposal but cannot read context.data_product; it asks here who created the product,
because the creator accepting it is a self-approval (BR-175) even when an editor submitted it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from libs.dataagent_db.models import context
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction

ROLE = "svc_context"


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
