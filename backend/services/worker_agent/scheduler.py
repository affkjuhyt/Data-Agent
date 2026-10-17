"""The clock of the data side: scheduled loads, scheduled pipelines, the daily quality pass.

Every minute the worker looks, organisation by organisation, for what is due:
- an active ingestion job whose schedule_cron fires this minute: a job_run and the
  job_run.backfill_requested event worker-ingest loads from (the same path as "Chạy lại ngay");
- an active pipeline whose cron fires this minute: a scheduled pipeline_run, skipped as
  skipped_overlap while a run of it is still going and no_overlap is set (UC-06-03);
- once a day, every active quality rule not checked in the last 24 hours (run_kind scheduled).

Row-level security scopes every session to one organisation and no role may list them all, so
the organisations are those the outbox relay has seen emit on ingest.outbox or pipeline.outbox
(every job and pipeline was created through one). A minute already served is recognised by the
run it created, so two replicas or a restart in the same minute do not fire twice.
"""

from __future__ import annotations

import datetime
import logging
import uuid

from sqlalchemy import select, text

from libs.dataagent_db import scoped_session, unscoped_session, uuid7
from libs.dataagent_db.models import ingest
from libs.dataagent_db.models import pipeline as pl
from services.worker_agent import cron, quality

logger = logging.getLogger(__name__)

QUALITY_EVERY = datetime.timedelta(hours=24)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def organisations() -> list[uuid.UUID]:
    async with unscoped_session("svc_outbox_relay") as session:
        rows = await session.execute(
            text("SELECT org_id FROM ingest.outbox UNION SELECT org_id FROM pipeline.outbox")
        )
        return [r[0] for r in rows]


def _fires(expr: str | None, minute: datetime.datetime) -> bool:
    if not expr:
        return False
    try:
        return cron.matches(expr, minute)
    except ValueError:
        return False


async def _jobs(org_id: uuid.UUID, minute: datetime.datetime) -> int:
    fired = 0
    async with scoped_session("svc_ingest", org_id) as session:
        jobs = (
            await session.scalars(
                select(ingest.IngestionJob).where(
                    ingest.IngestionJob.status == "active",
                    ingest.IngestionJob.deleted_at.is_(None),
                    ingest.IngestionJob.schedule_cron.is_not(None),
                )
            )
        ).all()
        for job in jobs:
            if not _fires(job.schedule_cron, minute):
                continue
            served = await session.scalar(
                select(ingest.JobRun.id).where(
                    ingest.JobRun.job_id == job.id, ingest.JobRun.started_at >= minute
                )
            )
            actor = job.updated_by or job.created_by
            if served is not None or actor is None:
                continue
            run = ingest.JobRun(id=uuid7(), org_id=org_id, job_id=job.id, status="running")
            session.add(run)
            session.add(
                ingest.Outbox(
                    id=uuid7(),
                    org_id=org_id,
                    aggregate_type="job_run",
                    aggregate_id=run.id,
                    event_type="job_run.backfill_requested",
                    payload={
                        "job_run_id": str(run.id),
                        "job_id": str(job.id),
                        "data_space_id": str(job.data_space_id),
                        "principal_id": str(actor),
                        "scheduled": True,
                    },
                )
            )
            fired += 1
    return fired


async def _pipelines(org_id: uuid.UUID, minute: datetime.datetime) -> int:
    fired = 0
    async with scoped_session("svc_pipeline", org_id) as session:
        rows = (
            await session.scalars(
                select(pl.Pipeline).where(
                    pl.Pipeline.status == "active",
                    pl.Pipeline.deleted_at.is_(None),
                    pl.Pipeline.cron.is_not(None),
                )
            )
        ).all()
        for row in rows:
            if not _fires(row.cron, minute):
                continue
            served = await session.scalar(
                select(pl.PipelineRun.id).where(
                    pl.PipelineRun.pipeline_id == row.id,
                    pl.PipelineRun.mode == "scheduled",
                    pl.PipelineRun.started_at >= minute,
                )
            )
            if served is not None:
                continue
            running = await session.scalar(
                select(pl.PipelineRun.id).where(
                    pl.PipelineRun.pipeline_id == row.id,
                    pl.PipelineRun.mode != "preview",
                    pl.PipelineRun.status == "running",
                )
            )
            run = pl.PipelineRun(
                id=uuid7(),
                org_id=org_id,
                pipeline_id=row.id,
                mode="scheduled",
                status="skipped_overlap" if running and row.no_overlap else "running",
                finished_at=_now() if running and row.no_overlap else None,
            )
            session.add(run)
            if run.status == "running":
                session.add(
                    pl.Outbox(
                        id=uuid7(),
                        org_id=org_id,
                        aggregate_type="pipeline_run",
                        aggregate_id=run.id,
                        event_type="pipeline_run.requested",
                        payload={
                            "pipeline_run_id": str(run.id),
                            "pipeline_id": str(row.id),
                            "data_space_id": str(row.data_space_id),
                            "mode": "scheduled",
                            "requested_by": str(row.owner_id),
                        },
                    )
                )
            fired += 1
    return fired


async def _quality(org_id: uuid.UUID) -> int:
    due_before = _now() - QUALITY_EVERY
    async with scoped_session("svc_ingest", org_id) as session:
        rules = (
            await session.scalars(
                select(ingest.QualityRule).where(ingest.QualityRule.status == "active")
            )
        ).all()
        due = []
        for rule in rules:
            last = await session.scalar(
                select(ingest.QualityResult.checked_at)
                .where(ingest.QualityResult.rule_id == rule.id)
                .order_by(ingest.QualityResult.checked_at.desc())
                .limit(1)
            )
            if last is None or last < due_before:
                due.append(rule.id)
    for rule_id in due:
        # Each rule runs with its own author's rights (quality.evaluate).
        await quality.evaluate(org_id, rule_id, run_kind="scheduled", run_ref=None)
    return len(due)


async def tick(at: datetime.datetime | None = None) -> dict[str, int]:
    """One pass over every organisation for the minute of `at` (now by default)."""
    minute = (at or _now()).replace(second=0, microsecond=0)
    counts = {"jobs": 0, "pipelines": 0, "quality": 0}
    for org_id in await organisations():
        try:
            counts["jobs"] += await _jobs(org_id, minute)
            counts["pipelines"] += await _pipelines(org_id, minute)
            counts["quality"] += await _quality(org_id)
        except Exception:  # one organisation's failure must not stop the others' schedules
            logger.exception("worker-agent | schedule pass failed for organisation %s", org_id)
    if any(counts.values()):
        logger.info("worker-agent | schedule %s: %s", minute.isoformat(), counts)
    return counts
