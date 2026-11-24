"""crawler entrypoint: claim acquire.run.queued events and work each run (KB-02, 09 §1), and
acquire.schema.test_requested to test a schema version on its stored sample (schema_tests.py), and
acquire.schema.draft_proposed to check and test the schema the model proposed (inference.py, KB-04).

Every run arrives as an event: manual ones (POST /v1/crawl-sources/{id}/runs), scheduled ones
(worker-agent's Temporal schedule for crawl_source.schedule_cron, schedules/targets.py) and
re-extractions after a revert.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Awaitable, Callable

from services.crawler import inference, runs, schema_tests
from services.crawler.events import Event, OutboxTableSource
from services.crawler.storage import RawStore, S3RawStore

POLL_SECONDS = float(os.environ.get("CRAWLER_POLL_SECONDS", "2"))

HANDLERS: dict[str, Callable[[Event, RawStore], Awaitable[None]]] = {
    runs.QUEUED: runs.handle,
    schema_tests.TEST_REQUESTED: schema_tests.handle,
    inference.DRAFT_PROPOSED: inference.handle_draft,
}


async def run(stop: asyncio.Event) -> None:
    source = OutboxTableSource(frozenset(HANDLERS))
    store = S3RawStore()
    log = logging.getLogger(__name__)
    while not stop.is_set():
        handled = 0
        try:
            # One run per claim: a run can take minutes at one request per second.
            async with source.claim(1) as events:
                for event in events:
                    # PL-02: a failed event is recorded and retried later; the next one runs.
                    try:
                        await HANDLERS[event.event_type](event, store)
                    except Exception as error:
                        log.exception("crawler | %s %s failed", event.event_type, event.event_id)
                        events.fail(event.event_id, error)
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
