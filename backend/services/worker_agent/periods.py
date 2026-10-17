"""Closing a period (UC-10-02): the snapshot, its hashes, the reconciliation, the lock.

period.close_requested (the api set the period to closing):
1. every dataset of the period's calendar is read on its source as of the business date (the
   rows of that date for a snapshot table, the rows up to it for one with a business date
   column, the whole table otherwise) through the query executor (caller_kind "period"), and
   written to da-period-close as gzipped JSON lines, one object per row, with its SHA-256 and
   row count (period_snapshot_file). The bucket has object lock (AC-10-02-07). The lines are
   sorted, so the same rows always give the same file and hash: a retry re-reads every table,
   keeps a file already written when its rows are unchanged (AC-10-02-05), and fails the close
   when they are not, since the locked file and the rows reconciled would no longer agree. The
   design names Parquet; the worker writes JSON lines until it carries a Parquet writer.
2. each reconciliation item of the calendar is computed on the rows just captured and compared
   with its ledger value within its tolerance (period_reconciliation). An item is
   {"code", "dataset_id", "aggregate": "sum"|"count"|"count_distinct", "column",
    "ledger": {"value": n} or {"dataset_id", "aggregate", "column"}, "tolerance"}.
3. all matched: the period is closed with the hash of the whole snapshot; one mismatched: it is
   close_failed and the screen shows which (AC-10-02-03); a person may then ask for an exception
   (the api's path) or fix the source and close again.
"""

from __future__ import annotations

import datetime
import decimal
import gzip
import hashlib
import json
import logging
import os
import uuid
from typing import Any

from sqlalchemy import delete, literal, select, update

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import catalog, inbox
from libs.dataagent_db.models import period as pd
from services.worker_agent import storage
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import run_query
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import load

logger = logging.getLogger(__name__)

ROLE = "svc_period"
CLOSE = "period.close_requested"
PUBLISH = "period.publish_requested"
PUBLISHED = "period.published"
BUCKET = os.environ.get("PERIOD_CLOSE_BUCKET", "da-period-close")
SNAPSHOT_ROWS = 500_000


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _plain(value: Any) -> Any:
    if isinstance(value, decimal.Decimal | datetime.date | datetime.datetime | uuid.UUID):
        return str(value)
    return value


def aggregate(kind: str, column: str | None, rows: list[dict[str, Any]]) -> decimal.Decimal:
    if kind == "count":
        return decimal.Decimal(len(rows))
    if column is None:
        raise ValueError(f"a {kind} item names a column")
    values = [r.get(column) for r in rows if r.get(column) is not None]
    if kind == "count_distinct":
        return decimal.Decimal(len({str(v) for v in values}))
    if kind == "sum":
        return sum((decimal.Decimal(str(v)) for v in values), decimal.Decimal(0))
    raise ValueError(f"unknown aggregate {kind}")


async def _source(
    session: Any, dataset: catalog.Dataset, org_id: uuid.UUID
) -> catalog.Source | None:
    from services.worker_agent.questions import _internal_workspace_source

    if dataset.source_id is None:
        return _internal_workspace_source(dataset.data_space_id, org_id)
    found: catalog.Source | None = await session.get(catalog.Source, dataset.source_id)
    return found


async def _rows(
    org_id: uuid.UUID,
    close: pd.PeriodClose,
    dataset: catalog.Dataset,
    actor: uuid.UUID,
    source: Any,
) -> list[dict[str, Any]] | str:
    """The dataset's rows as of the business date; or why they could not be read."""
    from services.worker_agent.questions import secrets

    table = f"{_ident(dataset.schema_name)}.{_ident(dataset.table_name)}"
    where = ""
    if dataset.business_date_col:
        op = "=" if dataset.history_kind == "snapshot" else "<="
        where = f" WHERE {_ident(dataset.business_date_col)} {op} DATE '{close.business_date.isoformat()}'"
    outcome = await run_query(
        org_id=org_id,
        data_space_id=close.data_space_id,
        principal_id=actor,
        caller_kind="period",
        caller_id=close.id,
        source=source,
        sql=f"SELECT * FROM {table}{where}",
        secrets=secrets(),
        limit=SNAPSHOT_ROWS,
    )
    if not outcome.ok:
        return outcome.error or outcome.status
    if outcome.truncated:
        return f"more than {SNAPSHOT_ROWS} rows"
    names = [c["name"] for c in outcome.columns]
    return [dict(zip(names, row, strict=True)) for row in outcome.rows]


async def _ledger(
    org_id: uuid.UUID, close: pd.PeriodClose, ledger: dict[str, Any], actor: uuid.UUID
) -> decimal.Decimal | str:
    if "value" in ledger:
        return decimal.Decimal(str(ledger["value"]))
    async with scoped_session("svc_catalog", org_id, actor) as session:
        dataset = await session.get(catalog.Dataset, uuid.UUID(str(ledger["dataset_id"])))
        source = await _source(session, dataset, org_id) if dataset else None
    if dataset is None or source is None or dataset.data_space_id != close.data_space_id:
        return "the ledger dataset is not readable in this Data Space"
    rows = await _rows(org_id, close, dataset, actor, source)
    if isinstance(rows, str):
        return rows
    return aggregate(str(ledger.get("aggregate", "sum")), ledger.get("column"), rows)


async def _end(
    org_id: uuid.UUID, close_id: uuid.UUID, status: str, snapshot_hash: str | None
) -> None:
    async with scoped_session(ROLE, org_id) as session:
        values: dict[str, Any] = {"status": status}
        if status == "closed":
            values |= {"snapshot_hash": snapshot_hash, "closed_at": _now()}
        await session.execute(
            update(pd.PeriodClose)
            .where(pd.PeriodClose.id == close_id, pd.PeriodClose.status == "closing")
            .values(**values)
        )


def _snapshot_hash(hashes: dict[str, str]) -> str:
    return hashlib.sha256(
        "\n".join(f"{k}:{v}" for k, v in sorted(hashes.items())).encode()
    ).hexdigest()


async def _capture(
    org_id: uuid.UUID,
    close: pd.PeriodClose,
    calendar: pd.PeriodCalendar,
    actor: uuid.UUID,
    written: dict[uuid.UUID, pd.PeriodSnapshotFile],
    *,
    write: bool = True,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]] | str:
    """Every calendar dataset's rows as of the business date and the hash of each one's file,
    writing the files not written yet (only checking them when `write` is off); or why not."""
    captured: dict[str, list[dict[str, Any]]] = {}
    hashes: dict[str, str] = {}
    for dataset_id in calendar.dataset_ids:
        async with scoped_session("svc_catalog", org_id, actor) as session:
            dataset = await session.get(catalog.Dataset, dataset_id)
            source = await _source(session, dataset, org_id) if dataset else None
        if dataset is None or source is None or dataset.data_space_id != close.data_space_id:
            return f"dataset {dataset_id} is not readable"
        rows = await _rows(org_id, close, dataset, actor, source)
        if isinstance(rows, str):
            return f"{dataset.table_name}: {rows}"
        captured[str(dataset_id)] = rows
        lines = sorted(
            json.dumps({k: _plain(v) for k, v in r.items()}, ensure_ascii=False, sort_keys=True)
            for r in rows
        )
        body = gzip.compress("".join(f"{line}\n" for line in lines).encode("utf-8"), mtime=0)
        sha = hashlib.sha256(body).hexdigest()
        hashes[str(dataset_id)] = sha
        if dataset_id in written:
            if written[dataset_id].sha256 != sha:
                return f"{dataset.table_name} changed since its snapshot was written"
            continue
        if not write:
            return f"{dataset.table_name} has no snapshot file"
        key = (
            f"{org_id}/{close.data_space_id}/{close.period_label}/v{close.version}/"
            f"{dataset_id}.jsonl.gz"
        )
        await storage.store().put(BUCKET, key, body, "application/gzip")
        async with scoped_session(ROLE, org_id, actor) as session:
            session.add(
                pd.PeriodSnapshotFile(
                    id=uuid7(),
                    org_id=org_id,
                    period_close_id=close.id,
                    dataset_id=dataset_id,
                    dataset_version=dataset.current_version,
                    object_key=key,
                    row_count=len(rows),
                    sha256=sha,
                )
            )
    return captured, hashes


async def close_requested(event: Event) -> None:
    org_id = event.org_id
    close_id = uuid.UUID(str(event.payload["period_close_id"]))
    async with scoped_session(ROLE, org_id) as session:
        close = await session.get(pd.PeriodClose, close_id)
        if close is None or close.status != "closing":
            return
        calendar = (
            await session.get(pd.PeriodCalendar, close.period_calendar_id)
            if close.period_calendar_id
            else None
        )
        written = {
            f.dataset_id: f
            for f in (
                await session.scalars(
                    select(pd.PeriodSnapshotFile).where(
                        pd.PeriodSnapshotFile.period_close_id == close_id
                    )
                )
            ).all()
        }
    actor = close.closed_by or close.created_by
    if calendar is None or actor is None:
        logger.info("worker-agent | period %s has no calendar or closer", close_id)
        await _end(org_id, close_id, "close_failed", None)
        return
    capture = await _capture(org_id, close, calendar, actor, written)
    if isinstance(capture, str):
        logger.info("worker-agent | period %s: %s", close_id, capture)
        await _end(org_id, close_id, "close_failed", None)
        return
    captured, hashes = capture
    results = []
    for item in calendar.reconciliation_items or []:
        code = str(item.get("code"))
        item_rows = captured.get(str(item.get("dataset_id")))
        tolerance = decimal.Decimal(str(item.get("tolerance", 0)))
        try:
            if item_rows is None:
                raise ValueError("the item's dataset is not in the snapshot")
            snapshot = aggregate(str(item.get("aggregate", "sum")), item.get("column"), item_rows)
            ledger = await _ledger(org_id, close, dict(item.get("ledger") or {}), actor)
            if isinstance(ledger, str):
                raise ValueError(ledger)
        except (ValueError, KeyError, decimal.InvalidOperation) as exc:
            logger.info(
                "worker-agent | period %s: item %s cannot be reconciled: %s", close_id, code, exc
            )
            await _end(org_id, close_id, "close_failed", None)
            return
        delta = snapshot - ledger
        status = "matched" if abs(delta) <= tolerance else "mismatched"
        results.append(
            pd.PeriodReconciliation(
                id=uuid7(),
                org_id=org_id,
                period_close_id=close_id,
                item_code=code,
                snapshot_value=snapshot,
                ledger_value=ledger,
                tolerance=tolerance,
                delta=delta,
                status=status,
            )
        )
    async with scoped_session(ROLE, org_id, actor) as session:
        # A close run again reconciles again. A person's exception (pending or approved) stands
        # only for the variance it was asked on: when the delta moved, it is replaced by the new
        # mismatch and a new exception is needed. Earlier matched/mismatched rows are replaced.
        decided = {
            r.item_code: r
            for r in (
                await session.scalars(
                    select(pd.PeriodReconciliation).where(
                        pd.PeriodReconciliation.period_close_id == close_id,
                        pd.PeriodReconciliation.status.in_(
                            ("exception_pending", "exception_approved")
                        ),
                    )
                )
            ).all()
        }
        kept = {
            r.item_code: decided[r.item_code].status
            for r in results
            if r.item_code in decided and decided[r.item_code].delta == r.delta
        }
        await session.execute(
            delete(pd.PeriodReconciliation).where(
                pd.PeriodReconciliation.period_close_id == close_id,
                pd.PeriodReconciliation.item_code.not_in(kept) if kept else literal(True),
            )
        )
        session.add_all(r for r in results if r.item_code not in kept)
    # An approved exception lets its item stand; a pending one still holds the close.
    mismatched = any(
        r.status == "mismatched" and kept.get(r.item_code) != "exception_approved" for r in results
    )
    snapshot_hash = _snapshot_hash(hashes)
    await _end(org_id, close_id, "close_failed" if mismatched else "closed", snapshot_hash)
    logger.info(
        "worker-agent | period %s %s (%d tables, %d items)",
        close_id,
        "close_failed" if mismatched else "closed",
        len(hashes),
        len(results),
    )


# --- filing (UC-10-03) ---------------------------------------------------------------------------


def period_range(kind: str, business_date: datetime.date) -> c.Period:
    """The days a period of `kind` ending on `business_date` covers (end exclusive)."""
    if kind == "month":
        start = business_date.replace(day=1)
    elif kind == "quarter":
        start = business_date.replace(month=3 * ((business_date.month - 1) // 3) + 1, day=1)
    elif kind == "year":
        start = business_date.replace(month=1, day=1)
    else:  # custom: the period is its business date
        start = business_date
    return c.Period(start=start, end=business_date + datetime.timedelta(days=1))


async def _overrides(
    org_id: uuid.UUID, close_id: uuid.UUID
) -> tuple[list[dict[str, Any]], dict[uuid.UUID, inbox.HitlRequest]]:
    """The manual numbers the filing asked for, from its own period.publish_requested (the
    latest for the period), and the approval requests opened for them so far, by id."""
    async with scoped_session(ROLE, org_id) as session:
        payload = await session.scalar(
            select(pd.Outbox.payload)
            .where(
                pd.Outbox.aggregate_id == close_id,
                pd.Outbox.event_type == PUBLISH,
            )
            .order_by(pd.Outbox.created_at.desc(), pd.Outbox.id.desc())
            .limit(1)
        )
    expected = list((payload or {}).get("overrides") or [])
    ids = [uuid.UUID(str(o["approval_id"])) for o in expected]
    if not ids:
        return expected, {}
    async with scoped_session("svc_inbox", org_id) as session:
        found = (
            await session.scalars(
                select(inbox.HitlRequest).where(
                    inbox.HitlRequest.id.in_(ids),
                    inbox.HitlRequest.source_service == "period",
                    inbox.HitlRequest.kind == "period_exception",
                    inbox.HitlRequest.source_ref == close_id,
                )
            )
        ).all()
    return expected, {r.id: r for r in found}


async def _unfile(org_id: uuid.UUID, close_id: uuid.UUID, why: str) -> None:
    """The filing could not be frozen: it is cleared, so the period can be filed again."""
    logger.info("worker-agent | period %s not filed: %s", close_id, why)
    async with scoped_session(ROLE, org_id) as session:
        await session.execute(
            update(pd.PeriodClose)
            .where(pd.PeriodClose.id == close_id, pd.PeriodClose.status == "closed")
            .values(filed_report_code=None, filed_at=None)
        )


async def _values(
    org_id: uuid.UUID,
    close: pd.PeriodClose,
    calendar: pd.PeriodCalendar,
    actor: uuid.UUID,
) -> dict[uuid.UUID, decimal.Decimal] | str:
    """Every certified metric of the Data Space computed on the period, by metric version; only
    the metrics read from the snapshotted tables (another table's number is not the period's)."""
    from services.worker_agent.questions import (
        QUERYABLE,
        _internal_workspace_source,
        dialect_for_source,
        secrets,
    )

    span = period_range(calendar.period_kind, close.business_date)
    snapshotted = set(calendar.dataset_ids)
    values: dict[uuid.UUID, decimal.Decimal] = {}
    async with scoped_session("svc_qa", org_id, actor) as session:
        layer = await load(session, close.data_space_id)
        compiled = []
        for version in layer.versions.values():
            if not version.certified:
                continue
            request = c.Request(version, period=span)
            try:
                first = c.compile_metric(request, layer.layer, dialect="postgres")
            except c.CompileError as exc:
                return f"{version.metric_code} does not compile: {exc}"
            if not set(first.dataset_ids) <= snapshotted:
                continue
            dataset = layer.datasets[first.dataset_ids[0]]
            source = (
                _internal_workspace_source(close.data_space_id, org_id)
                if dataset.schema_name == "workspace" and dataset.source_id is None
                else await session.get(catalog.Source, dataset.source_id)
            )
            if source is None or source.mode != "federated" or source.status not in QUERYABLE:
                return f"{version.metric_code}: its source is not queryable"
            compiled.append(
                (
                    source,
                    c.compile_metric(request, layer.layer, dialect=dialect_for_source(source.kind)),
                )
            )
    for source, metric in compiled:
        outcome = await run_query(
            org_id=org_id,
            data_space_id=close.data_space_id,
            principal_id=actor,
            caller_kind="period",
            caller_id=close.id,
            source=source,
            sql=metric.sql,
            secrets=secrets(),
            metric_version_ids=[metric.metric_version_id],
            limit=1,
        )
        if not outcome.ok:
            return f"metric version {metric.metric_version_id}: {outcome.error or outcome.status}"
        if outcome.rows and outcome.rows[0][-1] is not None:
            values[metric.metric_version_id] = decimal.Decimal(str(outcome.rows[0][-1]))
    return values


async def publish(org_id: uuid.UUID, close_id: uuid.UUID) -> str | None:
    """Freeze the filing of a closed period once every number it overrides is approved: each
    certified metric computed on the snapshot into published_result (insert-only, BR-42), the
    period filed, period.published. Returns the period's status after, or None when it waits."""
    async with scoped_session(ROLE, org_id) as session:
        close = await session.get(pd.PeriodClose, close_id)
        if close is None or close.status != "closed" or close.filed_report_code is None:
            return None
        calendar = (
            await session.get(pd.PeriodCalendar, close.period_calendar_id)
            if close.period_calendar_id
            else None
        )
        written = {
            f.dataset_id: f
            for f in (
                await session.scalars(
                    select(pd.PeriodSnapshotFile).where(
                        pd.PeriodSnapshotFile.period_close_id == close_id
                    )
                )
            ).all()
        }
    filer = close.updated_by
    if calendar is None or filer is None:
        await _unfile(org_id, close_id, "the period has no calendar or filer")
        return "closed"
    expected, requests = await _overrides(org_id, close_id)
    # Every manual number waits for its own request, opened and decided: one not opened yet (the
    # inbox has not caught up with the filing) is waited for like one still open.
    if any(
        uuid.UUID(str(o["approval_id"])) not in requests
        or requests[uuid.UUID(str(o["approval_id"]))].status in ("open", "reassigned")
        for o in expected
    ):
        return None
    overrides: dict[uuid.UUID, tuple[dict[str, Any], inbox.HitlRequest]] = {}
    for o in expected:
        r = requests[uuid.UUID(str(o["approval_id"]))]
        ctx = r.context or {}
        if (
            r.status != "approved"
            or r.resolved_by in (None, filer)
            # What was approved is what is frozen: the request shows the same metric and number.
            or str(ctx.get("metric_version_id")) != str(o["metric_version_id"])
            or str(ctx.get("reported_value")) != str(o["reported_value"])
        ):
            await _unfile(org_id, close_id, "a manual number was not approved (BR-133)")
            return "closed"
        overrides[uuid.UUID(str(o["metric_version_id"]))] = (o, r)
    # The numbers are the snapshot's only while the tables still hold its rows (BR-42).
    capture = await _capture(org_id, close, calendar, filer, written, write=False)
    if isinstance(capture, str):
        await _unfile(org_id, close_id, capture)
        return "closed"
    if close.snapshot_hash and _snapshot_hash(capture[1]) != close.snapshot_hash:
        await _unfile(org_id, close_id, "the snapshot no longer matches its hash")
        return "closed"
    values = await _values(org_id, close, calendar, filer)
    if isinstance(values, str):
        await _unfile(org_id, close_id, values)
        return "closed"
    # The metrics were read from the live tables: they are the snapshot's only if the tables
    # still hold its rows after the reads as well as before.
    after = await _capture(org_id, close, calendar, filer, written, write=False)
    if isinstance(after, str) or after[1] != capture[1]:
        await _unfile(org_id, close_id, "the tables moved while the numbers were computed")
        return "closed"
    missing = set(overrides) - set(values)
    if missing:
        await _unfile(org_id, close_id, f"no computed number for {sorted(map(str, missing))}")
        return "closed"
    async with scoped_session(ROLE, org_id, filer) as session:
        locked = await session.scalar(
            select(pd.PeriodClose).where(pd.PeriodClose.id == close_id).with_for_update()
        )
        if locked is None or locked.status != "closed":
            return None  # another delivery filed it
        for version_id, value in sorted(values.items(), key=lambda kv: str(kv[0])):
            asked: dict[str, Any] | None = None
            approval: inbox.HitlRequest | None = None
            if version_id in overrides:
                asked, approval = overrides[version_id]
            reported = decimal.Decimal(str(asked["reported_value"])) if asked else None
            session.add(
                pd.PublishedResult(
                    id=uuid7(),
                    org_id=org_id,
                    period_close_id=close_id,
                    metric_version_id=version_id,
                    value=value,
                    reported_value=reported,
                    manual_override=asked is not None,
                    override_reason=str(asked["override_reason"]) if asked else None,
                    # The controller who decided, never a value the filer sent (BR-133).
                    approved_by=approval.resolved_by if approval else None,
                    report_ref=str(close.filed_report_code),
                    sha256=hashlib.sha256(
                        f"{version_id}|{value}|{reported}|{close.snapshot_hash}".encode()
                    ).hexdigest(),
                )
            )
        locked.status = "filed"
        session.add(
            pd.Outbox(
                id=uuid7(),
                org_id=org_id,
                aggregate_type="period_close",
                aggregate_id=close_id,
                event_type=PUBLISHED,
                payload={
                    "period_close_id": str(close_id),
                    "data_space_id": str(close.data_space_id),
                    "report_code": close.filed_report_code,
                    "metric_version_ids": [str(v) for v in values],
                    "status": "filed",
                },
            )
        )
    logger.info("worker-agent | period %s filed with %d numbers", close_id, len(values))
    return "filed"


async def publish_requested(event: Event) -> None:
    await publish(event.org_id, uuid.UUID(str(event.payload["period_close_id"])))


async def exception_decided(event: Event) -> None:
    """An exception to a mismatched item, decided in the inbox: approved, it lets the item
    stand at the next close (for the variance it was asked on); otherwise the item stays
    mismatched. A decision on a filing's manual number moves the filing on."""
    p, org_id = event.payload, event.org_id
    request_id = uuid.UUID(str(p["hitl_request_id"]))
    ref = uuid.UUID(str(p["source_ref"]))
    async with scoped_session(ROLE, org_id) as session:
        done = await session.execute(
            update(pd.PeriodReconciliation)
            .where(
                pd.PeriodReconciliation.id == ref,
                pd.PeriodReconciliation.hitl_request_id == request_id,
                pd.PeriodReconciliation.status == "exception_pending",
            )
            .values(status="exception_approved" if p.get("status") == "approved" else "mismatched")
        )
    if not done.rowcount:
        await publish(org_id, ref)


HANDLERS: dict[str, Handler] = {CLOSE: close_requested, PUBLISH: publish_requested}
