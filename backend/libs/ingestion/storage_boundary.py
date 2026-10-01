"""Does this KBData document belong to a V2 structured source?

The async, database-reading half of `libs.shared.storage_ownership` (which stays
pure). Asked by every step that would push a document down the legacy chunk /
structured-KB path — the Silver and Gold consumers and kb_backend's promote and
propose routes — so a stale button, a replayed Kafka event or an auto-promote
from an old crawler build all stop at the same check.

The link from document to source is `KBData.metadata.source_id`, stamped by the
web-crawler's Bronze step (`platform_workers/workers/web_crawler/bronze.py`). A
manual upload has none and is never a V2 structured source.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select as sa_select

from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBDataORM,
    KBSourceConfigORM,
)
from libs.shared.storage_ownership import is_v2_structured_source


def _as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def is_v2_structured_document(postgres: Any, data_id: str, tenant_id: str) -> bool:
    """True when the document was crawled from a source whose active config has
    an `extraction_profile`. The source's *current* config decides, not the one
    in force when the document was crawled: adding a profile moves the source's
    numbers to `series_extractor` from then on, including a re-promote of a
    document it produced earlier. The read is scoped to the caller's tenant."""
    data_uuid = _as_uuid(data_id)
    if data_uuid is None:
        return False
    async with postgres.get_client(tenant_id=tenant_id) as session:
        metadata = await session.scalar(
            sa_select(KBDataORM.doc_metadata).where(KBDataORM.data_id == data_uuid).limit(1)
        )
        source_uuid = (
            _as_uuid((metadata or {}).get("source_id")) if isinstance(metadata, dict) else None
        )
        if source_uuid is None:
            return False
        config = await session.scalar(
            sa_select(KBSourceConfigORM.config)
            .where(
                KBSourceConfigORM.source_id == source_uuid,
                KBSourceConfigORM.is_active.is_(True),
                KBSourceConfigORM.is_deleted.is_(False),
            )
            .limit(1)
        )
    return is_v2_structured_source(config)
