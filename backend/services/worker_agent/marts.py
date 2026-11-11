"""Relation check for a data mart (UC-06-06 step 2 / E1).

data_mart.created and data_mart.updated (when relation_check_requested) load the mart's declared
relations against active context.relationship rows of its fact. A pass marks relation_check_passed
and promotes a draft to active so a product can publish; a fail leaves the draft and records the
failures in checks.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import context
from libs.dataagent_db.models import pipeline as pl
from libs.mart_model import relations_match_active
from services.worker_agent.events import Event, Handler

logger = logging.getLogger(__name__)

ROLE = "svc_pipeline"
CREATED = "data_mart.created"
UPDATED = "data_mart.updated"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _check(event: Event) -> None:
    p = event.payload
    if event.event_type == UPDATED and not p.get("relation_check_requested"):
        return
    mart_id = uuid.UUID(str(p.get("data_mart_id") or event.aggregate_id))
    space = uuid.UUID(str(p["data_space_id"]))
    who = p.get("updated_by") or p.get("created_by")
    if who is None:
        logger.info("worker-agent | data mart %s event has no actor; skip", mart_id)
        return
    actor = uuid.UUID(str(who))
    async with scoped_session(ROLE, event.org_id, actor) as session:
        mart = await session.get(pl.DataMart, mart_id)
        if mart is None or mart.data_space_id != space:
            logger.info("worker-agent | data mart %s gone; skip relation check", mart_id)
            return
        rels = (
            await session.scalars(
                select(context.Relationship).where(
                    context.Relationship.data_space_id == space,
                    context.Relationship.from_dataset_id == mart.fact_dataset_id,
                    context.Relationship.status == "active",
                )
            )
        ).all()
        targets = {r.to_dataset_id for r in rels} | {mart.fact_dataset_id}
        datasets = {
            d.id: {
                "table_name": d.table_name,
                "schema_name": d.schema_name,
                "display_name": d.display_name,
            }
            for d in (
                await session.scalars(
                    select(catalog.Dataset).where(
                        catalog.Dataset.id.in_(targets),
                        catalog.Dataset.data_space_id == space,
                        catalog.Dataset.deleted_at.is_(None),
                    )
                )
            ).all()
        }
        payload = [
            {
                "from_dataset_id": r.from_dataset_id,
                "to_dataset_id": r.to_dataset_id,
                "from_columns": list(r.from_columns or []),
                "to_columns": list(r.to_columns or []),
                "status": r.status,
            }
            for r in rels
        ]
        ok, failures = relations_match_active(
            fact_dataset_id=mart.fact_dataset_id,
            relations=list(mart.relations or []),
            relationships=payload,
            datasets=datasets,
        )
        stamp = _now()
        checks: list[dict[str, Any]] = list(mart.checks or []) if isinstance(mart.checks, list) else []
        checks = [c for c in checks if not (isinstance(c, dict) and c.get("kind") == "relation")]
        checks.append(
            {
                "kind": "relation",
                "passed": ok,
                "failures": failures,
                "checked_at": stamp.isoformat(),
            }
        )
        mart.checks = checks
        mart.relation_check_passed = ok
        if ok and mart.status == "draft":
            mart.status = "active"
        mart.updated_at = stamp
        logger.info(
            "worker-agent | data mart %s relation check %s (%s)",
            mart_id,
            "passed" if ok else "failed",
            len(failures),
        )


HANDLERS: dict[str, Handler] = {
    CREATED: _check,
    UPDATED: _check,
}
