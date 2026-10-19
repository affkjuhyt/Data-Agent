"""A2 group Nạp dữ liệu: ingestion jobs, DLQ, file uploads, data quality rules (schema ingest).

UC-04-06 to UC-04-09 and UC-06-07. The api only records intent: running a job, a backfill, a
replay, analysing or loading an uploaded file and evaluating a quality rule are worker-ingest's
work, started by the event this module writes to ingest.outbox in the same transaction as the
row (D1 rule 8). Sources and datasets belong to catalog; they are read here through the
catalog_read grant (definitions.json cross_reads) and never written.
"""

from __future__ import annotations

import csv
import datetime
import decimal
import io
import os
import re
import uuid
import warnings
from collections.abc import Callable, Sequence
from typing import Annotated, Any, TypeVar
from zipfile import BadZipFile

from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile
from fastapi._compat import get_cached_model_fields
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import catalog, ingest
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentDataSpace, CurrentPrincipal, Principal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Order,
    Page,
    PageRequest,
    encode_page_token,
    invalid_page_token,
    keyset,
    next_page,
    order_by,
    page_request,
)
from services.api.kernel.problems import Problem
from services.api.modules.ingest.schemas import (
    Accepted,
    Acknowledge,
    Backfill,
    DatasetView,
    DlqRecordView,
    FileUploadView,
    IngestionJobDryRun,
    IngestionJobSummary,
    IngestionJobView,
    JobCreate,
    JobPatch,
    JobRunView,
    JobStatus,
    QualityResultView,
    QualityRuleCreate,
    QualityRulePatch,
    QualityRuleView,
    Reason,
    Severity,
    UploadPatch,
    UploadPreview,
)
from services.api.modules.ingest.storage import BUCKET, ObjectStore, object_store

router = APIRouter()
ROLE = "svc_ingest"
IdPath = Annotated[uuid.UUID, Path(alias="id")]
POLL_AFTER_MS = 2_000

# UC-04-08 precondition and file_upload CHECK: at most 200 MB.
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
# UC-04-08 E1: macros and external links are dropped by the reader, so .xlsm is accepted.
UPLOAD_EXTENSIONS = {".csv": "text/csv", ".xlsx": "", ".xlsm": "", ".xls": ""}
# BR-104: discarding more than 1% of a run's records needs the Data Space owner's approval.
DISCARD_APPROVAL_RATIO = 0.01
# BR-105: a manual table expires after 90 days.
UPLOAD_TABLE_DAYS = 90

PAUSABLE = ("active", "failing", "schema_drift")
# A schema_drift job resumes only once the drift is resolved (A3: 409 invalid_state).
RESUMABLE = ("paused", "failing")
EDITABLE_UPLOAD = ("uploaded", "analyzed", "mapped")
# The worker must have analysed the file (error_ratio set) before it can be written.
COMMITTABLE_UPLOAD = ("analyzed", "mapped")

R = TypeVar("R")

# FastAPI 0.115 rebuilds each field of a single JSON body model as Annotated[type, FieldInfo], and
# pydantic 2.14 warns there that `alias` has no effect (UnsupportedFieldAttributeWarning), although
# the model itself still reads "from". FastAPI caches that per model: build it once here, silenced,
# so the first :backfill request does not raise it (an error under the suite's -W error). Drop this
# when FastAPI stops rebuilding JSON body fields.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    get_cached_model_fields(Backfill)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _event(
    principal: Principal,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    event_type: str,
    **payload: Any,
) -> ingest.Outbox:
    def plain(value: Any) -> Any:
        if isinstance(value, uuid.UUID | datetime.date | datetime.datetime):
            return str(value) if isinstance(value, uuid.UUID) else value.isoformat()
        return value

    return ingest.Outbox(
        id=uuid7(),
        org_id=principal.org_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        event_type=event_type,
        payload={k: plain(v) for k, v in payload.items()},
    )


def _after(page: PageRequest) -> tuple[datetime.datetime, uuid.UUID] | None:
    if not page.cursor:
        return None
    try:
        return datetime.datetime.fromisoformat(page.cursor["t"]), uuid.UUID(page.cursor["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise invalid_page_token() from exc


def _cut(
    rows: Sequence[R], page: PageRequest, key: Callable[[R], tuple[datetime.datetime, uuid.UUID]]
) -> tuple[list[R], str]:
    more = len(rows) > page.page_size
    kept = list(rows[: page.page_size])
    if not more:
        return kept, ""
    at, row_id = key(kept[-1])
    return kept, encode_page_token({"t": at.isoformat(), "id": str(row_id)})


# --- jobs ---------------------------------------------------------------------------------------


async def _job(
    session: AsyncSession,
    job_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    lock: bool = False,
) -> ingest.IngestionJob:
    job = ingest.IngestionJob
    query = select(job).where(
        job.id == job_id,
        job.org_id == principal.org_id,
        job.data_space_id == data_space,
        job.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update()
    row = await session.scalar(query)
    if row is None:
        raise Problem(404, "not_found", "Ingestion job not found.")
    return row


async def _dataset(
    session: AsyncSession, dataset_id: uuid.UUID, principal: Principal, data_space: uuid.UUID
) -> catalog.Dataset | None:
    ds = catalog.Dataset
    found: catalog.Dataset | None = await session.scalar(
        select(ds).where(
            ds.id == dataset_id,
            ds.org_id == principal.org_id,
            ds.data_space_id == data_space,
            ds.deleted_at.is_(None),
        )
    )
    return found


def _job_view(row: ingest.IngestionJob) -> IngestionJobView:
    return IngestionJobView.model_validate(row, from_attributes=True)


DRY_RUN_NOTE = (
    "Configuration checked and nothing recorded. The 1,000-row trial on the source is run by "
    "worker-ingest, which holds the source connection; the api cannot read the source, so no "
    "sample or row errors are returned here."
)


@router.post(
    "/v1/sources/{id}/jobs",
    response_model=IngestionJobView | IngestionJobDryRun,
    status_code=201,
    **endpoint("POST", "/v1/sources/{id}/jobs", operation_id="createIngestionJob"),
)
async def create_job(
    body: JobCreate,
    source_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
    dry_run: Annotated[
        bool, Query(description="Check the job as create would, record nothing (200)")
    ] = False,
) -> IngestionJobView | IngestionJobDryRun:
    async with transaction(ROLE, principal) as session:
        source = await session.scalar(
            select(catalog.Source).where(
                catalog.Source.id == source_id,
                catalog.Source.org_id == principal.org_id,
                catalog.Source.data_space_id == data_space,
                catalog.Source.deleted_at.is_(None),
            )
        )
        if source is None:
            raise Problem(404, "not_found", "Source not found.")
        if source.mode != "ingest":
            # UC-04-06 precondition: only a source in ingest mode is loaded; federated ones are
            # queried in place (invariant 15).
            raise Problem(
                422, "business_rule_violated", "The source is queried in place, not ingested."
            )
        if body.target_dataset_id is not None and not await _dataset(
            session, body.target_dataset_id, principal, data_space
        ):
            raise Problem(422, "validation_failed", "target_dataset_id is not a dataset here.")
        row = ingest.IngestionJob(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            source_id=source.id,
            name=body.name,
            mode=body.mode,
            source_object=body.source_object,
            target_dataset_id=body.target_dataset_id,
            cursor_column=body.cursor_column,
            schedule_cron=body.schedule_cron,
            # BR-56: the job inherits the source's freshness deadline unless it sets its own.
            freshness_sla=body.freshness_sla or source.freshness_sla,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        if body.dlq_retention_days is not None:
            row.dlq_retention_days = body.dlq_retention_days
        if dry_run:
            # The same insert as a create, so the table's own checks and defaults apply too, in
            # a savepoint that is rolled back: no row, no event (A3 ?dry_run).
            savepoint = await session.begin_nested()
            session.add(row)
            await session.flush()
            await session.refresh(row)
            would_be = _job_view(row)
            await savepoint.rollback()
            response.status_code = 200
            return IngestionJobDryRun(**would_be.model_dump(), note=DRY_RUN_NOTE)
        session.add(row)
        await session.flush()
        await session.refresh(row)
        session.add(
            _event(
                principal,
                "ingestion_job",
                row.id,
                "ingestion_job.created",
                job_id=row.id,
                source_id=row.source_id,
                data_space_id=data_space,
                principal_id=principal.user_id,
                mode=row.mode,
                status=row.status,
            )
        )
        return _job_view(row)


@router.post(
    "/v1/uploads/{id}:commit",
    response_model=DatasetView,
    **endpoint("POST", "/v1/uploads/{id}:commit", operation_id="commitUpload"),
)
async def commit_upload(
    upload_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    body: UploadPatch | None = None,
) -> DatasetView:
    return await _commit_upload_impl(upload_id, principal, data_space, body)


@router.get(
    "/v1/jobs",
    response_model=Page[IngestionJobSummary],
    **endpoint("GET", "/v1/jobs", operation_id="listIngestionJobs"),
)
async def list_jobs(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by(
                "created_at", "updated_at", "name", "status", "mode", default="created_at desc"
            )
        ),
    ],
    status: JobStatus | None = None,
    source_id: uuid.UUID | None = None,
) -> Page[IngestionJobSummary]:
    job = ingest.IngestionJob
    query = select(job).where(
        job.org_id == principal.org_id, job.data_space_id == data_space, job.deleted_at.is_(None)
    )
    if status:
        query = query.where(job.status == status)
    if source_id:
        query = query.where(job.source_id == source_id)
    query = keyset(
        query,
        columns={
            "created_at": job.created_at,
            "updated_at": job.updated_at,
            "name": job.name,
            "status": job.status,
            "mode": job.mode,
        },
        tiebreak=job.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        jobs, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
        ids = [j.id for j in jobs]
        runs = {
            r.job_id: r
            for r in (
                await session.scalars(
                    select(ingest.JobRun)
                    .where(ingest.JobRun.job_id.in_(ids))
                    .distinct(ingest.JobRun.job_id)
                    .order_by(ingest.JobRun.job_id, ingest.JobRun.started_at.desc())
                )
            ).all()
        }
        pending = dict(
            (
                await session.execute(
                    select(ingest.DlqRecord.job_id, func.count())
                    .where(ingest.DlqRecord.job_id.in_(ids), ingest.DlqRecord.status == "pending")
                    .group_by(ingest.DlqRecord.job_id)
                )
            )
            .tuples()
            .all()
        )
    items = []
    for j in jobs:
        run = runs.get(j.id)
        items.append(
            IngestionJobSummary(
                **_job_view(j).model_dump(),
                last_run=JobRunView.model_validate(run, from_attributes=True) if run else None,
                lag_seconds=run.lag_seconds if run else None,
                dlq_pending=pending.get(j.id, 0),
            )
        )
    return Page(items=items, next_page_token=token)


@router.patch(
    "/v1/jobs/{id}",
    response_model=IngestionJobView,
    **endpoint("PATCH", "/v1/jobs/{id}", operation_id="updateIngestionJob"),
)
async def update_job(
    body: JobPatch,
    job_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> IngestionJobView:
    changes = body.model_dump(exclude_unset=True)
    if changes.get("name", "") is None or changes.get("dlq_retention_days", 0) is None:
        raise Problem(422, "validation_failed", "name and dlq_retention_days cannot be cleared.")
    async with transaction(ROLE, principal) as session:
        row = await _job(session, job_id, principal, data_space, lock=True)
        for field, value in changes.items():
            setattr(row, field, value)
        row.updated_by, row.updated_at = principal.user_id, _now()
        # The scheduler re-reads the schedule and deadline from this event.
        session.add(
            _event(
                principal,
                "ingestion_job",
                row.id,
                "ingestion_job.updated",
                job_id=row.id,
                data_space_id=data_space,
                fields=sorted(changes),
                status=row.status,
            )
        )
        await session.flush()
        return _job_view(row)


async def _set_job_status(
    job_id: uuid.UUID,
    principal: Principal,
    data_space: uuid.UUID,
    *,
    allowed: tuple[str, ...],
    status: str,
    event_type: str,
    reason: str | None = None,
) -> IngestionJobView:
    async with transaction(ROLE, principal) as session:
        row = await _job(session, job_id, principal, data_space, lock=True)
        if row.status not in allowed:
            raise Problem(409, "invalid_state", f"The job is {row.status}.")
        now = _now()
        row.status = status
        row.paused_reason = reason
        row.paused_at = now if status == "paused" else None
        if status == "active":
            row.consecutive_failures = 0
        row.updated_by, row.updated_at = principal.user_id, now
        session.add(
            _event(
                principal,
                "ingestion_job",
                row.id,
                event_type,
                job_id=row.id,
                source_id=row.source_id,
                data_space_id=data_space,
                status=status,
            )
        )
        await session.flush()
        return _job_view(row)


@router.post(
    "/v1/jobs/{id}:pause",
    response_model=IngestionJobView,
    **endpoint("POST", "/v1/jobs/{id}:pause", operation_id="pauseIngestionJob"),
)
async def pause_job(
    body: Reason, job_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> IngestionJobView:
    # BR-106: the reason is required (Reason refuses a blank one; the CHECK backs it up).
    return await _set_job_status(
        job_id,
        principal,
        data_space,
        allowed=PAUSABLE,
        status="paused",
        event_type="ingestion_job.paused",
        reason=body.reason,
    )


@router.post(
    "/v1/jobs/{id}:resume",
    response_model=IngestionJobView,
    **endpoint("POST", "/v1/jobs/{id}:resume", operation_id="resumeIngestionJob"),
)
async def resume_job(
    job_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> IngestionJobView:
    # UC-04-07 step 5: the cursor carries on from where it stopped; cursor_value is untouched.
    return await _set_job_status(
        job_id,
        principal,
        data_space,
        allowed=RESUMABLE,
        status="active",
        event_type="ingestion_job.resumed",
    )


@router.post(
    "/v1/jobs/{id}:backfill",
    response_model=Accepted,
    status_code=202,
    **endpoint("POST", "/v1/jobs/{id}:backfill", operation_id="backfillIngestionJob"),
)
async def backfill_job(
    body: Backfill,
    job_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> Accepted:
    if body.to > _now().date():
        raise Problem(
            422, "business_rule_violated", "A backfill cannot reach past today at the source."
        )
    async with transaction(ROLE, principal) as session:
        job = await _job(session, job_id, principal, data_space, lock=True)
        if job.status == "draft":
            raise Problem(409, "invalid_state", "The job is a draft.")
        # UC-04-06 6a: a separate run over [from, to]; the job's own cursor is not moved.
        run = ingest.JobRun(
            id=uuid7(),
            org_id=principal.org_id,
            job_id=job.id,
            cursor_from=body.from_.isoformat(),
            cursor_to=body.to.isoformat(),
        )
        session.add(run)
        await session.flush()
        await session.refresh(run)
        session.add(
            _event(
                principal,
                "job_run",
                run.id,
                "job_run.backfill_requested",
                job_run_id=run.id,
                job_id=job.id,
                data_space_id=data_space,
                principal_id=principal.user_id,
                cursor_from=run.cursor_from,
                cursor_to=run.cursor_to,
                status=run.status,
            )
        )
    response.headers["Location"] = f"/v1/job-runs?job_id={job.id}"
    return Accepted(id=run.id, status=run.status, poll_after_ms=POLL_AFTER_MS)


@router.get(
    "/v1/job-runs",
    response_model=Page[JobRunView],
    **endpoint("GET", "/v1/job-runs", operation_id="listJobRuns"),
)
async def list_job_runs(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order, Depends(order_by("started_at", "created_at", "status", default="started_at desc"))
    ],
    job_id: uuid.UUID | None = None,
    status: str | None = None,
    from_: Annotated[datetime.datetime | None, Query(alias="from")] = None,
    to: datetime.datetime | None = None,
) -> Page[JobRunView]:
    run, job = ingest.JobRun, ingest.IngestionJob
    query = (
        select(run)
        .join(job, job.id == run.job_id)
        .where(run.org_id == principal.org_id, job.data_space_id == data_space)
    )
    if job_id:
        query = query.where(run.job_id == job_id)
    if status:
        query = query.where(run.status == status)
    if from_:
        query = query.where(run.started_at >= from_)
    if to:
        query = query.where(run.started_at < to)
    query = keyset(
        query,
        columns={"started_at": run.started_at, "created_at": run.created_at, "status": run.status},
        tiebreak=run.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(
        items=[JobRunView.model_validate(r, from_attributes=True) for r in rows],
        next_page_token=token,
    )


# --- DLQ ----------------------------------------------------------------------------------------


@router.get(
    "/v1/jobs/{id}/dlq",
    response_model=Page[DlqRecordView],
    **endpoint("GET", "/v1/jobs/{id}/dlq", operation_id="listJobDlq"),
)
async def list_dlq(
    job_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by("created_at", "expires_at", "status", "error_kind", default="created_at desc")
        ),
    ],
    status: str | None = None,
    error_kind: str | None = None,
) -> Page[DlqRecordView]:
    dlq = ingest.DlqRecord
    query = select(dlq).where(dlq.job_id == job_id, dlq.org_id == principal.org_id)
    if status:
        query = query.where(dlq.status == status)
    if error_kind:
        query = query.where(dlq.error_kind == error_kind)
    query = keyset(
        query,
        columns={
            "created_at": dlq.created_at,
            "expires_at": dlq.expires_at,
            "status": dlq.status,
            "error_kind": dlq.error_kind,
        },
        tiebreak=dlq.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        await _job(session, job_id, principal, data_space)
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(
        items=[DlqRecordView.model_validate(r, from_attributes=True) for r in rows],
        next_page_token=token,
    )


async def _dlq_record(
    session: AsyncSession, record_id: uuid.UUID, principal: Principal, data_space: uuid.UUID
) -> tuple[ingest.DlqRecord, ingest.IngestionJob]:
    dlq, job = ingest.DlqRecord, ingest.IngestionJob
    found = (
        await session.execute(
            select(dlq, job)
            .join(job, job.id == dlq.job_id)
            .where(
                dlq.id == record_id,
                dlq.org_id == principal.org_id,
                job.data_space_id == data_space,
                job.deleted_at.is_(None),
            )
            .with_for_update(of=dlq)
        )
    ).first()
    if found is None:
        raise Problem(404, "not_found", "DLQ record not found.")
    return found[0], found[1]


@router.post(
    "/v1/dlq/{id}:replay",
    response_model=Accepted,
    status_code=202,
    **endpoint("POST", "/v1/dlq/{id}:replay", operation_id="replayDlqRecord"),
)
async def replay_dlq(
    record_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    response: Response,
) -> Accepted:
    async with transaction(ROLE, principal) as session:
        record, job = await _dlq_record(session, record_id, principal, data_space)
        if record.status == "locked":
            # UC-04-07 E1: locked after three failed replays; A3 answers 409.
            raise Problem(409, "invalid_state", "The record is locked after three failed replays.")
        if record.status != "pending":
            raise Problem(409, "invalid_state", f"The record is already {record.status}.")
        # The worker replays with the job's current mapping, then marks the record replayed or
        # counts the failure (and locks it on the fourth).
        session.add(
            _event(
                principal,
                "dlq_record",
                record.id,
                "dlq_record.replay_requested",
                dlq_record_id=record.id,
                job_id=job.id,
                job_run_id=record.job_run_id,
                data_space_id=data_space,
                replay_count=record.replay_count,
                status=record.status,
            )
        )
    response.headers["Location"] = f"/v1/jobs/{job.id}/dlq"
    return Accepted(id=record.id, status=record.status, poll_after_ms=POLL_AFTER_MS)


@router.post(
    "/v1/dlq/{id}:discard",
    response_model=DlqRecordView,
    **endpoint("POST", "/v1/dlq/{id}:discard", operation_id="discardDlqRecord"),
)
async def discard_dlq(
    body: Reason, record_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> DlqRecordView:
    async with transaction(ROLE, principal) as session:
        record, job = await _dlq_record(session, record_id, principal, data_space)
        if record.status not in ("pending", "locked"):
            raise Problem(409, "invalid_state", f"The record is already {record.status}.")
        run = await session.scalar(
            select(ingest.JobRun).where(ingest.JobRun.id == record.job_run_id).with_for_update()
        )
        discarded = await session.scalar(
            select(func.count())
            .select_from(ingest.DlqRecord)
            .where(
                ingest.DlqRecord.job_run_id == record.job_run_id,
                ingest.DlqRecord.status == "discarded",
            )
        )
        rows_read = run.rows_read if run is not None else 0
        over = (discarded or 0) + 1 > DISCARD_APPROVAL_RATIO * rows_read
        if over and "approve" not in principal.capabilities:
            # BR-104: past 1% of the run the Data Space owner (approve) decides, not the engineer.
            raise Problem(
                422,
                "business_rule_violated",
                "Discarding more than 1% of a run's records needs the Data Space owner's approval (BR-104).",
            )
        record.status, record.discard_reason = "discarded", body.reason
        session.add(
            _event(
                principal,
                "dlq_record",
                record.id,
                "dlq_record.discarded",
                dlq_record_id=record.id,
                job_id=job.id,
                job_run_id=record.job_run_id,
                data_space_id=data_space,
                status=record.status,
                discarded_by=principal.user_id,
            )
        )
        await session.flush()
        return DlqRecordView.model_validate(record, from_attributes=True)


# --- uploads ------------------------------------------------------------------------------------


async def _measure(upload: UploadFile) -> int:
    size = 0
    while chunk := await upload.read(1 << 20):
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise Problem(413, "payload_too_large", "The file is larger than 200 MB.")
    await upload.seek(0)
    return size


# CSV cells arrive as text, so the first read recognises numbers, percents, dates and booleans
# in their written form too. Either separator may group or mark decimals ("1.234,5" and
# "1,234.5"); a code with a leading zero ("001") stays text.
_PLAIN_NUMBER = re.compile(r"[+-]?(?:0|[1-9]\d*)(?:[.,]\d+)?")
_GROUPED_NUMBER = re.compile(r"[+-]?[1-9]\d{0,2}(?:([.,])\d{3})(?:\1\d{3})*(?:[.,]\d+)?")
_TEXT_DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}(?:[ T].*)?|\d{1,2}/\d{1,2}/\d{4}")
_TEXT_BOOLEAN = {"true", "false"}


def _is_number_text(text: str) -> bool:
    return bool(_PLAIN_NUMBER.fullmatch(text) or _GROUPED_NUMBER.fullmatch(text))


def _cell_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float | decimal.Decimal):
        return "number"
    if isinstance(value, datetime.date | datetime.datetime):
        return "date"
    text = str(value).strip()
    if text.lower() in _TEXT_BOOLEAN:
        return "boolean"
    if _is_number_text(text):
        return "number"
    if text.endswith("%") and _is_number_text(text[:-1].strip()):
        return "percent"
    if _TEXT_DATE.fullmatch(text):
        return "date"
    return "string"


def _column_type(values: list[Any]) -> str:
    kinds = {_cell_type(value) for value in values if value not in (None, "")}
    return kinds.pop() if len(kinds) == 1 else "string"


def _is_personal_column(name: str) -> bool:
    tokens = set(name.lower().replace("-", "_").split("_"))
    return bool(tokens & {"name", "email", "phone", "mobile", "cccd", "passport", "address"})


def _masked_example(value: Any, personal: bool) -> str:
    text = str(value)
    if not personal or not text:
        return text
    return f"{text[:8]}… (đã che)"


def _analyse_upload(content: bytes, file_name: str, requested_sheet: str | None) -> dict[str, Any]:
    """Derive the deterministic first read shown by P04 before the worker enriches it."""
    extension = os.path.splitext(file_name)[1].lower()
    sheet_name: str | None = requested_sheet
    if extension in {".xlsx", ".xlsm"}:
        from openpyxl import load_workbook

        try:
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except BadZipFile:
            # Macro-enabled uploads are retained before worker analysis. A worker can inspect
            # them with the configured reader; the API must not turn a raw upload into a 500 just
            # because the lightweight preview reader cannot open its container yet.
            if extension == ".xlsm":
                return {
                    "sheet": requested_sheet,
                    "header_row": None,
                    "encoding": None,
                    "column_map": [],
                    "validation": {},
                }
            raise Problem(422, "validation_failed", "The Excel file is not readable.") from None
        sheet_name = requested_sheet or workbook.sheetnames[0]
        if sheet_name not in workbook.sheetnames:
            raise Problem(422, "validation_failed", "The selected sheet does not exist.")
        worksheet = workbook[sheet_name]
        row_count = max(0, (worksheet.max_row or 0) - 1)
        rows = [list(values) for values in worksheet.iter_rows(max_row=1001, values_only=True)]
        workbook.close()
        encoding: str | None = None
    elif extension == ".xls":
        try:
            import xlrd
        except ImportError as exc:
            raise Problem(
                503, "source_unavailable", "Legacy Excel analysis is not configured."
            ) from exc
        workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
        sheet_name = requested_sheet or workbook.sheet_names()[0]
        if sheet_name not in workbook.sheet_names():
            raise Problem(422, "validation_failed", "The selected sheet does not exist.")
        worksheet = workbook.sheet_by_name(sheet_name)
        row_count = max(0, worksheet.nrows - 1)
        rows = [worksheet.row_values(index) for index in range(min(worksheet.nrows, 1001))]
        workbook.release_resources()
        encoding = None
    elif extension == ".csv":
        encoding = "utf-8-sig"
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError as exc:
            raise Problem(422, "validation_failed", "The file encoding is not readable.") from exc
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [list(values) for values in csv.reader(io.StringIO(text), dialect)]
        row_count = max(0, len(rows) - 1)
    else:
        raise Problem(422, "validation_failed", "Only CSV and Excel analysis is supported.")
    if not rows:
        raise Problem(422, "validation_failed", "The file has no readable rows.")
    header, data = rows[0], rows[1:1001]
    columns = []
    for index, value in enumerate(header):
        name = str(value or "").strip() or f"column_{index + 1}"
        values = [row[index] if index < len(row) else None for row in data]
        example = next((item for item in values if item not in (None, "")), "")
        personal = _is_personal_column(name)
        columns.append(
            {
                "header": name,
                "name": name.lower().replace(" ", "_").replace(".", "_").replace("-", "_"),
                "type": _column_type(values),
                "unit": None,
                "example": _masked_example(example, personal),
                "personal": personal,
                "issue": None,
            }
        )
    return {
        "sheet": sheet_name,
        "header_row": 1,
        "encoding": encoding,
        "column_map": columns,
        "validation": {"row_count": row_count, "sample_rows": len(data)},
    }


@router.get(
    "/v1/uploads",
    response_model=Page[FileUploadView],
    **endpoint("GET", "/v1/uploads", operation_id="listUploads"),
)
async def list_uploads(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
) -> Page[FileUploadView]:
    """Recent uploads for the current Data Space, so the upload screen can recover its history."""
    upload = ingest.FileUpload
    query = (
        select(upload)
        .where(upload.org_id == principal.org_id, upload.data_space_id == data_space)
        .order_by(upload.created_at.desc(), upload.id.desc())
        .limit(page.page_size)
    )
    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    return Page(
        items=[FileUploadView.model_validate(row, from_attributes=True) for row in rows],
        next_page_token="",
    )


@router.post(
    "/v1/uploads",
    response_model=FileUploadView,
    status_code=201,
    **endpoint("POST", "/v1/uploads", operation_id="uploadFile"),
)
async def upload_file(
    file: Annotated[UploadFile, File()],
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    store: Annotated[ObjectStore, Depends(object_store)],
    sheet: Annotated[str | None, Form(max_length=200)] = None,
) -> FileUploadView:
    name = os.path.basename(file.filename or "").strip()
    ext = os.path.splitext(name)[1].lower()
    if not name or ext not in UPLOAD_EXTENSIONS:
        raise Problem(422, "validation_failed", "Only CSV and Excel files can be uploaded.")
    size = await _measure(file)
    if size == 0:
        raise Problem(422, "validation_failed", "The file is empty.")
    content = await file.read()
    await file.seek(0)
    analysis = _analyse_upload(content, name, sheet)
    upload_id = uuid7()
    key = f"{principal.org_id}/{data_space}/{upload_id}{ext}"
    await store.put(
        BUCKET,
        key,
        file.file,
        size,
        UPLOAD_EXTENSIONS[ext] or file.content_type or "application/octet-stream",
    )
    async with transaction(ROLE, principal) as session:
        row = ingest.FileUpload(
            id=upload_id,
            org_id=principal.org_id,
            data_space_id=data_space,
            uploader_id=principal.user_id,
            file_name=name,
            object_key=key,
            size_bytes=size,
            sheet=analysis["sheet"],
            header_row=analysis["header_row"],
            encoding=analysis["encoding"],
            column_map=analysis["column_map"],
            validation=analysis["validation"],
            # Upload only records the immutable original. The ingest worker owns analysis and
            # must advance the state after it has checked the bytes, schema and error ratio.
            error_ratio=None,
            status="uploaded",
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        # UC-04-08 steps 2-3: worker-ingest detects the structure and checks it against existing
        # data, filling validation and error_ratio.
        session.add(
            _event(
                principal,
                "file_upload",
                row.id,
                "file_upload.uploaded",
                file_upload_id=row.id,
                data_space_id=data_space,
                object_key=key,
                status=row.status,
            )
        )
        return FileUploadView.model_validate(row, from_attributes=True)


@router.get(
    "/v1/uploads/{id}",
    response_model=FileUploadView,
    **endpoint("GET", "/v1/uploads/{id}", operation_id="getUpload"),
)
async def get_upload(
    upload_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> FileUploadView:
    """Return one upload so the history can reopen its real analysis state."""
    async with transaction(ROLE, principal) as session:
        row = await _upload(session, upload_id, principal, data_space)
        return FileUploadView.model_validate(row, from_attributes=True)


@router.get(
    "/v1/uploads/{id}/preview",
    response_model=UploadPreview,
    **endpoint("GET", "/v1/uploads/{id}/preview", operation_id="previewUpload"),
)
async def preview_upload(
    upload_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    store: Annotated[ObjectStore, Depends(object_store)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> UploadPreview:
    async with transaction(ROLE, principal) as session:
        row = await _upload(session, upload_id, principal, data_space)
    content = await store.get(BUCKET, row.object_key)
    extension = os.path.splitext(row.file_name)[1].lower()
    if extension in {".xlsx", ".xlsm"}:
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise Problem(503, "source_unavailable", "Excel preview is not configured.") from exc
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheet_name = row.sheet or workbook.sheetnames[0]
        if sheet_name not in workbook.sheetnames:
            raise Problem(422, "validation_failed", "The selected sheet does not exist.")
        values = workbook[sheet_name].iter_rows(values_only=True)
        rows = [list(item) for item in values]
        workbook.close()
    elif extension == ".xls":
        try:
            import xlrd
        except ImportError as exc:
            raise Problem(
                503, "source_unavailable", "Legacy Excel preview is not configured."
            ) from exc
        workbook = xlrd.open_workbook(file_contents=content, on_demand=True)
        sheet_name = row.sheet or workbook.sheet_names()[0]
        if sheet_name not in workbook.sheet_names():
            raise Problem(422, "validation_failed", "The selected sheet does not exist.")
        rows = [
            workbook.sheet_by_name(sheet_name).row_values(index)
            for index in range(workbook.sheet_by_name(sheet_name).nrows)
        ]
        workbook.release_resources()
    elif extension == ".csv":
        encoding = row.encoding or "utf-8-sig"
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError as exc:
            raise Problem(422, "validation_failed", "The file encoding is not readable.") from exc
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [list(item) for item in csv.reader(io.StringIO(text), dialect)]
    else:
        raise Problem(422, "validation_failed", "Only CSV and Excel previews are supported.")
    if not rows:
        return UploadPreview(file_name=row.file_name, columns=[], rows=[], truncated=False)
    header_index = max((row.header_row or 1) - 1, 0)
    if header_index >= len(rows):
        raise Problem(422, "validation_failed", "The configured header row is outside the file.")
    columns = [
        str(value or "").strip() or f"Cột {index + 1}"
        for index, value in enumerate(rows[header_index])
    ]
    data = rows[header_index + 1 : header_index + 1 + limit]
    return UploadPreview(
        file_name=row.file_name,
        columns=columns,
        rows=[list(item) for item in data],
        truncated=len(rows) > header_index + 1 + limit,
    )


async def _upload(
    session: AsyncSession, upload_id: uuid.UUID, principal: Principal, data_space: uuid.UUID
) -> ingest.FileUpload:
    up = ingest.FileUpload
    row = await session.scalar(
        select(up)
        .where(up.id == upload_id, up.org_id == principal.org_id, up.data_space_id == data_space)
        .with_for_update()
    )
    if row is None:
        raise Problem(404, "not_found", "Upload not found.")
    return row


@router.patch(
    "/v1/uploads/{id}",
    response_model=FileUploadView,
    **endpoint("PATCH", "/v1/uploads/{id}", operation_id="updateUpload"),
)
async def update_upload(
    body: UploadPatch, upload_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> FileUploadView:
    changes = body.model_dump(exclude_unset=True)
    async with transaction(ROLE, principal) as session:
        row = await _upload(session, upload_id, principal, data_space)
        if row.status not in EDITABLE_UPLOAD:
            raise Problem(409, "invalid_state", f"The upload is {row.status}.")
        target = changes.get("target_dataset_id")
        if target is not None:
            dataset = await _dataset(session, target, principal, data_space)
            # UC-04-08 4a: a file is merged only into a manual table that already exists.
            if dataset is None or dataset.origin != "upload":
                raise Problem(
                    422, "validation_failed", "target_dataset_id is not a manual table here."
                )
        for field, value in changes.items():
            setattr(row, field, value)
        if row.status == "analyzed" and changes:
            row.status = "mapped"
        row.updated_by, row.updated_at = principal.user_id, _now()
        # The worker re-checks the file with the new mapping and refreshes error_ratio.
        session.add(
            _event(
                principal,
                "file_upload",
                row.id,
                "file_upload.mapped",
                file_upload_id=row.id,
                data_space_id=data_space,
                fields=sorted(changes),
                status=row.status,
            )
        )
        await session.flush()
        return FileUploadView.model_validate(row, from_attributes=True)


@router.delete(
    "/v1/uploads/{id}",
    status_code=204,
    **endpoint("DELETE", "/v1/uploads/{id}", operation_id="deleteUpload"),
)
async def delete_upload(
    upload_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    store: Annotated[ObjectStore, Depends(object_store)],
) -> Response:
    async with transaction(ROLE, principal) as session:
        row = await _upload(session, upload_id, principal, data_space)
        await store.delete(BUCKET, row.object_key)
        session.add(
            _event(
                principal,
                "file_upload",
                row.id,
                "file_upload.deleted",
                file_upload_id=row.id,
                data_space_id=data_space,
                object_key=row.object_key,
            )
        )
        await session.delete(row)
    return Response(status_code=204)


async def _commit_upload_impl(
    upload_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    body: UploadPatch | None = None,
) -> DatasetView:
    body = body or UploadPatch()
    async with transaction(ROLE, principal) as session:
        row = await _upload(session, upload_id, principal, data_space)
        if row.status not in COMMITTABLE_UPLOAD:
            raise Problem(
                409, "invalid_state", f"The upload is {row.status}; it is written once analysed."
            )
        if row.error_ratio is not None and row.error_ratio > decimal.Decimal("0.05"):
            raise Problem(
                422,
                "business_rule_violated",
                "More than 5% of the upload rows are invalid; the file cannot be written.",
            )
        if body.column_map is not None:
            row.column_map = body.column_map
        expires_at = _now() + datetime.timedelta(days=UPLOAD_TABLE_DAYS)
        existing = None
        if row.target_dataset_id is not None:
            existing = await _dataset(session, row.target_dataset_id, principal, data_space)
            if existing is None:
                raise Problem(422, "validation_failed", "The target table no longer exists.")
        else:
            # A new manual table: catalog registers it under this id from file_upload.committed
            # (origin upload, authority level 3, BR-58), so the id is known now.
            row.target_dataset_id = uuid7()
        # UC-04-08 E2: more than 5% of rows in error refuses the write; the CHECK on file_upload
        # enforces it (422 business_rule_violated).
        row.status = "committed"
        row.updated_by, row.updated_at = principal.user_id, _now()
        await session.flush()
        session.add(
            _event(
                principal,
                "file_upload",
                row.id,
                "file_upload.committed",
                file_upload_id=row.id,
                data_space_id=data_space,
                target_dataset_id=row.target_dataset_id,
                new_table=existing is None,
                replace_mode=row.replace_mode,
                table_expires_at=expires_at,
                status=row.status,
            )
        )
    if existing is not None:
        return DatasetView(
            id=existing.id,
            data_space_id=existing.data_space_id,
            origin=existing.origin,
            registered=True,
            schema_name=existing.schema_name,
            table_name=existing.table_name,
            display_name=existing.display_name,
            scope=existing.scope,
            expires_at=existing.expires_at,
            current_version=existing.current_version,
            quality_state=existing.quality_state,
            enabled=existing.enabled,
        )
    return DatasetView(
        id=row.target_dataset_id,
        data_space_id=data_space,
        origin="upload",
        registered=False,
        display_name=row.file_name,
        expires_at=expires_at,
    )


# --- quality ------------------------------------------------------------------------------------


async def _rule(
    session: AsyncSession, rule_id: uuid.UUID, principal: Principal, data_space: uuid.UUID
) -> ingest.QualityRule:
    rule = ingest.QualityRule
    row = await session.scalar(
        select(rule)
        .where(
            rule.id == rule_id, rule.org_id == principal.org_id, rule.data_space_id == data_space
        )
        .with_for_update()
    )
    if row is None:
        raise Problem(404, "not_found", "Quality rule not found.")
    return row


@router.get(
    "/v1/quality-rules",
    response_model=Page[QualityRuleView],
    **endpoint("GET", "/v1/quality-rules", operation_id="listQualityRules"),
)
async def list_quality_rules(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    order: Annotated[
        Order,
        Depends(
            order_by(
                "created_at", "updated_at", "kind", "severity", "status", default="created_at desc"
            )
        ),
    ],
    dataset_id: uuid.UUID | None = None,
    severity: Severity | None = None,
    status: str | None = None,
) -> Page[QualityRuleView]:
    rule = ingest.QualityRule
    query = select(rule).where(rule.org_id == principal.org_id, rule.data_space_id == data_space)
    if dataset_id:
        query = query.where(rule.dataset_id == dataset_id)
    if severity:
        query = query.where(rule.severity == severity)
    if status:
        query = query.where(rule.status == status)
    query = keyset(
        query,
        columns={
            "created_at": rule.created_at,
            "updated_at": rule.updated_at,
            "kind": rule.kind,
            "severity": rule.severity,
            "status": rule.status,
        },
        tiebreak=rule.id,
        order=order,
        page=page,
    )
    async with transaction(ROLE, principal) as session:
        rows, token = next_page(list((await session.scalars(query)).all()), order=order, page=page)
    return Page(
        items=[QualityRuleView.model_validate(r, from_attributes=True) for r in rows],
        next_page_token=token,
    )


@router.post(
    "/v1/quality-rules",
    response_model=QualityRuleView,
    status_code=201,
    **endpoint("POST", "/v1/quality-rules", operation_id="createQualityRule"),
)
async def create_quality_rule(
    body: QualityRuleCreate, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> QualityRuleView:
    async with transaction(ROLE, principal) as session:
        # UC-06-07 precondition: the table is in the catalog.
        if not await _dataset(session, body.dataset_id, principal, data_space):
            raise Problem(422, "validation_failed", "dataset_id is not a dataset here.")
        row = ingest.QualityRule(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=data_space,
            dataset_id=body.dataset_id,
            column_name=body.column_name,
            kind=body.kind,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        if body.params is not None:
            row.params = body.params
        if body.severity is not None:
            row.severity = body.severity
        session.add(row)
        await session.flush()
        await session.refresh(row)
        # The data-quality agent runs it on the current data (step 2) and after every load.
        session.add(
            _event(
                principal,
                "quality_rule",
                row.id,
                "quality_rule.created",
                quality_rule_id=row.id,
                dataset_id=row.dataset_id,
                data_space_id=data_space,
                severity=row.severity,
                status=row.status,
            )
        )
        return QualityRuleView.model_validate(row, from_attributes=True)


@router.patch(
    "/v1/quality-rules/{id}",
    response_model=QualityRuleView,
    **endpoint("PATCH", "/v1/quality-rules/{id}", operation_id="updateQualityRule"),
)
async def update_quality_rule(
    body: QualityRulePatch,
    rule_id: IdPath,
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
) -> QualityRuleView:
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    async with transaction(ROLE, principal) as session:
        row = await _rule(session, rule_id, principal, data_space)
        for field, value in changes.items():
            setattr(row, field, value)
        row.updated_by, row.updated_at = principal.user_id, _now()
        session.add(
            _event(
                principal,
                "quality_rule",
                row.id,
                "quality_rule.updated",
                quality_rule_id=row.id,
                dataset_id=row.dataset_id,
                data_space_id=data_space,
                fields=sorted(changes),
                severity=row.severity,
                status=row.status,
            )
        )
        await session.flush()
        return QualityRuleView.model_validate(row, from_attributes=True)


def _result_view(result: ingest.QualityResult, rule: ingest.QualityRule) -> QualityResultView:
    return QualityResultView(
        id=result.id,
        rule_id=result.rule_id,
        run_kind=result.run_kind,
        run_ref=result.run_ref,
        passed=result.passed,
        violations=result.violations,
        sample_ref=result.sample_ref,
        acknowledged_by=result.acknowledged_by,
        acknowledged_at=result.acknowledged_at,
        acknowledged_note=result.acknowledged_note,
        checked_at=result.checked_at,
        dataset_id=rule.dataset_id,
        severity=rule.severity,
    )


@router.get(
    "/v1/quality-results",
    response_model=Page[QualityResultView],
    **endpoint("GET", "/v1/quality-results", operation_id="listQualityResults"),
)
async def list_quality_results(
    principal: CurrentPrincipal,
    data_space: CurrentDataSpace,
    page: Annotated[PageRequest, Depends(page_request)],
    dataset_id: uuid.UUID | None = None,
    passed: bool | None = None,
    from_: Annotated[datetime.datetime | None, Query(alias="from")] = None,
    to: datetime.datetime | None = None,
    rule_id: uuid.UUID | None = None,
) -> Page[QualityResultView]:
    result, rule = ingest.QualityResult, ingest.QualityRule
    query = (
        select(result, rule)
        .join(rule, rule.id == result.rule_id)
        .where(result.org_id == principal.org_id, rule.data_space_id == data_space)
    )
    if dataset_id:
        query = query.where(rule.dataset_id == dataset_id)
    if rule_id:
        query = query.where(result.rule_id == rule_id)
    if passed is not None:
        query = query.where(result.passed.is_(passed))
    if from_:
        query = query.where(result.checked_at >= from_)
    if to:
        query = query.where(result.checked_at < to)
    after = _after(page)
    if after:
        query = query.where(tuple_(result.checked_at, result.id) < after)
    query = query.order_by(result.checked_at.desc(), result.id.desc()).limit(page.page_size + 1)
    async with transaction(ROLE, principal) as session:
        rows, token = _cut(
            [(r[0], r[1]) for r in (await session.execute(query)).all()],
            page,
            lambda r: (r[0].checked_at, r[0].id),
        )
    return Page(items=[_result_view(res, rul) for res, rul in rows], next_page_token=token)


@router.post(
    "/v1/quality-results/{id}:acknowledge",
    response_model=QualityResultView,
    **endpoint(
        "POST", "/v1/quality-results/{id}:acknowledge", operation_id="acknowledgeQualityResult"
    ),
)
async def acknowledge_quality_result(
    body: Acknowledge, result_id: IdPath, principal: CurrentPrincipal, data_space: CurrentDataSpace
) -> QualityResultView:
    result, rule = ingest.QualityResult, ingest.QualityRule
    async with transaction(ROLE, principal) as session:
        found = (
            await session.execute(
                select(result, rule)
                .join(rule, rule.id == result.rule_id)
                .where(
                    result.id == result_id,
                    result.org_id == principal.org_id,
                    rule.data_space_id == data_space,
                )
                .with_for_update(of=result)
            )
        ).first()
        if found is None:
            raise Problem(404, "not_found", "Quality result not found.")
        res, rul = found[0], found[1]
        # BR-116: only a failed high-severity check blocks reports, so only it is acknowledged.
        if res.passed or rul.severity != "high":
            raise Problem(
                409, "invalid_state", "Only a failed high-severity check needs acknowledging."
            )
        if res.acknowledged_by is not None:
            raise Problem(409, "invalid_state", "The result is already acknowledged.")
        res.acknowledged_by = principal.user_id
        # BR-116: the reason to keep using the data is kept with the acknowledgement (12/10/2026);
        # the event carries ids only.
        res.acknowledged_at = _now()
        res.acknowledged_note = body.note.strip()
        session.add(
            _event(
                principal,
                "quality_result",
                res.id,
                "quality_result.acknowledged",
                quality_result_id=res.id,
                quality_rule_id=rul.id,
                dataset_id=rul.dataset_id,
                data_space_id=data_space,
                acknowledged_by=principal.user_id,
            )
        )
        await session.flush()
        return _result_view(res, rul)
