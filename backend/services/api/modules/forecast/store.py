"""Lookups and the outbox writer the forecast routes share.

Only forecast_job and forecast_model carry data_space_id; every other forecast row reaches its Data
Space through its model, so each lookup here joins forecast_model and answers 404 for a row of
another Data Space exactly as for a missing one (A1.7).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import forecast as fc
from services.api.kernel.auth import Principal
from services.api.kernel.pagination import PageRequest, encode_page_token, invalid_page_token
from services.api.kernel.problems import Problem

ROLE = "svc_forecast"


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
    """One forecast.outbox row in the caller's transaction (D1 rule 8): ids and states only."""

    def plain(value: Any) -> Any:
        if isinstance(value, uuid.UUID):
            return str(value)
        if isinstance(value, datetime.date):
            return value.isoformat()
        if isinstance(value, list):
            return [plain(v) for v in value]
        return value

    session.add(
        fc.Outbox(
            id=uuid7(),
            org_id=principal.org_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload={k: plain(v) for k, v in payload.items()},
        )
    )


def keyset(query: Select[Any], page: PageRequest, created: Any, ident: Any) -> Select[Any]:
    """Newest first on (created, id); the cursor is the last row's pair (A1.4)."""
    if page.cursor:
        try:
            after = (
                datetime.datetime.fromisoformat(page.cursor["created_at"]),
                uuid.UUID(page.cursor["id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(created, ident) < after)
    return query.order_by(created.desc(), ident.desc()).limit(page.page_size + 1)


def page_token(rows: list[Any], page: PageRequest, created: str = "created_at") -> str:
    if len(rows) <= page.page_size:
        return ""
    last = rows[page.page_size - 1]
    return encode_page_token({"created_at": getattr(last, created).isoformat(), "id": str(last.id)})


async def job(
    session: AsyncSession,
    job_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> fc.ForecastJob:
    query = select(fc.ForecastJob).where(
        fc.ForecastJob.id == job_id,
        fc.ForecastJob.org_id == principal.org_id,
        fc.ForecastJob.data_space_id == data_space,
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Forecast job not found.")
    return row


async def model(
    session: AsyncSession,
    model_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> fc.ForecastModel:
    query = select(fc.ForecastModel).where(
        fc.ForecastModel.id == model_id,
        fc.ForecastModel.org_id == principal.org_id,
        fc.ForecastModel.data_space_id == data_space,
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Forecast model not found.")
    return row


async def version(
    session: AsyncSession,
    version_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> tuple[fc.ModelVersion, fc.ForecastModel]:
    query = (
        select(fc.ModelVersion, fc.ForecastModel)
        .join(fc.ForecastModel, fc.ForecastModel.id == fc.ModelVersion.model_id)
        .where(
            fc.ModelVersion.id == version_id,
            fc.ModelVersion.org_id == principal.org_id,
            fc.ForecastModel.data_space_id == data_space,
        )
    )
    if lock:
        # Both rows: publishing repoints the model, and two approvals of one model serialise.
        query = query.with_for_update()
    found = (await session.execute(query)).first()
    if found is None:
        raise Problem(404, "not_found", "Model version not found.")
    return found[0], found[1]


async def formula_model(
    session: AsyncSession,
    formula_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
) -> tuple[fc.FormulaModel, fc.ModelVersion, fc.ForecastModel]:
    found = (
        await session.execute(
            select(fc.FormulaModel, fc.ModelVersion, fc.ForecastModel)
            .join(fc.ModelVersion, fc.ModelVersion.id == fc.FormulaModel.model_version_id)
            .join(fc.ForecastModel, fc.ForecastModel.id == fc.ModelVersion.model_id)
            .where(
                fc.FormulaModel.id == formula_id,
                fc.FormulaModel.org_id == principal.org_id,
                fc.ForecastModel.data_space_id == data_space,
            )
        )
    ).first()
    if found is None:
        raise Problem(404, "not_found", "Formula model not found.")
    return found[0], found[1], found[2]


async def ledger_entry(
    session: AsyncSession,
    entry_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
) -> fc.LedgerEntry:
    row = await session.scalar(
        select(fc.LedgerEntry)
        .join(fc.ForecastModel, fc.ForecastModel.id == fc.LedgerEntry.forecast_model_id)
        .where(
            fc.LedgerEntry.id == entry_id,
            fc.LedgerEntry.org_id == principal.org_id,
            fc.ForecastModel.data_space_id == data_space,
        )
    )
    if row is None:
        raise Problem(404, "not_found", "Ledger entry not found.")
    return row


def review_note(
    row: fc.ModelVersion, principal: Principal, action: str, text: str | None
) -> None:
    """Keeps a submit/approve/reject note with the version (AC-15-04-02: the reason stays with the
    decision). model_version has no column for it, so it goes in model_card.review."""
    card = dict(row.model_card or {})
    review = list(card.get("review") or [])
    review.append(
        {"action": action, "by": str(principal.user_id), "at": now().isoformat(), "note": text}
    )
    card["review"] = review
    row.model_card = card
