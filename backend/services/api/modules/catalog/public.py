"""What other api modules may ask the catalog module.

ADR-28: modules call each other through public interfaces. The conflict module ranks sources and
shows them next to the claims they back (UC-07-04, UC-07-05); it asks here instead of reading
catalog.source itself, and never sees connection details or OpenBao paths.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select

from libs.dataagent_db.models import catalog
from services.api.kernel.auth import Principal
from services.api.kernel.db import transaction


@dataclass(frozen=True)
class SourceSummary:
    id: uuid.UUID
    name: str
    kind: str
    authority_level: str
    status: str


async def sources_in_data_space(
    principal: Principal, data_space: uuid.UUID, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, SourceSummary]:
    """The sources among `ids` that belong to the Data Space and are not deleted, by id."""
    wanted = set(ids)
    if not wanted:
        return {}
    src = catalog.Source
    async with transaction("svc_catalog", principal) as session:
        rows = (
            await session.execute(
                select(src.id, src.name, src.kind, src.authority_level, src.status).where(
                    src.id.in_(wanted),
                    src.org_id == principal.org_id,
                    src.data_space_id == data_space,
                    src.deleted_at.is_(None),
                )
            )
        ).all()
    return {
        row.id: SourceSummary(
            id=row.id,
            name=row.name,
            kind=row.kind,
            authority_level=row.authority_level,
            status=row.status,
        )
        for row in rows
    }
