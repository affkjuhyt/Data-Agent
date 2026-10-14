"""worker-ingest entrypoint: claim ingest outbox events and run source materialization."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal

from services.worker_ingest.events import OutboxTableSource
from services.worker_ingest.worker import HANDLERS

POLL_SECONDS = float(os.environ.get("WORKER_INGEST_POLL_SECONDS", "0.5"))


async def run(stop: asyncio.Event) -> None:
    source = OutboxTableSource(frozenset(HANDLERS))
    while not stop.is_set():
        handled = 0
        try:
            async with source.claim(int(os.environ.get("WORKER_INGEST_BATCH", "10"))) as events:
                for event in events:
                    await HANDLERS[event.event_type](event)
                handled = len(events)
        except Exception:
            logging.getLogger(__name__).exception("worker-ingest batch failed")
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
