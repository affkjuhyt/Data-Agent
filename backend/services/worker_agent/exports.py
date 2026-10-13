"""Exporting an answer to a file (UC-01-06): the `export_job.requested` handler.

The api records the export `queued`, or `waiting_approval` when it carries detail rows or more
rows than the export policy allows (BR-85). For the latter this opens the HITL request (kind
export, decided by a steward of the Data Space, never by the requester) and records its id on
the job; `hitl.resolved` comes back through resolutions.py, which queues the job on approval and
runs it here.

Running: the job is claimed `queued` → `running` in one statement. The file holds the answer's
figures and, when asked, the detail rows behind them, read again with the requester's rights
(BR-07) rather than copied from whoever asked the question; a figure the answer masked (BR-178)
stays empty and brings no rows. A footer names the exporter, the time, the knowledge time and
each metric's definition (AC-01-06-01). Any text cell starting with = + - @ is written behind an
apostrophe so a spreadsheet never reads it as a formula (AC-01-06-04). The file goes to
da-exports and the job becomes `ready` with a 24 hour expiry (BR-86); an audit row is requested
with the row count and the columns. CSV and Excel are written; PDF and PNG need a renderer the
worker does not have, and such a job fails saying so.
"""

from __future__ import annotations

import csv
import datetime
import io
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, context, qa
from libs.dataagent_db.models import query as q
from services.worker_agent import hitl, storage
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import run_query
from services.worker_agent.questions import secrets

logger = logging.getLogger(__name__)

EVENT_TYPE = "export_job.requested"
BUCKET = "da-exports"
LINK_HOURS = 24  # BR-86
NAMESPACE = uuid.UUID("0f3b6f4e-8a0c-4b8e-9e55-0c0b7c9d1a21")
CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
NUMERIC_TYPES = re.compile(
    r"^(numeric|decimal|int[248]?|integer|bigint|smallint|float[48]?|real|double)", re.I
)
_RISKY = ("=", "+", "-", "@")


class ExportFailed(RuntimeError):
    """The export cannot be written; the message is the job's reason."""


@dataclass
class Sheet:
    title: str
    columns: list[str]
    rows: list[list[Any]]
    numeric: set[int] = field(default_factory=set)


_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def safe(value: Any, numeric: bool = False, *, as_number: bool = False) -> Any:
    """A cell value a spreadsheet cannot run (AC-01-06-04). A number of a numeric column stays a
    number: exact text in a CSV, a number in a workbook."""
    if value is None:
        return ""
    text = str(value)
    if numeric and _NUMBER.match(text):
        if not as_number:
            return text
        return int(text) if "." not in text else float(text)
    return "'" + text if text.startswith(_RISKY) else text


# ---------------------------------------------------------------- approval


async def _open_approval(event: Event) -> None:
    p = event.payload
    export_id = uuid.UUID(str(p["export_job_id"]))
    requester = uuid.UUID(str(p["requested_by"]))
    request_id = uuid.uuid5(NAMESPACE, str(export_id))
    async with scoped_session("svc_qa", event.org_id, requester) as session:
        job = await session.get(qa.ExportJob, export_id, with_for_update=True)
        if job is None or job.status != "waiting_approval":
            return
        job.hitl_request_id = request_id
    rows = p.get("row_count")
    await hitl.open_request(
        Event(
            event_id=event.event_id,
            org_id=event.org_id,
            event_type=hitl.EVENT_TYPE,
            aggregate_type="export_job",
            aggregate_id=export_id,
            payload={
                "hitl_request_id": str(request_id),
                "data_space_id": p.get("data_space_id"),
                "kind": "export",
                "risk": "high" if p.get("include_detail_rows") else "medium",
                "title": "Xuất kết quả kèm dòng chi tiết"
                if p.get("include_detail_rows")
                else f"Xuất {rows} dòng kết quả",
                "source_service": "qa",
                "source_ref": str(export_id),
                "created_by": str(requester),
                "context": {
                    "answer_id": str(p.get("answer_id")),
                    "format": p.get("format"),
                    "row_count": rows,
                    "include_detail_rows": bool(p.get("include_detail_rows")),
                },
                "assignee_capability": "steward",
                # BR-85: whoever asked for the file does not approve it.
                "excluded_approver_ids": [str(requester)],
            },
        )
    )


# ---------------------------------------------------------------- the file


async def _figures_sheet(
    job: qa.ExportJob, requester: uuid.UUID
) -> tuple[Sheet, list[str], list[dict[str, Any]], str]:
    async with scoped_session("svc_qa", job.org_id, requester) as session:
        answer = await session.get(qa.Answer, job.answer_id)
        if answer is None:
            raise ExportFailed("Câu trả lời không còn.")
        question = await session.get(qa.Question, answer.question_id)
        figures = [f for f in (answer.figures or []) if isinstance(f, dict)]
        version_ids = {
            uuid.UUID(f["metric_version_id"]) for f in figures if f.get("metric_version_id")
        }
        versions = (
            (
                await session.scalars(
                    select(context.MetricVersion).where(context.MetricVersion.id.in_(version_ids))
                )
            ).all()
            if version_ids
            else []
        )
    definitions = [f"{v.display_name}: {v.expression}" for v in versions]
    as_of = (
        str(question.as_of)
        if question is not None and question.as_of
        else f"dữ liệu mới nhất lúc trả lời ({answer.created_at:%d/%m/%Y %H:%M})"
    )
    sheet = Sheet(
        "Kết quả",
        ["Mã", "Nhãn", "Giá trị", "Đơn vị"],
        [
            [
                f.get("key"),
                f.get("label"),
                f.get("value") if f.get("display_state", "shown") == "shown" else None,
                f.get("unit"),
            ]
            for f in figures
        ],
        numeric={2},
    )
    return sheet, definitions, figures, as_of


async def _detail_sheets(
    job: qa.ExportJob, requester: uuid.UUID, figures: list[dict[str, Any]]
) -> list[Sheet]:
    run_ids = sorted(
        {
            uuid.UUID(str(f["query_run_id"]))
            for f in figures
            if f.get("query_run_id") and f.get("display_state", "shown") == "shown"
        },
        key=str,
    )
    sheets: list[Sheet] = []
    for n, run_id in enumerate(run_ids, start=1):
        async with scoped_session("svc_query", job.org_id, requester) as session:
            original = await session.scalar(select(q.QueryRun).where(q.QueryRun.id == run_id))
            source = (
                await session.get(catalog.Source, original.source_id)
                if original and original.source_id
                else None
            )
        if original is None or source is None or not original.sql_generated:
            raise ExportFailed("Không còn truy vấn gốc của số liệu để lấy dòng chi tiết.")
        outcome = await run_query(
            org_id=job.org_id,
            data_space_id=original.data_space_id,
            principal_id=requester,
            caller_kind="ad_hoc",
            caller_id=job.id,
            source=source,
            sql=original.sql_generated,
            secrets=secrets(),
            metric_version_ids=list(original.metric_version_ids or ()),
        )
        if not outcome.ok:
            raise ExportFailed(f"Không đọc lại được dòng chi tiết: {outcome.error}")
        sheets.append(
            Sheet(
                f"Chi tiết {n}",
                [c["name"] for c in outcome.columns],
                outcome.rows,
                numeric={
                    i
                    for i, c in enumerate(outcome.columns)
                    if NUMERIC_TYPES.match(c.get("type", ""))
                },
            )
        )
    return sheets


def _footer(
    requester: str, now: datetime.datetime, as_of: str, definitions: Sequence[str]
) -> list[list[str]]:
    return [
        [],
        ["Người xuất", requester],
        ["Thời điểm xuất", now.strftime("%d/%m/%Y %H:%M UTC")],
        ["Dữ liệu tính đến", as_of],
        *[["Định nghĩa chỉ số", d] for d in definitions],
    ]


def write_csv(sheets: list[Sheet], footer: list[list[str]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    for i, sheet in enumerate(sheets):
        if i:
            writer.writerow([])
            writer.writerow([safe(sheet.title)])
        writer.writerow([safe(c) for c in sheet.columns])
        for row in sheet.rows:
            writer.writerow([safe(v, j in sheet.numeric) for j, v in enumerate(row)])
    for line in footer:
        writer.writerow([safe(v) for v in line])
    # Excel opens a UTF-8 CSV with Vietnamese text correctly only with the BOM.
    return out.getvalue().encode("utf-8-sig")


def write_xlsx(sheets: list[Sheet], footer: list[list[str]]) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    first = True
    for sheet in sheets:
        ws = book.active if first else book.create_sheet()
        first = False
        assert ws is not None
        ws.title = sheet.title[:31]
        ws.append([safe(c) for c in sheet.columns])
        for row in sheet.rows:
            ws.append([safe(v, j in sheet.numeric, as_number=True) for j, v in enumerate(row)])
        if sheet is sheets[0]:
            for line in footer:
                ws.append([safe(v) for v in line])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


# ---------------------------------------------------------------- running


async def _claim(org: uuid.UUID, export_id: uuid.UUID, requester: uuid.UUID) -> qa.ExportJob | None:
    async with scoped_session("svc_qa", org, requester) as session:
        return await session.scalar(
            update(qa.ExportJob)
            .where(qa.ExportJob.id == export_id, qa.ExportJob.status == "queued")
            .values(status="running")
            .returning(qa.ExportJob)
        )


async def _finish(
    job: qa.ExportJob, requester: uuid.UUID, audit: dict[str, Any] | None = None, **values: Any
) -> None:
    async with scoped_session("svc_qa", job.org_id, requester) as session:
        await session.execute(
            update(qa.ExportJob)
            .where(qa.ExportJob.id == job.id, qa.ExportJob.status == "running")
            .values(**values)
        )
        if audit is not None:
            session.add(
                qa.Outbox(
                    id=uuid7(),
                    org_id=job.org_id,
                    aggregate_type="export_job",
                    aggregate_id=job.id,
                    event_type="audit.requested",
                    payload={
                        "action": "export",
                        "object_kind": "export_job",
                        "object_id": str(job.id),
                        "actor_id": str(requester),
                        "client_id": None,
                        "before": None,
                        "after": audit,
                    },
                )
            )


async def run_export(org: uuid.UUID, export_id: uuid.UUID, requester: uuid.UUID) -> None:
    job = await _claim(org, export_id, requester)
    if job is None:
        return  # taken, finished, or still waiting for approval
    try:
        if job.format not in CONTENT_TYPES:
            raise ExportFailed(f"Định dạng {job.format} chưa xuất được; hãy chọn Excel hoặc CSV.")
        if job.answer_id is None:
            raise ExportFailed("Chỉ xuất được câu trả lời.")
        result, definitions, figures, as_of = await _figures_sheet(job, requester)
        sheets = [result]
        if job.include_detail_rows:
            sheets += await _detail_sheets(job, requester, figures)
        now = datetime.datetime.now(datetime.UTC)
        footer = _footer(str(requester), now, as_of, definitions)
        body = write_csv(sheets, footer) if job.format == "csv" else write_xlsx(sheets, footer)
        key = f"{org}/{job.id}.{job.format}"
        await storage.store().put(BUCKET, key, body, CONTENT_TYPES[job.format])
    except (ExportFailed, storage.StoreUnavailable) as exc:
        logger.warning("worker-agent | export %s failed: %s", job.id, type(exc).__name__)
        await _finish(job, requester, status="failed")
        return
    await _finish(
        job,
        requester,
        status="ready",
        object_key=key,
        expires_at=now + datetime.timedelta(hours=LINK_HOURS),
        row_count=sum(len(s.rows) for s in sheets),
        audit={
            "format": job.format,
            "row_count": sum(len(s.rows) for s in sheets),
            "columns": {s.title: s.columns for s in sheets},
        },
    )


async def export_requested(event: Event) -> None:
    p = event.payload
    if p.get("status") == "waiting_approval":
        await _open_approval(event)
        return
    await run_export(
        event.org_id,
        uuid.UUID(str(p["export_job_id"])),
        uuid.UUID(str(p["requested_by"])),
    )


HANDLERS: dict[str, Handler] = {EVENT_TYPE: export_requested}
