"""worker-agent: the agent-execution block of ADR-28.

Run: `python -m services.worker_agent.main` with backend/ on PYTHONPATH.

Polls its event source, hands each event to the handler of its type, and acknowledges the
batch only when every handler returned; a failing event is logged and left for the next
claim (handlers are idempotent).

It handles `question.received` from qa.outbox, `hitl.resolved` from inbox.outbox,
`writeback.submitted` from pipeline.outbox, and `hitl.requested` from the outbox of every
schema that has one, in the main database and in the audit database (whose outbox is read
through its own connection key, svc_outbox_relay_audit).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Mapping

from sqlalchemy import text

from libs.dataagent_db import unscoped_session
from services.worker_agent import (
    exports,
    hitl,
    private,
    proposals,
    query_runs,
    questions,
    replay,
    resolutions,
    runs,
    schema_scans,
    source_tests,
    tiles,
    uploads,
)
from services.worker_agent.events import EventSource, Handler, OutboxTableSource

logger = logging.getLogger(__name__)

POLL_SECONDS = float(os.environ.get("WORKER_AGENT_POLL_SECONDS", "0.5"))
BATCH = int(os.environ.get("WORKER_AGENT_BATCH", "10"))


async def run_once(source: EventSource, handlers: Mapping[str, Handler], batch: int = BATCH) -> int:
    """Claim one batch and handle it; returns how many events were handled."""
    async with source.claim(batch) as events:
        for event in events:
            # Within one claim a failure aborts the batch: nothing is acknowledged and the
            # events come back on the next claim, already-handled ones as no-ops.
            await handlers[event.event_type](event)
        return len(events)


HANDLERS: dict[str, Handler] = {
    **questions.HANDLERS,
    **replay.HANDLERS,
    **hitl.HANDLERS,
    **resolutions.HANDLERS,
    **source_tests.HANDLERS,
    **schema_scans.HANDLERS,
    **query_runs.HANDLERS,
    **tiles.HANDLERS,
    **uploads.HANDLERS,
    **proposals.HANDLERS,
    **exports.HANDLERS,
    **runs.HANDLERS,
}
AUDIT_RELAY = "svc_outbox_relay_audit"


async def _outbox_schemas(role: str) -> list[str]:
    """The schemas whose outbox this connection may claim from (its grants decide)."""
    async with unscoped_session(role) as session:
        rows = await session.execute(
            text(
                "SELECT table_schema FROM information_schema.tables "
                "WHERE table_name = 'outbox' ORDER BY table_schema"
            )
        )
        return [r[0] for r in rows]


async def sources() -> list[EventSource]:
    found: list[EventSource] = []
    for schema in await _outbox_schemas("svc_outbox_relay"):
        types = {hitl.EVENT_TYPE}
        if schema == "qa":
            types |= set(questions.HANDLERS) | set(replay.HANDLERS) | set(exports.HANDLERS)
        if schema == "inbox":
            # Claiming stamps published_at, so this worker is the one consumer of hitl.resolved
            # until Debezium gives each consumer its own group (ADR-06).
            types |= set(resolutions.HANDLERS) | {proposals.DECIDED}
        if schema == "catalog":
            types |= set(source_tests.HANDLERS) | set(schema_scans.HANDLERS)
        if schema == "query":
            types |= set(query_runs.HANDLERS)
        if schema == "workspace":
            types |= set(tiles.HANDLERS)
        if schema == "skill":
            types |= {proposals.PUBLISH, proposals.SHARE}
        if schema == "pack":
            types |= {proposals.ACTIVATE}
        if schema == "pipeline":
            types |= {proposals.WRITEBACK}
        if schema == "ingest":
            types |= set(uploads.HANDLERS)
        if schema == "agent":
            # The one consumer of run.status_changed: the run executor (runs.py, ADR-37 stand-in).
            types |= set(runs.HANDLERS)
        found.append(OutboxTableSource(schema=schema, event_types=frozenset(types)))
    try:
        audit = await _outbox_schemas(AUDIT_RELAY)
    except RuntimeError:
        # No URL for the audit database: requests raised by the audit module wait until it is set.
        logger.warning("worker-agent | DATAAGENT_DB_URL__%s is not set", AUDIT_RELAY.upper())
        audit = []
    found += [
        OutboxTableSource(schema=s, event_types=frozenset({hitl.EVENT_TYPE}), role=AUDIT_RELAY)
        for s in audit
    ]
    return found


async def run(stop: asyncio.Event) -> None:
    claimed_from = await sources()
    # UC-16-02's private questions come from Redis, not an outbox (BR-193: never in a table).
    redis = private.redis_from_env()
    private_loop = asyncio.create_task(private.run(redis, stop)) if redis is not None else None
    while not stop.is_set():
        handled = 0
        for source in claimed_from:
            try:
                handled += await run_once(source, HANDLERS)
            except Exception:
                logger.exception("worker-agent | batch failed; retrying after a pause")
        if not handled:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
    if private_loop is not None:
        await private_loop


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    stop = asyncio.Event()
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    loop.run_until_complete(run(stop))


if __name__ == "__main__":
    main()
