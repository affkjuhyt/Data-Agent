"""crawler entrypoint: claim acquire.run.queued events and work each run (KB-02, 09 §1).

Runs created by a schedule (crawl_source.schedule_cron) are not made here yet: no V3 component
schedules across organisations, and that is the Temporal Schedules work of ADR-33. Manual runs
(POST /v1/crawl-sources/{id}/runs) and re-extractions after a revert arrive as events.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal

from services.crawler import runs
from services.crawler.events import OutboxTableSource
from services.crawler.storage import S3RawStore

POLL_SECONDS = float(os.environ.get("CRAWLER_POLL_SECONDS", "2"))


async def run(stop: asyncio.Event) -> None:
    source = OutboxTableSource(frozenset({runs.QUEUED}))
    store = S3RawStore()
    log = logging.getLogger(__name__)
    while not stop.is_set():
        handled = 0
        try:
            # One run per claim: a run can take minutes at one request per second.
            async with source.claim(1) as events:
                for event in events:
                    await runs.handle(event, store)
                handled = len(events)
        except Exception:
            log.exception("crawler batch failed")
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
