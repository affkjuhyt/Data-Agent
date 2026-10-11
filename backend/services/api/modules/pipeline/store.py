"""Database helpers shared by the pipeline routes and the module's public functions."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterable
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import pipeline as pl
from services.api.kernel.auth import Principal
from services.api.kernel.problems import FieldError, Problem

ROLE = "svc_pipeline"


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def emit(
    session: AsyncSession,
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    """One pipeline.outbox row in the caller's transaction (D1 rule 8)."""
    session.add(
        pl.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=jsonable_encoder(payload),
        )
    )


def invalid(field: str, detail: str) -> Problem:
    return Problem(
        422,
        "validation_failed",
        detail,
        errors=[FieldError(field=field, code="invalid", message=detail)],
    )


async def require_datasets(
    session: AsyncSession,
    principal: Principal,
    data_space: uuid.UUID,
    dataset_ids: Iterable[uuid.UUID],
    field: str,
) -> None:
    """Every id is a live dataset of this Data Space (catalog_read grant); else 422 on `field`."""
    wanted = set(dataset_ids)
    if not wanted:
        return
    found = set(
        (
            await session.scalars(
                select(catalog.Dataset.id).where(
                    catalog.Dataset.id.in_(wanted),
                    catalog.Dataset.org_id == principal.org_id,
                    catalog.Dataset.data_space_id == data_space,
                    catalog.Dataset.deleted_at.is_(None),
                )
            )
        ).all()
    )
    missing = wanted - found
    if missing:
        raise invalid(field, f"{len(missing)} dataset(s) are not in this Data Space.")
