"""Replaying a receipt (UC-01-07, BR-177): the `receipt_replay.requested` handler.

The api records the replay `running` and emits the event; this re-runs every query the receipt's
figures came from, with the rights of the person who asked (caller_kind receipt_replay), and
compares figure by figure: is the SQL the one that was compiled (sha256), is the result the same
(sha256), and the value of each figure (F<n> is row n-1 of its query, column `value`, as
answer/narrate.py wrote it).

Outcome: `identical` when every figure matches; `differs` with a count and a cause otherwise;
`not_reproducible` when a query cannot run any more. The replay runs on the source as it is now:
time travel to the answer's knowledge_at needs the history route (dataset history_kind,
period snapshots), which the executor does not take yet, so a source updated since the answer
differs, and the cause says so. `figures_and_text` regenerates the text from the replayed figures
with the same template the answer used.
"""

from __future__ import annotations

import datetime
import decimal
import hashlib
import logging
import uuid
from typing import Any

from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog, qa
from libs.dataagent_db.models import query as q
from services.worker_agent.events import Event, Handler
from services.worker_agent.query.executor import QueryOutcome, run_query
from services.worker_agent.questions import secrets

logger = logging.getLogger(__name__)

EVENT_TYPE = "receipt_replay.requested"
LIVE_ONLY = "Nguồn chỉ được truy vấn trên dữ liệu hiện hành; số khác vì dữ liệu nguồn đã thay đổi từ lúc trả lời."


def _value(outcome: QueryOutcome, figure_key: str) -> decimal.Decimal | None:
    try:
        row = int(figure_key.removeprefix("F")) - 1
        col = [c["name"] for c in outcome.columns].index("value")
        raw = outcome.rows[row][col]
    except (ValueError, IndexError):
        return None
    return None if raw is None else decimal.Decimal(str(raw))


async def replay_receipt(event: Event) -> None:
    replay_id = uuid.UUID(str(event.payload["replay_id"]))
    requested_by = uuid.UUID(str(event.payload["requested_by"]))
    async with scoped_session("svc_qa", event.org_id, requested_by) as session:
        replay = await session.get(qa.ReceiptReplay, replay_id)
        if replay is None or replay.status != "running":
            return
        figures = (
            await session.scalars(
                select(qa.AnswerReceiptFigure)
                .where(qa.AnswerReceiptFigure.receipt_id == replay.receipt_id)
                .order_by(qa.AnswerReceiptFigure.figure_key)
            )
        ).all()
        mode = replay.mode
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
    for run_id, original in originals.items():
        source = sources.get(original.source_id) if original.source_id else None
        if source is None:
            reruns[run_id] = None
            continue
        reruns[run_id] = await run_query(
            org_id=event.org_id,
            data_space_id=original.data_space_id,
            principal_id=requested_by,
            caller_kind="receipt_replay",
            caller_id=replay_id,
            source=source,
            sql=original.sql_generated,
            secrets=secrets(),
            metric_version_ids=list(original.metric_version_ids or ()),
            as_of=original.as_of,
        )

    results: list[dict[str, Any]] = []
    differs, unreproducible = 0, False
    for f in figures:
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
        replayed = _value(rerun, f.figure_key) if f.display_state == "shown" else None
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
                "cause": None if same else LIVE_ONLY,
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
        replay.cause_summary = LIVE_ONLY if outcome == "differs" else None
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
