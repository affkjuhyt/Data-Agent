"""Replaying a receipt (UC-01-07, BR-177): the `receipt_replay.requested` handler.

The api records the replay `running` and emits the event; this re-runs every query the receipt's
figures came from, with the rights of the person who asked (caller_kind receipt_replay), and
compares figure by figure: is the SQL the one that was compiled (sha256), is the result the same
(sha256), and the value of each figure (F<n> is row n-1 of its query, column `value`, as
answer/narrate.py wrote it).

Outcome: `identical` when every figure matches; `differs` with a count and a cause otherwise;
`not_reproducible` when a query cannot run any more.

DF-06: a figure read from workspace datasets is read again at the versions its receipt recorded
(`dataset_versions`), when a dataset has moved on since: from each version's own table while it
stands, else from its Parquet snapshot in DuckDB (query/executor.at_versions, route `history`),
with the asker's policies written in as on the live path. A source outside the workspace is read
as it is now: a source updated since the answer differs, and the cause says so. `figures_and_text` regenerates the text from the replayed figures
with the same template the answer used.

A figure computed from others (source_kind compute_run, answer/insights.py) is not read from a
row: its expression, from the answer and checked against the receipt's expr_sha256, is evaluated
again over the replayed figures.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select, text

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog, qa
from libs.dataagent_db.models import query as q
from services.worker_agent.answer.insights import ExprError, evaluate
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import (
    INTERNAL_WORKSPACE,
    QueryOutcome,
    VersionRead,
    run_query,
)
from services.worker_agent.query.executor import ROLE as QUERY_ROLE
from services.worker_agent.questions import _internal_workspace_source, secrets

logger = logging.getLogger(__name__)

EVENT_TYPE = "receipt_replay.requested"
LIVE_ONLY = "Nguồn chỉ được truy vấn trên dữ liệu hiện hành; số khác vì dữ liệu nguồn đã thay đổi từ lúc trả lời."
AT_VERSION = "Đã đọc lại đúng phiên bản dữ liệu lúc trả lời mà số vẫn khác."


async def versions_at(
    org_id: uuid.UUID, requested_by: uuid.UUID, recorded: list[dict[str, Any]]
) -> list[VersionRead]:
    """Where the rows of each recorded workspace dataset version are now (DF-06): nothing when
    no dataset moved on (the live tables are those versions), else every one of them, with its
    version's table if it still stands and its snapshot if one was taken."""
    wanted = {
        uuid.UUID(str(r["dataset_id"])): int(r["version"])
        for r in recorded
        if r.get("dataset_id") and r.get("version") is not None
    }
    if not wanted:
        return []
    async with scoped_session(QUERY_ROLE, org_id, requested_by) as session:
        datasets = (
            await session.scalars(
                select(catalog.Dataset).where(
                    catalog.Dataset.id.in_(wanted), catalog.Dataset.schema_name == "workspace"
                )
            )
        ).all()
        if not any(d.current_version != wanted[d.id] for d in datasets):
            return []
        dv, ds = catalog.DatasetVersion, catalog.DatasetSnapshot
        reads: list[VersionRead] = []
        for d in datasets:
            version = wanted[d.id]
            physical = await session.scalar(
                select(dv.physical_table).where(dv.dataset_id == d.id, dv.version == version)
            )
            # Before versioning the dataset's own table held the version it was at.
            if physical is None and version == d.current_version:
                physical = d.table_name
            if physical is not None and not await session.scalar(
                text("SELECT to_regclass(format('workspace.%I', CAST(:t AS text))) IS NOT NULL"),
                {"t": physical},
            ):
                physical = None
            snapshot = (
                await session.execute(
                    select(ds.object_key, ds.sha256).where(
                        ds.dataset_id == d.id, ds.version == version, ds.status == "ok"
                    )
                )
            ).first()
            reads.append(
                VersionRead(
                    table=d.table_name,
                    physical=physical,
                    snapshot_key=snapshot[0] if snapshot else None,
                    snapshot_sha256=snapshot[1] if snapshot else None,
                )
            )
    return reads


async def rerun_query(
    *,
    org_id: uuid.UUID,
    requested_by: uuid.UUID,
    replay_id: uuid.UUID,
    original: q.QueryRun,
    source: catalog.Source,
    recorded: list[dict[str, Any]],
) -> tuple[QueryOutcome, bool]:
    """Run a figure's query again as its asker may now, at the versions its receipt recorded
    when it read the workspace; True when it read earlier versions."""
    versions = (
        await versions_at(org_id, requested_by, recorded)
        if source.secret_ref == INTERNAL_WORKSPACE
        else []
    )
    outcome = await run_query(
        org_id=org_id,
        data_space_id=original.data_space_id,
        principal_id=requested_by,
        caller_kind="receipt_replay",
        caller_id=replay_id,
        source=source,
        sql=original.sql_generated,
        secrets=secrets(),
        metric_version_ids=list(original.metric_version_ids or ()),
        as_of=original.as_of,
        versions=versions,
    )
    return outcome, bool(versions)


def _value(
    outcome: QueryOutcome, figure_key: str, cell: tuple[int, str] | None = None
) -> decimal.Decimal | None:
    """The replayed value of a figure, read at the cell its answer named ({row, col}).

    Answers written before figures named their cell read F<n> as row n-1 of `value`; a compared
    answer (R1-06) has two figures a row, so the cell is what counts."""
    try:
        row, name = cell if cell is not None else (int(figure_key.removeprefix("F")) - 1, "value")
        col = [c["name"] for c in outcome.columns].index(name)
        raw = outcome.rows[row][col]
    except (ValueError, IndexError):
        return None
    return None if raw is None else decimal.Decimal(str(raw))


def _cell(source: dict[str, Any]) -> tuple[int, str] | None:
    row, col = source.get("row"), source.get("col")
    return (row, col) if isinstance(row, int) and isinstance(col, str) else None


async def replay_receipt(event: Event) -> None:
    replay_id = uuid.UUID(str(event.payload["replay_id"]))
    requested_by = uuid.UUID(str(event.payload["requested_by"]))
    async with scoped_session("svc_qa", event.org_id, requested_by) as session:
        replay = await session.get(qa.ReceiptReplay, replay_id)
        if replay is None or replay.status != "running":
            return
        figures = sorted(
            (
                await session.scalars(
                    select(qa.AnswerReceiptFigure).where(
                        qa.AnswerReceiptFigure.receipt_id == replay.receipt_id
                    )
                )
            ).all(),
            # F2 before F10, as the receipt lists them (the re-run's values follow this order).
            key=lambda f: [
                (0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"(\d+)", f.figure_key)
            ],
        )
        mode = replay.mode
        receipt = await session.get(qa.AnswerReceipt, replay.receipt_id)
        answer = await session.get(qa.Answer, receipt.answer_id) if receipt else None
        sources_of = {
            str(x.get("key")): x.get("source") or {}
            for x in ((answer.figures if answer else None) or [])
            if isinstance(x, dict)
        }
        exprs = {key: source.get("expr") for key, source in sources_of.items()}
    run_ids = sorted({f.query_run_id for f in figures if f.query_run_id is not None}, key=str)
    async with scoped_session("svc_query", event.org_id, requested_by) as session:
        originals = {
            r.id: r
            for r in (
                await session.scalars(select(q.QueryRun).where(q.QueryRun.id.in_(run_ids)))
            ).all()
        }
    async with scoped_session("svc_qa", event.org_id, requested_by) as session:
        sources = {
            s.id: s
            for s in (
                await session.scalars(
                    select(catalog.Source).where(
                        catalog.Source.id.in_(
                            {r.source_id for r in originals.values() if r.source_id}
                        )
                    )
                )
            ).all()
        }

    reruns: dict[uuid.UUID, QueryOutcome | None] = {}
    historic: set[uuid.UUID] = set()
    for run_id, original in originals.items():
        source = sources.get(original.source_id) if original.source_id else None
        workspace = _internal_workspace_source(original.data_space_id, event.org_id)
        # The workspace is no catalog.source row: one fixed id stands for it (questions.py).
        if source is None and original.source_id == workspace.id:
            source = workspace
        if source is None:
            reruns[run_id] = None
            continue
        recorded = [
            v
            for f in figures
            if f.query_run_id == run_id
            for v in (f.dataset_versions or [])
            if isinstance(v, dict)
        ]
        reruns[run_id], at_version = await rerun_query(
            org_id=event.org_id,
            requested_by=requested_by,
            replay_id=replay_id,
            original=original,
            source=source,
            recorded=recorded,
        )
        if at_version:
            historic.add(run_id)

    results: list[dict[str, Any]] = []
    differs, unreproducible = 0, False
    derived = [f for f in figures if f.source_kind == "compute_run"]
    for f in figures:
        if f.source_kind == "compute_run":
            continue
        first_run = originals.get(f.query_run_id) if f.query_run_id else None
        rerun = reruns.get(f.query_run_id) if f.query_run_id else None
        if first_run is None or rerun is None or not rerun.ok:
            unreproducible = True
            results.append(
                {
                    "figure_key": f.figure_key,
                    "original_value": _str(f.value),
                    "replay_value": None,
                    "sql_sha256_match": False,
                    "result_sha256_match": False,
                    "cause": rerun.error
                    if rerun is not None
                    else "the query is no longer available",
                }
            )
            continue
        # A figure the answer masked (stale, not available) stays masked here: its value is never
        # read back or stored, only the result's hash is compared.
        replayed = (
            _value(rerun, f.figure_key, _cell(sources_of.get(f.figure_key, {})))
            if f.display_state == "shown"
            else None
        )
        sql_match = (
            hashlib.sha256(first_run.sql_generated.encode()).hexdigest() == f.compiled_sql_sha256
        )
        result_match = rerun.result_hash == f.result_sha256
        # A masked figure had no value to compare; it matches when the result does.
        same = result_match if f.value is None else (replayed is not None and replayed == f.value)
        if not same:
            differs += 1
        results.append(
            {
                "figure_key": f.figure_key,
                "original_value": _str(f.value),
                "replay_value": _str(replayed),
                "sql_sha256_match": sql_match,
                "result_sha256_match": result_match,
                "cause": None if same else AT_VERSION if f.query_run_id in historic else LIVE_ONLY,
            }
        )

    replayed_values = {
        r["figure_key"]: decimal.Decimal(r["replay_value"])
        for r in results
        if r["replay_value"] is not None
    }
    inputs_match = all(r["result_sha256_match"] for r in results)
    for f in derived:
        expr = exprs.get(f.figure_key)
        value: decimal.Decimal | None = None
        if expr and hashlib.sha256(expr.encode()).hexdigest() == f.expr_sha256:
            try:
                value = evaluate(expr, replayed_values)
            except (ExprError, ArithmeticError):
                value = None
        if value is None:
            unreproducible = True
        same = value is not None and value == f.value
        if value is not None and not same:
            differs += 1
        results.append(
            {
                "figure_key": f.figure_key,
                "original_value": _str(f.value),
                "replay_value": _str(value),
                "sql_sha256_match": value is not None,
                "result_sha256_match": inputs_match,
                "cause": None
                if same
                else LIVE_ONLY
                if value is not None
                else "the figures it is computed from could not be replayed",
            }
        )

    outcome = "not_reproducible" if unreproducible else "differs" if differs else "identical"
    async with scoped_session("svc_qa", event.org_id, requested_by) as session:
        replay = await session.get(qa.ReceiptReplay, replay_id, with_for_update=True)
        if replay is None or replay.status != "running":
            return
        replay.status = "completed"
        replay.outcome = outcome
        replay.figure_results = results
        replay.differs_count = differs
        replay.cause_summary = (
            next((r["cause"] for r in results if r["cause"]), LIVE_ONLY)
            if outcome == "differs"
            else None
        )
        replay.query_run_ids = [r.query_run_id for r in reruns.values() if r is not None]
        replay.finished_at = datetime.datetime.now(datetime.UTC)
        if mode == "figures_and_text":
            replay.replay_text = "; ".join(
                f"{r['figure_key']}: {r['replay_value'] if r['replay_value'] is not None else 'CHƯA CÓ'}"
                for r in results
            )
            replay.replay_model = "template"  # the answer's own fast-path template; no model call
    logger.info("receipt replay %s: %s (%d differ)", replay_id, outcome, differs)


def _str(value: decimal.Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


HANDLERS: dict[str, Handler] = {EVENT_TYPE: replay_receipt}
