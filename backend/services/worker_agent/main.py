"""worker-agent: the agent-execution block of ADR-28.

Run: `python -m services.worker_agent.main` with backend/ on PYTHONPATH.

Polls its event source, hands each event to the handler of its type, and acknowledges the
batch only when every handler returned; a failing event is logged and left for the next
claim (handlers are idempotent). G1 handles `question.received` only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Mapping

from services.worker_agent.events import EventSource, Handler, OutboxTableSource
from services.worker_agent.questions import HANDLERS

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


async def run(stop: asyncio.Event) -> None:
    source = OutboxTableSource(schema="qa", event_types=frozenset(HANDLERS))
    while not stop.is_set():
        try:
            handled = await run_once(source, HANDLERS)
        except Exception:
            logger.exception("worker-agent | batch failed; retrying after a pause")
            handled = 0
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
