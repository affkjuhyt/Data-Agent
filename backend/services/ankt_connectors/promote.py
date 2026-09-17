"""Advancing an API artifact through silver and gold.

The artifact reaches bronze through `artifact.store`, and for a while it
stopped there: Data Layers showed eight documents frozen at bronze while the
figures they contained were already queryable. That is the wrong picture of
what happened, so this closes the walk.

What it deliberately does NOT do is hand the artifact to the document
pipeline. `kb_promote_silver` chunks prose with Docling and `kb_promote_gold`
embeds those chunks for retrieval; run a World Bank JSON series through them
and you get chunks like "1990 1.31e+11" — retrievable, meaningless, and
competing with real documents for the same top-k. The tiers are stages of
work, not one fixed implementation of them, and for a declared series the
connector already performs both:

  silver = normalise into typed records. The runner parsed the response into
           `ankt.observation` rows, each with a period, a unit and a release.
  gold   = publish, queryable, governed. Those rows are `status='approved'`
           and answer point-in-time reads through `libs.indicator.observation`.

So this records the transitions with notes that say which store holds the
output, and writes no chunks anywhere. `kb_silver_staging` stays empty for
these documents on purpose — a reader of that collection is looking for
something to embed, and there is nothing here that should be embedded.

The reason this is not the `structured_table` route, which would also be a
defensible home for tabular bronze: that route exists to have an LLM guess the
schema of a spreadsheet nobody has seen, then hold it for human approval. A
connector's schema is declared in `source_binding` and was previewed by the
steward when they saved the source. Re-proposing it on every scheduled fetch
would ask a person to re-approve a decision they already made, weekly, forever.
"""
from __future__ import annotations

import contextlib
import uuid
from typing import Any

from sqlalchemy import update as sa_update

from libs.basemodel.services_databaseconnector.postgres_model import (
    KBLifecycleHistoryInsert,
    Tier,
)
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBDataORM,
)
from libs.shared.row_version import with_new_version


async def _set_tier(postgres: Any, data_id: str, tier: Tier) -> None:
    async with postgres.get_client() as session:
        try:
            await session.execute(
                sa_update(KBDataORM)
                .where(KBDataORM.data_id == uuid.UUID(data_id))
                .values(**with_new_version(current_tier=tier.value))
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _record(
    postgres: Any,
    data_id: str,
    to_tier: Tier,
    from_tier: Tier,
    approved_by: str,
    notes: str,
    metadata: dict[str, Any],
) -> None:
    # Mirrors the document pipeline, which also swallows a failed history
    # insert: the tier has already moved, and raising here would roll back
    # nothing while making a completed promotion look like a failed one.
    with contextlib.suppress(Exception):
        await postgres.insert(KBLifecycleHistoryInsert(
            data_id=data_id,
            to_tier=to_tier,
            from_tier=from_tier,
            approved_by=approved_by,
            notes=notes,
            pipeline_metadata=metadata,
        ))


async def promote(
    postgres: Any,
    *,
    data_id: str,
    source_code: str,
    series_key: str,
    variable_code: str,
    observations_written: int,
    periods: tuple[str, str] | None,
    release_id: str | None,
    approved_by: str = "ankt-connector",
) -> None:
    """Walk one stored artifact from bronze to gold.

    Called after the observations are committed, never before: gold claims the
    figures are queryable, and that claim has to be true when it is written.
    """
    span = f"{periods[0]}..{periods[1]}" if periods else "none"
    common = {
        "ankt_source": source_code,
        "series_key": series_key,
        "variable_code": variable_code,
        "release_id": release_id,
    }

    await _set_tier(postgres, data_id, Tier.SILVER)
    await _record(
        postgres, data_id, Tier.SILVER, Tier.BRONZE, approved_by,
        f"Extracted {observations_written} observations ({span}) into ankt.observation",
        {**common, "stage": "silver", "extractor": "ankt_connectors.runner",
         "store": "ankt.observation", "observations": observations_written,
         "chunks_accepted": 0,
         # Says why the count above is zero, so the absence reads as a
         # decision rather than as a stage that silently did nothing.
         "chunking_skipped": "structured series; figures are rows, not prose"},
    )

    await _set_tier(postgres, data_id, Tier.GOLD)
    await _record(
        postgres, data_id, Tier.GOLD, Tier.SILVER, approved_by,
        f"Published {observations_written} approved observations for {variable_code}",
        {**common, "stage": "gold", "store": "ankt.observation",
         "observations_published": observations_written,
         "queryable_via": "libs.indicator.observation (as_of point-in-time)",
         "embedded": False,
         "embedding_skipped": "numeric series; retrieval is by variable and period"},
    )


_STRANDED = """
SELECT d.data_id,
       d.metadata->>'ankt_source' AS source_code,
       d.metadata->>'series_key'  AS series_key,
       COALESCE(o.variable_code, '?')     AS variable_code,
       COALESCE(o.observations, 0)        AS observations,
       o.first_period, o.last_period, o.release_id
  FROM "KBData" d
  LEFT JOIN LATERAL (
        SELECT v.code AS variable_code, count(*) AS observations,
               min(obs.period) AS first_period, max(obs.period) AS last_period,
               max(obs.release_id::text) AS release_id
          FROM ankt.observation obs
          JOIN ankt.variable v ON v.variable_id = obs.variable_id
         WHERE obs.source_ref->>'artifact_data_id' = d.data_id::text
         GROUP BY v.code
       ) o ON true
 WHERE d.tenant_id = :tenant_id
   AND d.metadata->>'source_type' = 'api'
   AND d.current_tier = 'bronze'
   AND d.is_deleted = false
 ORDER BY d.created_at
"""


async def backfill(session: Any, postgres: Any, tenant_id: Any) -> list[dict[str, Any]]:
    """Walk artifacts that were stored before this module existed.

    Eight documents were already sitting at bronze when promotion was added,
    with their observations long since published. Re-running the connector
    would not move them: the responses are byte-identical, so `store` returns
    `already_present` and the runner correctly declines to redo work. They need
    the transitions recorded once, from what the rows themselves show.
    """
    from services.ankt_connectors.db import fetch_all

    rows = await fetch_all(session, _STRANDED, tenant_id=tenant_id)
    done = []
    for row in rows:
        periods = (
            (row["first_period"], row["last_period"])
            if row["first_period"] and row["last_period"] else None
        )
        await promote(
            postgres,
            data_id=str(row["data_id"]),
            source_code=row["source_code"] or "?",
            series_key=row["series_key"] or "?",
            variable_code=row["variable_code"],
            observations_written=int(row["observations"]),
            periods=periods,
            release_id=row["release_id"],
            approved_by="ankt-connector-backfill",
        )
        done.append({"data_id": str(row["data_id"]),
                     "series_key": row["series_key"],
                     "variable_code": row["variable_code"],
                     "observations": int(row["observations"])})
    return done
