"""The release rules of a forecast model version that the api and worker-agent share.

A version is submitted for approval from two places: POST /v1/forecast-model-versions/{id}:submit
(the api) and the end of a training job (worker-agent, forecasting.py). worker-agent may not import
the api (.importlinter), so the rule lives here and both call it: which state a version is
submitted from, what it needs (a finished backtest beating seasonal naive, BR-158), the note kept
with it and the forecast.outbox event Inbox opens the owner's request on. The job's state event is here for
the same reason: the api queues and cancels a job, worker-agent moves it through training. The DB
still guards the life cycle itself (routines/forecast.sql model_version_guard).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import forecast as fc

__all__ = [
    "SubmitRefused",
    "emit",
    "job_changed",
    "latest_backtest",
    "now",
    "review_note",
    "submit",
]

JOB_CHANGED = "forecast.job.state_changed"
SUBMITTED = "forecast.model_version.submitted"


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _plain(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def emit(
    session: AsyncSession,
    org_id: uuid.UUID,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> None:
    """One forecast.outbox row in the caller's transaction (D1 rule 8): ids and states only."""
    session.add(
        fc.Outbox(
            id=uuid7(),
            org_id=org_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload={k: _plain(v) for k, v in payload.items()},
        )
    )


def job_changed(session: AsyncSession, row: fc.ForecastJob, previous: str | None) -> None:
    """The job's forecast.job.state_changed, written with each change of its status."""
    emit(
        session,
        row.org_id,
        "forecast_job",
        row.id,
        JOB_CHANGED,
        job_id=row.id,
        data_space_id=row.data_space_id,
        question_id=row.question_id,
        purpose=(row.progress or {}).get("purpose", "forecast"),
        previous_status=previous,
        status=row.status,
        model_version_id=row.model_version_id,
    )


def review_note(row: fc.ModelVersion, by: uuid.UUID, action: str, text: str | None) -> None:
    """Keeps a submit/approve/reject note with the version (AC-15-04-02: the reason stays with the
    decision). model_version has no column for it, so it goes in model_card.review."""
    card = dict(row.model_card or {})
    review = list(card.get("review") or [])
    review.append({"action": action, "by": str(by), "at": now().isoformat(), "note": text})
    card["review"] = review
    row.model_card = card


async def latest_backtest(session: AsyncSession, row: fc.ModelVersion) -> fc.BacktestRun | None:
    if row.backtest_id is not None:
        return await session.get(fc.BacktestRun, row.backtest_id)
    # Before the version points at one, its latest run is the one under way.
    found: fc.BacktestRun | None = await session.scalar(
        select(fc.BacktestRun)
        .where(fc.BacktestRun.model_version_id == row.id)
        .order_by(fc.BacktestRun.started_at.desc())
        .limit(1)
    )
    return found


class SubmitRefused(Exception):
    """The version may not be submitted; `status` and `code` are the api's problem for it."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


async def submit(
    session: AsyncSession,
    row: fc.ModelVersion,
    model: fc.ForecastModel,
    *,
    by: uuid.UUID,
    note: str | None,
) -> None:
    """A draft version sent to its metric owner for approval. The caller holds both rows locked."""
    if row.status != "draft":
        raise SubmitRefused(409, "invalid_state", f"The version is {row.status}, not draft.")
    if row.algorithm == "seasonal_naive":
        # UC-15-03 E1: the baseline is answered with its label, never sent for release.
        raise SubmitRefused(
            422,
            "business_rule_violated",
            "The seasonal naive baseline is not submitted for release (BR-158).",
        )
    run = await latest_backtest(session, row)
    if run is None or run.finished_at is None or run.mase >= 1:
        mase = "none" if run is None else str(run.mase)
        raise SubmitRefused(
            422,
            "business_rule_violated",
            f"Release needs a finished backtest beating seasonal naive, MASE < 1; MASE is {mase} (BR-158).",
        )
    row.backtest_id = run.id
    row.status = "pending_approval"
    row.updated_by, row.updated_at = by, now()
    review_note(row, by, "submit", note)
    # Inbox opens the forecast_model HITL request for the metric owner (UC-15-04 trigger).
    emit(
        session,
        row.org_id,
        "model_version",
        row.id,
        SUBMITTED,
        model_version_id=row.id,
        model_id=model.id,
        data_space_id=model.data_space_id,
        hitl_kind="forecast_model",
        assignee_id=model.owner_id,
        backtest_id=run.id,
        status=row.status,
    )
