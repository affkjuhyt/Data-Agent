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
from libs.dataagent_db.models import catalog
from libs.dataagent_db.models import period as pd
from services.worker_agent import storage
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import run_query

logger = logging.getLogger(__name__)

ROLE = "svc_period"
CLOSE = "period.close_requested"
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
    event: Event, close: pd.PeriodClose, dataset: catalog.Dataset, actor: uuid.UUID, source: Any
) -> list[dict[str, Any]] | str:
    """The dataset's rows as of the business date; or why they could not be read."""
    from services.worker_agent.questions import secrets

    table = f"{_ident(dataset.schema_name)}.{_ident(dataset.table_name)}"
    where = ""
    if dataset.business_date_col:
        op = "=" if dataset.history_kind == "snapshot" else "<="
        where = f" WHERE {_ident(dataset.business_date_col)} {op} DATE '{close.business_date.isoformat()}'"
    outcome = await run_query(
        org_id=event.org_id,
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
    event: Event, close: pd.PeriodClose, ledger: dict[str, Any], actor: uuid.UUID
) -> decimal.Decimal | str:
    if "value" in ledger:
        return decimal.Decimal(str(ledger["value"]))
    async with scoped_session("svc_catalog", event.org_id, actor) as session:
        dataset = await session.get(catalog.Dataset, uuid.UUID(str(ledger["dataset_id"])))
        source = await _source(session, dataset, event.org_id) if dataset else None
    if dataset is None or source is None or dataset.data_space_id != close.data_space_id:
        return "the ledger dataset is not readable in this Data Space"
    rows = await _rows(event, close, dataset, actor, source)
    if isinstance(rows, str):
        return rows
    return aggregate(str(ledger.get("aggregate", "sum")), ledger.get("column"), rows)


async def _end(event: Event, close_id: uuid.UUID, status: str, snapshot_hash: str | None) -> None:
    async with scoped_session(ROLE, event.org_id) as session:
        values: dict[str, Any] = {"status": status}
        if status == "closed":
            values |= {"snapshot_hash": snapshot_hash, "closed_at": _now()}
        await session.execute(
            update(pd.PeriodClose)
            .where(pd.PeriodClose.id == close_id, pd.PeriodClose.status == "closing")
            .values(**values)
        )


async def close_requested(event: Event) -> None:
    close_id = uuid.UUID(str(event.payload["period_close_id"]))
    async with scoped_session(ROLE, event.org_id) as session:
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
        await _end(event, close_id, "close_failed", None)
        return
    captured: dict[str, list[dict[str, Any]]] = {}
    hashes: dict[str, str] = {}
    for dataset_id in calendar.dataset_ids:
        async with scoped_session("svc_catalog", event.org_id, actor) as session:
            dataset = await session.get(catalog.Dataset, dataset_id)
            source = await _source(session, dataset, event.org_id) if dataset else None
        if dataset is None or source is None or dataset.data_space_id != close.data_space_id:
            logger.info(
                "worker-agent | period %s: dataset %s is not readable", close_id, dataset_id
            )
            await _end(event, close_id, "close_failed", None)
            return
        rows = await _rows(event, close, dataset, actor, source)
        if isinstance(rows, str):
            logger.info("worker-agent | period %s: %s: %s", close_id, dataset.table_name, rows)
            await _end(event, close_id, "close_failed", None)
            return
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
                logger.info(
                    "worker-agent | period %s: %s changed since its snapshot was written",
                    close_id,
                    dataset.table_name,
                )
                await _end(event, close_id, "close_failed", None)
                return
            continue
        key = (
            f"{event.org_id}/{close.data_space_id}/{close.period_label}/v{close.version}/"
            f"{dataset_id}.jsonl.gz"
        )
        await storage.store().put(BUCKET, key, body, "application/gzip")
        async with scoped_session(ROLE, event.org_id, actor) as session:
            session.add(
                pd.PeriodSnapshotFile(
                    id=uuid7(),
                    org_id=event.org_id,
                    period_close_id=close_id,
                    dataset_id=dataset_id,
                    dataset_version=dataset.current_version,
                    object_key=key,
                    row_count=len(rows),
                    sha256=sha,
                )
            )
    results = []
    for item in calendar.reconciliation_items or []:
        code = str(item.get("code"))
        item_rows = captured.get(str(item.get("dataset_id")))
        tolerance = decimal.Decimal(str(item.get("tolerance", 0)))
        try:
            if item_rows is None:
                raise ValueError("the item's dataset is not in the snapshot")
            snapshot = aggregate(str(item.get("aggregate", "sum")), item.get("column"), item_rows)
            ledger = await _ledger(event, close, dict(item.get("ledger") or {}), actor)
            if isinstance(ledger, str):
                raise ValueError(ledger)
        except (ValueError, KeyError, decimal.InvalidOperation) as exc:
            logger.info(
                "worker-agent | period %s: item %s cannot be reconciled: %s", close_id, code, exc
            )
            await _end(event, close_id, "close_failed", None)
            return
        delta = snapshot - ledger
        status = "matched" if abs(delta) <= tolerance else "mismatched"
        results.append(
            pd.PeriodReconciliation(
                id=uuid7(),
                org_id=event.org_id,
                period_close_id=close_id,
                item_code=code,
                snapshot_value=snapshot,
                ledger_value=ledger,
                tolerance=tolerance,
                delta=delta,
                status=status,
            )
        )
    async with scoped_session(ROLE, event.org_id, actor) as session:
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
    snapshot_hash = hashlib.sha256(
        "\n".join(f"{k}:{v}" for k, v in sorted(hashes.items())).encode()
    ).hexdigest()
    await _end(event, close_id, "close_failed" if mismatched else "closed", snapshot_hash)
    logger.info(
        "worker-agent | period %s %s (%d tables, %d items)",
        close_id,
        "close_failed" if mismatched else "closed",
        len(hashes),
        len(results),
    )


HANDLERS: dict[str, Handler] = {CLOSE: close_requested}
