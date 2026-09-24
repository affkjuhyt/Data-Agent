"""Stamps the outcome of a Silver/Gold attempt onto `KBData.metadata`.

`AssetInventory.tsx`'s "Auto-processing to Silver/Gold..." status for an
auto-promotion source only checks `promotion_mode == "auto" and current_tier
!= "gold"` — true forever once an attempt hits a genuine terminal failure
(an unsupported file format, a corrupt file the parser can't open), not just
while a run is actually in flight. Found live: "Comtrade" (`.bin`, Docling
refuses the extension) and an old-format `.xls` report both sat at Bronze
showing "still processing" for a full day after failing outright. Recording
the real outcome here lets the frontend tell "still working" apart from
"gave up, needs attention" without a second read from Mongo per row."""
from __future__ import annotations

import uuid

from sqlalchemy import bindparam
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import JSONB

from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import KBDataORM
from libs.database_connector.postgres_connector import PostgresClient
from libs.shared.row_version import with_new_version


async def stamp_pipeline_status(
    postgres: PostgresClient, data_id: str, *, stage: str, status: str, error: str | None = None,
) -> None:
    """Best-effort: a failure here (row deleted mid-flight, connection hiccup)
    must not turn a real pipeline error into an unrelated crash, so this
    swallows its own exceptions same as the rollback helpers around it."""
    patch = {
        "last_pipeline_stage": stage,
        "last_pipeline_status": status,
        "last_pipeline_error": error[:300] if error else None,
    }
    # Must be a bindparam explicitly typed JSONB carrying the raw dict, not
    # `cast(json.dumps(patch), JSONB)` — asyncpg's JSONB codec already calls
    # json.dumps on whatever Python value it's handed, so feeding it an
    # already-serialized string double-encodes it into a JSON *string
    # scalar*. Postgres's `||` then sees "object || scalar", which is
    # **array concatenation**, not an object merge — this genuinely
    # corrupted 7 live KBData.metadata rows into `[old_object, "...", "..."]`
    # before being caught (see the one-off repair script run alongside this
    # fix).
    patch_param = bindparam("pipeline_status_patch", patch, type_=JSONB)
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBDataORM)
                .where(KBDataORM.data_id == uuid.UUID(data_id))
                .values(**with_new_version(doc_metadata=KBDataORM.doc_metadata.op("||")(patch_param)))
            )
            await session.commit()
        except Exception:  # noqa: BLE001 — best-effort, see docstring
            await session.rollback()
