"""forecast_metric: create or reuse a forecast job and land mean + intervals on the workspace.

Does not HTTP-loopback to the API: same tables and outbox the forecast routes write, so ml-forecast
picks the job up. What-if / studio stay on the Forecast screens; Ask only reads a result with
confidence bands (exploratory trust — never certified semantic).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select

from libs import forecast_release as release
from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import forecast as fc
from services.worker_agent.agentic.tools.base import Tool, ToolContext, ToolResult

ROLE = "svc_forecast"
FREQUENCIES = frozenset({"day", "week", "month", "quarter"})
TERMINAL = frozenset({"ready", "failed", "cancelled"})
# Monthly ETA target is 15 minutes; the tool waits that long then fails with the job id so Ask
# can still offer "open studio" rather than inventing numbers.
POLL_SECONDS = 5.0
ETA = {
    "day": dt.timedelta(minutes=60),
    "week": dt.timedelta(minutes=30),
    "month": dt.timedelta(minutes=15),
    "quarter": dt.timedelta(minutes=15),
}
COLUMNS = ["period", "mean", "lo80", "hi80", "lo95", "hi95"]


class ForecastMetricArgs(BaseModel):
    metric: str = Field(description="mã chỉ số cần dự báo (trong catalog)")
    horizon: int = Field(description="số kỳ dự báo phía trước, ví dụ 3 hoặc 12", ge=1, le=120)
    frequency: str | None = Field(
        default=None,
        description="day|week|month|quarter; bỏ trống thì lấy tần suất của mô hình đã phát hành",
    )
    scope: dict[str, Any] = Field(
        default_factory=dict,
        description="phạm vi theo chiều (cùng shape với mô hình dự báo), có thể rỗng",
    )


def _plain(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def _forecast_is_current(periods: list[dt.date], today: dt.date) -> bool:
    first = min(periods, default=None)
    return first is not None and first > today


class ForecastMetric(Tool):
    name = "forecast_metric"
    title = "Dự báo chỉ số"
    description = (
        "dự báo một chỉ số đã có mô hình (mean + khoảng 80%/95%); kết quả là dataset thăm dò "
        "(exploratory), không phải chỉ số chuẩn đã chứng nhận; mở studio để what-if"
    )
    Args = ForecastMetricArgs
    timeout_seconds = 960.0

    async def run(self, ctx: ToolContext, args: ForecastMetricArgs) -> ToolResult:  # type: ignore[override]
        frequency = (args.frequency or "").strip().lower() or None
        if frequency is not None and frequency not in FREQUENCIES:
            return ToolResult(
                False,
                f"tần suất {args.frequency} không hợp lệ (day|week|month|quarter)",
                failure="arguments",
            )
        if args.horizon < 1:
            return ToolResult(False, "horizon phải ≥ 1", failure="arguments")

        try:
            job, predictions, version = await _resolve(
                ctx,
                metric=args.metric,
                horizon=args.horizon,
                frequency=frequency,
                scope=dict(args.scope or {}),
            )
        except _NoModel as exc:
            return ToolResult(False, str(exc), failure="unavailable")

        if job.status == "failed":
            reason = (job.progress or {}).get("error") or (job.progress or {}).get("reason") or ""
            return ToolResult(
                False,
                f"dự báo thất bại{': ' + str(reason) if reason else ''}",
                detail={"forecast_id": str(job.id), "status": job.status},
                failure="logic",
            )
        if job.status == "cancelled":
            return ToolResult(
                False,
                "việc dự báo đã bị huỷ",
                detail={"forecast_id": str(job.id)},
                failure="unavailable",
            )
        if job.status != "ready" or not predictions:
            return ToolResult(
                False,
                "dự báo chưa xong trong thời gian chờ; mở studio / theo dõi job",
                detail={"forecast_id": str(job.id), "status": job.status},
                failure="unavailable",
            )

        rows = [
            [
                p.period.isoformat(),
                float(p.point),
                float(p.lo80),
                float(p.hi80),
                float(p.lo95),
                float(p.hi95),
            ]
            for p in predictions[: args.horizon]
        ]
        algorithm = version.algorithm if version is not None else None
        title = f"Dự báo {args.metric} ({job.frequency}, {args.horizon} kỳ)"
        ds = ctx.ws.add(
            title=title,
            columns=list(COLUMNS),
            rows=rows,
            trust="exploratory",
            step_id=ctx.step_id,
            tool=self.name,
            reading={
                "metric": args.metric,
                "forecast_id": str(job.id),
                "frequency": job.frequency,
                "horizon": args.horizon,
                "cutoff_at": job.cutoff_at.isoformat() if job.cutoff_at else None,
                "algorithm": algorithm,
                "model_version_id": str(job.model_version_id) if job.model_version_id else None,
            },
        )
        detail = {
            "dataset": ds.id,
            "forecast_id": str(job.id),
            "metric": args.metric,
            "frequency": job.frequency,
            "horizon": args.horizon,
            "row_count": len(rows),
            "cutoff_at": _plain(job.cutoff_at),
            "algorithm": algorithm,
            "status": job.status,
        }
        return ToolResult(True, ds.preview(), [ds.id], detail)


class _NoModel(Exception):
    pass


async def _resolve(
    ctx: ToolContext,
    *,
    metric: str,
    horizon: int,
    frequency: str | None,
    scope: dict[str, Any],
) -> tuple[fc.ForecastJob, list[fc.Prediction], fc.ModelVersion | None]:
    """Reuse a current ready job when possible; else queue one and poll until terminal."""
    async with scoped_session(ROLE, ctx.org_id, ctx.user_id) as session:
        model = await _released_model(session, ctx, metric, frequency, scope)
        if model is None:
            # Frequency omitted: try any released model for the metric+scope.
            if frequency is None:
                model = await _any_released_model(session, ctx, metric, scope)
            if model is None:
                raise _NoModel(
                    f"không có mô hình dự báo đã phát hành cho chỉ số {metric}"
                    + (f" / {frequency}" if frequency else "")
                )
        freq = model.frequency
        current = await _current_job(session, ctx, model, horizon, scope)
        if current is not None:
            preds = await _base_predictions(session, current.id)
            version = (
                await session.get(fc.ModelVersion, current.model_version_id)
                if current.model_version_id
                else None
            )
            return current, preds[:horizon], version

        row = fc.ForecastJob(
            id=uuid7(),
            org_id=ctx.org_id,
            data_space_id=ctx.space_id,
            question_id=ctx.question_id,
            requested_by=ctx.user_id,
            metric_code=metric,
            scope=scope,
            assumptions=[],
            frequency=freq,
            horizon=horizon,
            cutoff_at=release.now(),
            status="queued",
            eta_at=release.now() + ETA.get(freq, ETA["month"]),
            notify_channels=[],
            progress={"step": "queued", "purpose": "forecast"},
            model_version_id=None,
            created_by=ctx.user_id,
        )
        session.add(row)
        release.job_changed(session, row, None)
        await session.flush()
        job_id = row.id

    return await _poll(ctx, job_id, horizon, freq)


async def _poll(
    ctx: ToolContext, job_id: uuid.UUID, horizon: int, frequency: str
) -> tuple[fc.ForecastJob, list[fc.Prediction], fc.ModelVersion | None]:
    wait = ETA.get(frequency, ETA["month"]).total_seconds()
    deadline = asyncio.get_event_loop().time() + wait
    while True:
        async with scoped_session(ROLE, ctx.org_id, ctx.user_id) as session:
            row = await session.scalar(
                select(fc.ForecastJob).where(
                    fc.ForecastJob.id == job_id,
                    fc.ForecastJob.org_id == ctx.org_id,
                    fc.ForecastJob.data_space_id == ctx.space_id,
                )
            )
            if row is None:
                raise _NoModel("việc dự báo không còn tồn tại")
            if row.status in TERMINAL:
                preds = await _base_predictions(session, row.id) if row.status == "ready" else []
                version = (
                    await session.get(fc.ModelVersion, row.model_version_id)
                    if row.model_version_id
                    else None
                )
                return row, preds[:horizon], version
        if asyncio.get_event_loop().time() >= deadline:
            async with scoped_session(ROLE, ctx.org_id, ctx.user_id) as session:
                row = await session.get(fc.ForecastJob, job_id)
                assert row is not None
                return row, [], None
        await asyncio.sleep(POLL_SECONDS)


async def _released_model(
    session: Any,
    ctx: ToolContext,
    metric: str,
    frequency: str | None,
    scope: dict[str, Any],
) -> fc.ForecastModel | None:
    query = select(fc.ForecastModel).where(
        fc.ForecastModel.org_id == ctx.org_id,
        fc.ForecastModel.data_space_id == ctx.space_id,
        fc.ForecastModel.metric_code == metric,
        fc.ForecastModel.scope == scope,
        fc.ForecastModel.status != "retired",
        fc.ForecastModel.current_version_id.is_not(None),
    )
    if frequency is not None:
        query = query.where(fc.ForecastModel.frequency == frequency)
    return await session.scalar(query)


async def _any_released_model(
    session: Any, ctx: ToolContext, metric: str, scope: dict[str, Any]
) -> fc.ForecastModel | None:
    return await session.scalar(
        select(fc.ForecastModel)
        .where(
            fc.ForecastModel.org_id == ctx.org_id,
            fc.ForecastModel.data_space_id == ctx.space_id,
            fc.ForecastModel.metric_code == metric,
            fc.ForecastModel.scope == scope,
            fc.ForecastModel.status != "retired",
            fc.ForecastModel.current_version_id.is_not(None),
        )
        .order_by(fc.ForecastModel.updated_at.desc())
        .limit(1)
    )


async def _current_job(
    session: Any,
    ctx: ToolContext,
    model: fc.ForecastModel,
    horizon: int,
    scope: dict[str, Any],
) -> fc.ForecastJob | None:
    candidate = await session.scalar(
        select(fc.ForecastJob)
        .where(
            fc.ForecastJob.org_id == ctx.org_id,
            fc.ForecastJob.data_space_id == ctx.space_id,
            fc.ForecastJob.metric_code == model.metric_code,
            fc.ForecastJob.frequency == model.frequency,
            fc.ForecastJob.scope == scope,
            fc.ForecastJob.assumptions == [],
            fc.ForecastJob.status == "ready",
            fc.ForecastJob.model_version_id == model.current_version_id,
            fc.ForecastJob.horizon >= horizon,
        )
        .order_by(fc.ForecastJob.cutoff_at.desc())
        .limit(1)
    )
    if candidate is None:
        return None
    periods = [p.period for p in await _base_predictions(session, candidate.id)]
    if not _forecast_is_current(periods, release.now().date()):
        return None
    return candidate


async def _base_predictions(session: Any, job_id: uuid.UUID) -> list[fc.Prediction]:
    return list(
        (
            await session.scalars(
                select(fc.Prediction)
                .where(fc.Prediction.job_id == job_id, fc.Prediction.scenario_id.is_(None))
                .order_by(fc.Prediction.period, fc.Prediction.model_version_id)
            )
        ).all()
    )
