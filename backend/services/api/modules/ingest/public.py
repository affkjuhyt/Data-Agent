"""What other api modules may ask of the ingest module (ADR-28: through this, never its tables).

Each function opens its own svc_ingest transaction: modules do not share transactions.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection

from sqlalchemy import or_, select

from libs.dataagent_db.models import ingest
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction

ROLE = "svc_ingest"


async def jobs_feeding(
    principal: Principal,
    data_space_id: uuid.UUID,
    dataset_ids: Collection[uuid.UUID],
    source_ids: Collection[uuid.UUID],
) -> list[tuple[uuid.UUID, str]]:
    """The live ingestion jobs of the Data Space that load these datasets or read these sources,
    as (id, name): what a pipeline on them can be scheduled after (UC-06-03)."""
    if not dataset_ids and not source_ids:
        return []
    job = ingest.IngestionJob
    async with transaction(ROLE, principal) as session:
        rows = (
            await session.execute(
                select(job.id, job.name)
                .where(
                    job.org_id == principal.org_id,
                    job.data_space_id == data_space_id,
                    job.deleted_at.is_(None),
                    job.status != "draft",
                    or_(job.target_dataset_id.in_(dataset_ids), job.source_id.in_(source_ids)),
                )
                .order_by(job.name)
            )
        ).all()
    return [(r.id, r.name) for r in rows]


async def job_name(principal: Principal, data_space_id: uuid.UUID, job_id: uuid.UUID) -> str | None:
    """The name of a live ingestion job of the Data Space, or None (a pipeline scheduled after it)."""
    job = ingest.IngestionJob
    async with transaction(ROLE, principal) as session:
        name: str | None = await session.scalar(
            select(job.name).where(
                job.id == job_id,
                job.org_id == principal.org_id,
                job.data_space_id == data_space_id,
                job.deleted_at.is_(None),
            )
        )
    return name
