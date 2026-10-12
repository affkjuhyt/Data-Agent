"""The forecast ledger (UC-15-08): issued forecasts, their scores and the calibration of a model.

Lines are written by ml-forecast before their period and scored when the published price arrives;
ledger_entry_guard keeps the forecast half immutable and ledger_entry_scored_event emits
forecast.ledger.scored / .missed. The API reads the ledger and asks for a rescore (202); the
scoring itself, against the corrected dataset version, is ml-forecast's.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import forecast as fc
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    PageRequest,
    encode_page_token,
    invalid_page_token,
    page_request,
)
from services.api.kernel.problems import Problem
from services.api.modules.forecast import rules, store
from services.api.modules.forecast.schemas import (
    Accepted,
    Calibration,
    LedgerEntryView,
    LedgerPage,
    LedgerScore,
)
from services.api.modules.forecast.store import ROLE

router = APIRouter()

EntryId = Annotated[uuid.UUID, Path(alias="id")]
ModelId = Annotated[uuid.UUID, Path(alias="id")]
DEFAULT_LAST_N = 20


async def _calibration(
    session: AsyncSession,
    principal: Principal,
    model_id: uuid.UUID,
    product_code: str | None,
    last_n: int,
) -> Calibration:
    entry = fc.LedgerEntry
    query = select(entry).where(
        entry.org_id == principal.org_id,
        entry.forecast_model_id == model_id,
        entry.status == "scored",
    )
    if product_code:
        query = query.where(entry.scope["product_code"].astext == product_code)
    rows = (await session.scalars(query.order_by(entry.period.desc()).limit(last_n))).all()
    figures = rules.calibration(
        [
            rules.ScoredPeriod(
                actual=r.actual,
                baseline_value=r.baseline_value,
                abs_error=r.abs_error,
                crps=r.crps,
                in80=r.in80,
                in95=r.in95,
                miss=r.miss,
            )
            for r in rows
            if r.actual is not None
        ]
    )
    return Calibration(
        forecast_model_id=model_id,
        product_code=product_code,
        last_n=last_n,
        n_scored=figures.n_scored,
        coverage80=figures.coverage80,
        coverage95=figures.coverage95,
        mae=figures.mae,
        crps=figures.crps,
        crps_naive=figures.crps_naive,
        miss_count=figures.miss_count,
        insufficient=figures.insufficient,
    )


@router.get(
    "/v1/forecast-ledger",
    response_model=LedgerPage,
    **endpoint("GET", "/v1/forecast-ledger", operation_id="listForecastLedger"),
)
async def list_ledger(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    forecast_model_id: uuid.UUID | None = None,
    product_code: Annotated[str | None, Query(max_length=50)] = None,
    period_from: Annotated[datetime.date | None, Query(alias="from")] = None,
    period_to: Annotated[datetime.date | None, Query(alias="to")] = None,
    status: Annotated[str | None, Query(pattern="^(open|scored|void)$")] = None,
    miss: bool | None = None,
) -> LedgerPage:
    entry = fc.LedgerEntry
    query = (
        select(entry)
        .join(fc.ForecastModel, fc.ForecastModel.id == entry.forecast_model_id)
        .where(entry.org_id == principal.org_id, fc.ForecastModel.data_space_id == data_space)
    )
    if forecast_model_id:
        query = query.where(entry.forecast_model_id == forecast_model_id)
    if product_code:
        query = query.where(entry.scope["product_code"].astext == product_code)
    if period_from:
        query = query.where(entry.period >= period_from)
    if period_to:
        query = query.where(entry.period <= period_to)
    if status:
        query = query.where(entry.status == status)
    if miss is not None:
        query = query.where(entry.miss.is_(miss))
    if page.cursor:
        try:
            after = (
                datetime.date.fromisoformat(page.cursor["period"]),
                datetime.datetime.fromisoformat(page.cursor["created_at"]),
                uuid.UUID(page.cursor["id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        # The cursor follows the sort: on (created_at, id) alone, a later page would skip lines of
        # an older period issued after the last line shown.
        query = query.where(tuple_(entry.period, entry.created_at, entry.id) < after)
    async with transaction(ROLE, principal) as session:
        # Newest first (SCR-71): by period, then issue time.
        rows = list(
            (
                await session.scalars(
                    query.order_by(
                        entry.period.desc(), entry.created_at.desc(), entry.id.desc()
                    ).limit(page.page_size + 1)
                )
            ).all()
        )
        calibration = None
        if forecast_model_id is not None:
            # BR-185: the ledger of one model shows its calibration over the last N periods.
            await store.model(session, forecast_model_id, principal, data_space)
            calibration = await _calibration(
                session, principal, forecast_model_id, product_code, DEFAULT_LAST_N
            )
    token = ""
    if len(rows) > page.page_size:
        last = rows[page.page_size - 1]
        token = encode_page_token(
            {
                "period": last.period.isoformat(),
                "created_at": last.created_at.isoformat(),
                "id": str(last.id),
            }
        )
    return LedgerPage(
        items=[LedgerEntryView.model_validate(r) for r in rows[: page.page_size]],
        next_page_token=token,
        calibration=calibration,
    )


@router.get(
    "/v1/forecast-ledger/{id}",
    response_model=LedgerEntryView,
    **endpoint("GET", "/v1/forecast-ledger/{id}", operation_id="getForecastLedgerEntry"),
)
async def get_ledger_entry(
    entry_id: EntryId, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> LedgerEntryView:
    async with transaction(ROLE, principal) as session:
        row = await store.ledger_entry(session, entry_id, principal, data_space)
    return LedgerEntryView.model_validate(row)


@router.post(
    "/v1/forecast-ledger/{id}:score",
    status_code=202,
    response_model=Accepted,
    **endpoint("POST", "/v1/forecast-ledger/{id}:score", operation_id="scoreForecastLedgerEntry"),
)
async def rescore_ledger_entry(
    body: LedgerScore,
    entry_id: EntryId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        row = await store.ledger_entry(session, entry_id, principal, data_space)
        if row.status == "void":
            raise Problem(409, "invalid_state", "The period did not take place; nothing to score.")
        if row.actual is None:
            raise Problem(409, "invalid_state", "The period has no published price yet.")
        # ml-forecast rescores against the dataset's newest version; the old score stays in
        # accuracy_log (UC-15-08 alt 1). The reason is the caller's text: audit keeps it with
        # the request, the event carries ids only.
        store.emit(
            session,
            principal,
            "ledger_entry",
            row.id,
            "forecast.ledger.rescore_requested",
            ledger_entry_id=row.id,
            forecast_model_id=row.forecast_model_id,
            data_space_id=data_space,
            status=row.status,
            requested_by=principal.user_id,
        )
    response.headers["Location"] = f"/v1/forecast-ledger/{row.id}"
    return Accepted(id=row.id, status="queued", poll_after_ms=rules.POLL_AFTER_MS)


@router.get(
    "/v1/forecast-models/{id}/calibration",
    response_model=Calibration,
    **endpoint(
        "GET", "/v1/forecast-models/{id}/calibration", operation_id="getForecastCalibration"
    ),
)
async def get_calibration(
    model_id: ModelId,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    last_n: Annotated[int, Query(ge=1, le=500)] = DEFAULT_LAST_N,
    product_code: Annotated[str | None, Query(max_length=50)] = None,
) -> Calibration:
    async with transaction(ROLE, principal) as session:
        await store.model(session, model_id, principal, data_space)
        return await _calibration(session, principal, model_id, product_code, last_n)
