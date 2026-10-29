"""What is scheduled, and what one firing of it does.

`wanted` lists, per organisation, the schedules the data says should exist; clock.py keeps one
Temporal Schedule per item. `fire` is what a Schedule runs when it comes due:
- an active ingestion job: a job_run and the job_run.backfill_requested event worker-ingest loads
  from (the same path as "Chạy lại ngay");
- an active pipeline: a scheduled pipeline_run, skipped as skipped_overlap while a run of it is
  still going and no_overlap is set (UC-06-03), and not run at all on a day its work calendar does
  not work, the days the schedule preview leaves out;
- an active report schedule: a report_run dated by its as_of_rule and the report_run.requested
  event (report_runs.py);
- an organisation's quality pass, hourly: every active rule not checked in the last 24 hours;
- the nightly eval run of every active context eval set (nightly_evals.py), once for all
  organisations.

A firing re-reads its row first. A schedule paused, deleted or given another cron since clock.py
last synced fires into nothing, and a minute already served is recognised by the run it created,
so a retried activity does not fire twice.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from libs import cron
from libs.dataagent_db import scoped_session, unscoped_session, uuid7
from libs.dataagent_db.models import ingest, monitor, org
from libs.dataagent_db.models import pipeline as pl
from services.worker_agent import nightly_evals, quality, report_runs

logger = logging.getLogger(__name__)

QUALITY_EVERY = datetime.timedelta(hours=24)
QUALITY_CRON = "0 * * * *"
NIGHTLY_EVALS_CRON = "0 2 * * *"
UTC = "UTC"


@dataclass(frozen=True)
class Target:
    kind: str
    cron: str
    zone: str
    org_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None

    @property
    def schedule_id(self) -> str:
        return ":".join(str(p) for p in (self.kind, self.org_id, self.entity_id) if p is not None)

    @property
    def fingerprint(self) -> str:
        """Changes when the Schedule must change; kept in its memo."""
        return hashlib.sha256(f"{self.cron}|{self.zone}".encode()).hexdigest()[:16]


KINDS = frozenset({"job", "pipeline", "report", "quality", "nightly-evals"})


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _valid(expr: str | None) -> str | None:
    if not expr:
        return None
    try:
        return cron.check(expr)
    except cron.CronError:
        return None


async def organisations() -> list[uuid.UUID]:
    """Row-level security scopes every session to one organisation and no role may list them all,
    so the organisations are those the outbox relay has seen emit on ingest.outbox, pipeline.outbox
    or monitor.outbox (every job, pipeline and report schedule was created through one)."""
    async with unscoped_session("svc_outbox_relay") as session:
        rows = await session.execute(
            text(
                "SELECT org_id FROM ingest.outbox UNION SELECT org_id FROM pipeline.outbox"
                " UNION SELECT org_id FROM monitor.outbox"
            )
        )
        return [r[0] for r in rows]


async def wanted(org_id: uuid.UUID) -> list[Target]:
    """The schedules this organisation's data asks for; a row with an unreadable cron has none."""
    out: list[Target] = []
    async with scoped_session("svc_ingest", org_id) as session:
        for job_id, expr in await session.execute(
            select(ingest.IngestionJob.id, ingest.IngestionJob.schedule_cron).where(
                ingest.IngestionJob.status == "active",
                ingest.IngestionJob.deleted_at.is_(None),
                ingest.IngestionJob.schedule_cron.is_not(None),
            )
        ):
            if (checked := _valid(expr)) is not None:
                out.append(Target("job", checked, UTC, org_id, job_id))
        has_rules = await session.scalar(
            select(ingest.QualityRule.id).where(ingest.QualityRule.status == "active").limit(1)
        )
        if has_rules is not None:
            out.append(Target("quality", QUALITY_CRON, UTC, org_id))
    async with scoped_session("svc_pipeline", org_id) as session:
        for pipeline_id, expr, calendar_id in await session.execute(
            select(pl.Pipeline.id, pl.Pipeline.cron, pl.Pipeline.work_calendar_id).where(
                pl.Pipeline.status == "active",
                pl.Pipeline.deleted_at.is_(None),
                pl.Pipeline.cron.is_not(None),
            )
        ):
            if (checked := _valid(expr)) is not None:
                zone = await _calendar_zone(session, calendar_id)
                out.append(Target("pipeline", checked, zone, org_id, pipeline_id))
    async with scoped_session(report_runs.ROLE, org_id) as session:
        for schedule_id, expr in await session.execute(
            select(monitor.ReportSchedule.id, monitor.ReportSchedule.cron).where(
                monitor.ReportSchedule.status == "active",
                monitor.ReportSchedule.deleted_at.is_(None),
            )
        ):
            if (checked := _valid(expr)) is not None:
                out.append(Target("report", checked, report_runs.ZONE.key, org_id, schedule_id))
    return out


def global_targets() -> list[Target]:
    """Schedules of no one organisation: nightly_evals finds the eval sets itself."""
    return [Target("nightly-evals", NIGHTLY_EVALS_CRON, report_runs.ZONE.key)]


async def _calendar_zone(session: AsyncSession, calendar_id: uuid.UUID | None) -> str:
    """The zone a pipeline's cron is read in: its work calendar's, as the schedule preview reads it,
    else UTC."""
    if calendar_id is None:
        return UTC
    zone = await session.scalar(
        select(org.WorkCalendar.time_zone).where(org.WorkCalendar.id == calendar_id)
    )
    return zone or UTC


async def _working(session: AsyncSession, calendar_id: uuid.UUID, day: datetime.date) -> bool:
    calendar = await session.scalar(
        select(org.WorkCalendar).where(org.WorkCalendar.id == calendar_id)
    )
    if calendar is None:
        return True
    kind = await session.scalar(
        select(org.WorkCalendarDay.kind).where(
            org.WorkCalendarDay.calendar_id == calendar_id, org.WorkCalendarDay.day == day
        )
    )
    if kind == "holiday":
        return False
    if kind == "working_override":
        return True
    return day.isoweekday() not in (calendar.weekend_days or [])


async def fire(
    kind: str, org_id: uuid.UUID | None, entity_id: uuid.UUID | None, at: datetime.datetime
) -> str:
    """One firing of a schedule for the minute of `at`; returns what it did, for the workflow's
    result in the Temporal UI."""
    minute = at.astimezone(datetime.UTC).replace(second=0, microsecond=0)
    if kind == "nightly-evals":
        requested = await nightly_evals.request_all(minute)
        return f"requested {len(requested)}"
    if org_id is None:
        raise ValueError(f"a {kind} schedule belongs to an organisation")
    if kind == "quality":
        return f"checked {await _quality(org_id)}"
    if entity_id is None:
        raise ValueError(f"a {kind} schedule names its row")
    if kind == "job":
        return await _job(org_id, entity_id, minute)
    if kind == "pipeline":
        return await _pipeline(org_id, entity_id, minute)
    if kind == "report":
        return await _report(org_id, entity_id, minute)
    raise ValueError(f"unknown schedule kind {kind}")


def _due(expr: str | None, local: datetime.datetime) -> bool:
    """Whether the row's current cron fires at this local minute: a Schedule not yet synced to a
    changed cron fires into nothing instead of at the old time."""
    try:
        return expr is not None and cron.matches(expr, local)
    except ValueError:
        return False


async def _job(org_id: uuid.UUID, job_id: uuid.UUID, minute: datetime.datetime) -> str:
    async with scoped_session("svc_ingest", org_id) as session:
        job = await session.scalar(
            select(ingest.IngestionJob).where(
                ingest.IngestionJob.id == job_id,
                ingest.IngestionJob.status == "active",
                ingest.IngestionJob.deleted_at.is_(None),
            )
        )
        if job is None or not _due(job.schedule_cron, minute):
            return "not due"
        served = await session.scalar(
            select(ingest.JobRun.id).where(
                ingest.JobRun.job_id == job.id, ingest.JobRun.started_at >= minute
            )
        )
        actor = job.updated_by or job.created_by
        if served is not None or actor is None:
            return "already served" if served is not None else "no actor"
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
        return f"job_run {run.id}"


async def _pipeline(org_id: uuid.UUID, pipeline_id: uuid.UUID, minute: datetime.datetime) -> str:
    async with scoped_session("svc_pipeline", org_id) as session:
        row = await session.scalar(
            select(pl.Pipeline).where(
                pl.Pipeline.id == pipeline_id,
                pl.Pipeline.status == "active",
                pl.Pipeline.deleted_at.is_(None),
            )
        )
        if row is None:
            return "not due"
        zone = ZoneInfo(await _calendar_zone(session, row.work_calendar_id))
        local = minute.astimezone(zone).replace(tzinfo=None)
        if not _due(row.cron, local):
            return "not due"
        if row.work_calendar_id is not None and not await _working(
            session, row.work_calendar_id, local.date()
        ):
            return "not a working day"
        served = await session.scalar(
            select(pl.PipelineRun.id).where(
                pl.PipelineRun.pipeline_id == row.id,
                pl.PipelineRun.mode == "scheduled",
                pl.PipelineRun.started_at >= minute,
            )
        )
        if served is not None:
            return "already served"
        running = await session.scalar(
            select(pl.PipelineRun.id).where(
                pl.PipelineRun.pipeline_id == row.id,
                pl.PipelineRun.mode != "preview",
                pl.PipelineRun.status == "running",
            )
        )
        skipped = running is not None and row.no_overlap
        run = pl.PipelineRun(
            id=uuid7(),
            org_id=org_id,
            pipeline_id=row.id,
            mode="scheduled",
            status="skipped_overlap" if skipped else "running",
            finished_at=_now() if skipped else None,
        )
        session.add(run)
        if not skipped:
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
        return f"pipeline_run {run.id} {run.status}"


async def _report(org_id: uuid.UUID, schedule_id: uuid.UUID, minute: datetime.datetime) -> str:
    local = minute.astimezone(report_runs.ZONE)
    async with scoped_session(report_runs.ROLE, org_id) as session:
        row = await session.scalar(
            select(monitor.ReportSchedule).where(
                monitor.ReportSchedule.id == schedule_id,
                monitor.ReportSchedule.status == "active",
                monitor.ReportSchedule.deleted_at.is_(None),
            )
        )
        if row is None or not _due(row.cron, local.replace(tzinfo=None)):
            return "not due"
        served = await session.scalar(
            select(monitor.ReportRun.id).where(
                monitor.ReportRun.report_schedule_id == row.id,
                monitor.ReportRun.trigger == "schedule",
                monitor.ReportRun.created_at >= minute,
            )
        )
        if served is not None:
            return "already served"
        as_of = report_runs.as_of_for(row.as_of_rule, local.date())
        run = monitor.ReportRun(
            id=uuid7(),
            org_id=org_id,
            report_schedule_id=row.id,
            kind=row.kind,
            as_of=as_of or local.date(),
            trigger="schedule",
            # A rule this worker cannot read fails the run instead of dating it wrongly.
            status="running" if as_of is not None else "failed",
            checks={}
            if as_of is not None
            else {"error": f"Không hiểu quy tắc ngày {row.as_of_rule}"},
            finished_at=None if as_of is not None else _now(),
        )
        session.add(run)
        if as_of is not None:
            session.add(
                monitor.Outbox(
                    id=uuid7(),
                    org_id=org_id,
                    aggregate_type="report_run",
                    aggregate_id=run.id,
                    event_type=report_runs.EVENT_TYPE,
                    payload={
                        "report_run_id": str(run.id),
                        "report_schedule_id": str(row.id),
                        "data_space_id": str(row.data_space_id),
                        "kind": row.kind,
                        "as_of": as_of.isoformat(),
                        "trigger": "schedule",
                        "status": "running",
                        "requested_by": str(row.owner_id),
                    },
                )
            )
        return f"report_run {run.id} {run.status}"


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
