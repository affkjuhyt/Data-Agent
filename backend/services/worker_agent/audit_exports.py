"""Writing an audit export (UC-11-03 step 4, BR-138): the `audit.export_requested` handler.

POST /v1/audit:export records audit.export_job `queued` with the filters of GET /v1/audit and the
last seq they matched (`to_seq`), and writes this event to audit.outbox; an export over the
threshold waits in `waiting_approval` and is started by its approval instead (resolutions.py).

Running: the job is claimed `queued` → `running` in one statement, so a redelivered event or an
approval delivered twice runs it once. The rows are the organisation's audit_log rows matching the
filters up to `to_seq`, in seq order: what the requester counted, whatever the log has grown to
since. Every column of the row is written, the chain's prev_hash and hash included, so the file can
be checked against audit.verify_chain; before/after were masked of personal data when they were
logged (AC-11-03-06). CSV cells go through exports.safe, so no cell starts a spreadsheet formula;
JSONL is one object per row. The file goes to da-exports beside the answer exports and the job
becomes `ready` with its object key, signature and a 24 hour expiry (BR-86's link lifetime).

The signature (export_job.signature: "chữ ký số của hệ thống trên SHA-256 của tệp") is
"v1;alg=hmac-sha256;sha256=<file digest>;sig=<HMAC-SHA256 of the digest>". The stack holds no
signing key pair for files, so it is an HMAC whose key is derived per organisation from
AUDIT_EXPORT_SIGNING_KEY (the way governance/dsr.py derives DSR_SUBJECT_KEY): the system checks it
with `verify`; it is not a public-key signature a third party can check alone. Without the key the
job fails rather than ship an unsigned file.

svc_audit_worker reads audit_log and export_job and moves a job queued → running → ready/failed
(routines/audit.sql, v3_0034); it cannot write the log. The export itself is in the log already:
the api appended `audit.export_requested` when it was asked for.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import os
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, select, update

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import audit
from services.worker_agent import exports, storage
from services.worker_agent.events import Event, Handler

logger = logging.getLogger(__name__)

EVENT_TYPE = "audit.export_requested"
ROLE = "svc_audit_worker"
KEY_ENV = "AUDIT_EXPORT_SIGNING_KEY"
PAGE = 5_000
CONTENT_TYPES = {"csv": "text/csv; charset=utf-8", "jsonl": "application/x-ndjson"}
# The columns of GET /v1/audit's AuditLogView, in its order.
COLUMNS = (
    "id",
    "seq",
    "occurred_at",
    "actor_kind",
    "actor_id",
    "on_behalf_of",
    "data_space_id",
    "service",
    "action",
    "object_kind",
    "object_id",
    "outcome",
    "before",
    "after",
    "agent_run_id",
    "ip",
    "prev_hash",
    "hash",
)
_ID_FILTERS = ("actor_id", "on_behalf_of", "object_id", "agent_run_id")
_TEXT_FILTERS = ("object_kind", "action", "outcome")


class SigningKeyMissing(RuntimeError):
    """AUDIT_EXPORT_SIGNING_KEY is not set: no export can be signed."""


def signing_key() -> bytes:
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise SigningKeyMissing(f"{KEY_ENV} is not set")
    return key.encode()


def _org_key(org_id: uuid.UUID, master: bytes) -> bytes:
    # One key per organisation: a signature of one organisation's file is not valid for another's.
    return hmac.new(master, f"audit-export:{org_id}".encode(), hashlib.sha256).digest()


def sign(org_id: uuid.UUID, body: bytes, master: bytes) -> str:
    digest = hashlib.sha256(body).hexdigest()
    mac = hmac.new(_org_key(org_id, master), digest.encode(), hashlib.sha256).hexdigest()
    return f"v1;alg=hmac-sha256;sha256={digest};sig={mac}"


def verify(org_id: uuid.UUID, body: bytes, signature: str, master: bytes) -> bool:
    return hmac.compare_digest(sign(org_id, body, master), signature)


def _plain(value: Any) -> Any:
    """A column value as JSON holds it: ids, times and addresses as text."""
    if value is None or isinstance(value, bool | int | float | str | dict | list):
        return value
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return str(value)


def _cell(value: Any) -> Any:
    value = _plain(value)
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def write_file(fmt: str, rows: Sequence[dict[str, Any]]) -> bytes:
    if fmt == "jsonl":
        return b"".join(
            json.dumps({c: _plain(r[c]) for c in COLUMNS}, ensure_ascii=False).encode() + b"\n"
            for r in rows
        )
    sheet = exports.Sheet(
        "audit_log",
        list(COLUMNS),
        [[_cell(r[c]) for c in COLUMNS] for r in rows],
        numeric={COLUMNS.index("seq")},
    )
    return exports.write_csv([sheet], [])


def _filtered(query: Select[Any], filters: dict[str, Any]) -> Select[Any]:
    """GET /v1/audit's filters (api audit/routes._where), bounded by the seq counted at request."""
    log = audit.AuditLog
    for name in _ID_FILTERS + _TEXT_FILTERS:
        value = filters.get(name)
        if value is not None:
            value = uuid.UUID(str(value)) if name in _ID_FILTERS else str(value)
            query = query.where(getattr(log, name) == value)
    if filters.get("from") is not None:
        query = query.where(log.occurred_at >= datetime.datetime.fromisoformat(filters["from"]))
    if filters.get("to") is not None:
        query = query.where(log.occurred_at < datetime.datetime.fromisoformat(filters["to"]))
    return query.where(log.seq <= int(filters.get("to_seq") or 0))


async def _rows(org: uuid.UUID, filters: dict[str, Any]) -> list[dict[str, Any]]:
    log = audit.AuditLog
    columns = [getattr(log, c) for c in COLUMNS]
    rows: list[dict[str, Any]] = []
    after = 0
    async with scoped_session(ROLE, org) as session:
        while True:
            page = (
                await session.execute(
                    _filtered(select(*columns), filters)
                    .where(log.seq > after)
                    .order_by(log.seq)
                    .limit(PAGE)
                )
            ).all()
            rows += [dict(zip(COLUMNS, r, strict=True)) for r in page]
            if len(page) < PAGE:
                return rows
            after = rows[-1]["seq"]


async def _move(org: uuid.UUID, job_id: uuid.UUID, frm: str, **values: Any) -> Any:
    async with scoped_session(ROLE, org) as session:
        return await session.scalar(
            update(audit.ExportJob)
            .where(audit.ExportJob.id == job_id, audit.ExportJob.status == frm)
            .values(**values, updated_at=datetime.datetime.now(datetime.UTC))
            .returning(audit.ExportJob)
        )


async def run(org: uuid.UUID, job_id: uuid.UUID) -> None:
    """Write, sign and store a queued export; a job in any other state is left alone."""
    job = await _move(org, job_id, "queued", status="running")
    if job is None:
        return  # taken, finished, or still waiting for approval
    try:
        if job.format not in CONTENT_TYPES:
            raise exports.ExportFailed(f"format {job.format}")
        master = signing_key()
        body = write_file(job.format, await _rows(org, dict(job.filters or {})))
        key = f"{org}/audit/{job.id}.{job.format}"
        await storage.store().put(exports.BUCKET, key, body, CONTENT_TYPES[job.format])
    except (exports.ExportFailed, SigningKeyMissing, storage.StoreUnavailable) as exc:
        logger.warning("worker-agent | audit export %s failed: %s", job_id, exc)
        await _move(org, job_id, "running", status="failed")
        return
    except Exception:
        # The claim is committed: a job left `running` would never be taken again, so whatever
        # broke ends it `failed` (the requester exports again) before the error goes up.
        await _move(org, job_id, "running", status="failed")
        raise
    await _move(
        org,
        job_id,
        "running",
        status="ready",
        object_key=key,
        signature=sign(org, body, master),
        expires_at=datetime.datetime.now(datetime.UTC)
        + datetime.timedelta(hours=exports.LINK_HOURS),
    )


async def export_requested(event: Event) -> None:
    await run(
        event.org_id, uuid.UUID(str(event.payload.get("export_job_id") or event.aggregate_id))
    )


HANDLERS: dict[str, Handler] = {EVENT_TYPE: export_requested}
