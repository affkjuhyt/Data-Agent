"""What other api modules may ask the governance module.

ADR-28: modules call each other through public interfaces. The catalog module hands out a dataset
version's raw Parquet snapshot only when no access policy shapes what its readers see (DF-05):
it asks here instead of reading governance.access_policy itself.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from libs.dataagent_db.models import governance
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction

ROLE = "svc_governance"


async def dataset_has_access_policies(principal: Principal, dataset_id: uuid.UUID) -> bool:
    """True when a row, column or mask policy, active or about to be, covers the dataset.

    Drafts count: a policy is written before it is switched on, and a file handed out before then
    would outlive the switch."""
    ap = governance.AccessPolicy
    async with transaction(ROLE, principal) as session:
        found = await session.scalar(
            select(ap.id)
            .where(ap.dataset_id == dataset_id, ap.status.in_(("draft", "active")))
            .limit(1)
        )
    return found is not None
