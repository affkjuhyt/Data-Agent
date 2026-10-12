"""worker-agent: the agent-execution block of ADR-28.

Run: `python -m services.worker_agent.main` with backend/ on PYTHONPATH.

Polls its event source, hands each event to the handler of its type, and acknowledges the
batch only when every handler returned; a failing event is logged and left for the next
claim (handlers are idempotent).

It handles `question.received` from qa.outbox, `hitl.resolved` from inbox.outbox, and
`hitl.requested` from the outbox of every
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
from services.worker_agent import hitl, questions, replay, resolutions
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
            types |= set(questions.HANDLERS) | set(replay.HANDLERS)
        if schema == "inbox":
            # Claiming stamps published_at, so this worker is the one consumer of hitl.resolved
            # until Debezium gives each consumer its own group (ADR-06).
            types |= set(resolutions.HANDLERS)
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


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    stop = asyncio.Event()
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    loop.run_until_complete(run(stop))


if __name__ == "__main__":
    main()
