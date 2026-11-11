"""The release rules of a forecast model version that the api and worker-agent share.

A version is submitted for approval from two places: POST /v1/forecast-model-versions/{id}:submit
(the api) and the end of a training job (worker-agent, forecasting.py). Approving it is the same:
POST …versions/{id}:approve and the inbox proposal's apply both call `approve` here. worker-agent
may not import the api (.importlinter), so the rule lives here and both call it: which state a
version is submitted from, what it needs (a finished backtest beating seasonal naive, BR-158), who
may decide (the metric owner, never the builder unless the org allows it), the note kept with it
and the forecast.outbox events. The job's state event is here for the same reason: the api queues
and cancels a job, worker-agent moves it through training. The DB still guards the life cycle
itself (routines/forecast.sql model_version_guard). Publishing takes the experimental label off
the version's predictions; the figures stay append-only.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import forecast as fc

__all__ = [
    "ApproveRefused",
    "SubmitRefused",
    "approve",
    "emit",
    "job_changed",
    "latest_backtest",
    "locked",
    "may_decide",
    "now",
    "reject",
    "review_note",
    "submit",
]

# What a model may serve with (BR-160). Duplicated from the api's rules so this lib stays free of it.
_SERVING = ("published", "degraded")

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


class ApproveRefused(Exception):
    """The version may not be decided; `status` and `code` are the api's problem for it."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def may_decide(
    row: fc.ModelVersion, model: fc.ForecastModel, *, by: uuid.UUID, allow_self: bool
) -> bool:
    """BR-158: the metric owner decides, never whoever built the version, unless the org allows
    it. True when the decider built the version (a self-approval)."""
    if by == row.created_by and not allow_self:
        raise ApproveRefused(
            403,
            "policy_denied",
            "Whoever built the version cannot decide on it (BR-158).",
        )
    if model.owner_id != by:
        raise ApproveRefused(
            403, "policy_denied", "Only the metric owner decides on a forecast model (BR-158)."
        )
    return by == row.created_by


async def locked(
    session: AsyncSession, version_id: uuid.UUID, org_id: uuid.UUID, data_space_id: uuid.UUID
) -> tuple[fc.ModelVersion, fc.ForecastModel]:
    """The version and its model, locked, or a not-found refusal for another Data Space."""
    found = (
        await session.execute(
            select(fc.ModelVersion, fc.ForecastModel)
            .join(fc.ForecastModel, fc.ForecastModel.id == fc.ModelVersion.model_id)
            .where(
                fc.ModelVersion.id == version_id,
                fc.ModelVersion.org_id == org_id,
                fc.ForecastModel.data_space_id == data_space_id,
            )
            .with_for_update()
        )
    ).first()
    if found is None:
        raise ApproveRefused(404, "not_found", "Model version not found.")
    return found[0], found[1]


async def _clear_experimental(session: AsyncSession, version_id: uuid.UUID) -> None:
    """A published version's forecasts are no longer experimental (BR-158). The numbers stay."""
    await session.execute(
        update(fc.Prediction)
        .where(
            fc.Prediction.model_version_id == version_id,
            fc.Prediction.is_experimental.is_(True),
        )
        .values(is_experimental=False)
    )


async def approve(
    session: AsyncSession,
    row: fc.ModelVersion,
    model: fc.ForecastModel,
    *,
    by: uuid.UUID,
    note: str | None,
    allow_self: bool,
    idempotent: bool = False,
) -> bool:
    """Publish `row` and point the model at it. The caller holds both rows locked.

    True when `by` built the version. `idempotent` lets a second apply of an already published
    version succeed: the inbox writes the decision and the worker applies the event, and either
    may arrive first.
    """
    if idempotent and row.status == "published" and model.current_version_id == row.id:
        await _clear_experimental(session, row.id)
        return row.created_by == by
    if row.status != "pending_approval":
        raise ApproveRefused(
            409, "invalid_state", f"The version is {row.status}, not pending_approval."
        )
    self_built = may_decide(row, model, by=by, allow_self=allow_self)
    if model.status == "retired":
        raise ApproveRefused(409, "invalid_state", "The model is retired; it takes no new version.")
    moment = now()
    previous_id = model.current_version_id
    if previous_id is not None and previous_id != row.id:
        old = await session.get(fc.ModelVersion, previous_id, with_for_update=True)
        if old is not None and old.status in _SERVING:
            old.status = "retired"
            old.updated_by, old.updated_at = by, moment
            emit(
                session,
                row.org_id,
                "model_version",
                old.id,
                "forecast.model_version.retired",
                model_version_id=old.id,
                model_id=model.id,
                data_space_id=model.data_space_id,
                status=old.status,
            )
            # One published version per model (partial unique index): retire first.
            await session.flush()
    row.status = "published"
    row.approved_by, row.approved_at = by, moment
    row.updated_by, row.updated_at = by, moment
    review_note(row, by, "approve", note)
    previous_model_status = model.status
    model.current_version_id = row.id
    model.status = "active"
    model.updated_by, model.updated_at = by, moment
    emit(
        session,
        row.org_id,
        "model_version",
        row.id,
        "forecast.model.published",
        model_version_id=row.id,
        model_id=model.id,
        data_space_id=model.data_space_id,
        previous_version_id=previous_id,
        status=row.status,
        approved_by=by,
    )
    if previous_model_status != model.status:
        emit(
            session,
            row.org_id,
            "forecast_model",
            model.id,
            "forecast.model.status_changed",
            model_id=model.id,
            data_space_id=model.data_space_id,
            previous_status=previous_model_status,
            status=model.status,
        )
    await session.flush()
    await _clear_experimental(session, row.id)
    return self_built


async def reject(
    session: AsyncSession,
    row: fc.ModelVersion,
    model: fc.ForecastModel,
    *,
    by: uuid.UUID,
    reason: str | None,
    allow_self: bool,
    idempotent: bool = False,
) -> bool:
    """Send a pending version back as rejected. True when `by` built it. The caller holds the lock."""
    if idempotent and row.status == "rejected":
        return row.created_by == by
    if row.status != "pending_approval":
        raise ApproveRefused(
            409, "invalid_state", f"The version is {row.status}, not pending_approval."
        )
    self_built = may_decide(row, model, by=by, allow_self=allow_self)
    row.status = "rejected"
    row.updated_by, row.updated_at = by, now()
    review_note(row, by, "reject", reason)
    emit(
        session,
        row.org_id,
        "model_version",
        row.id,
        "forecast.model_version.rejected",
        model_version_id=row.id,
        model_id=model.id,
        data_space_id=model.data_space_id,
        status=row.status,
        rejected_by=by,
    )
    await session.flush()
    return self_built
