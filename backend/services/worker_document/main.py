from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from functools import partial

from libs import metrics
from services.worker_document.events import DocumentOutboxSource
from services.worker_document.handler import HANDLERS

SERVICE = "worker-document"


async def run(stop: asyncio.Event) -> None:
    source = DocumentOutboxSource(frozenset(HANDLERS))
    while not stop.is_set():
        handled = 0
        try:
            async with source.claim(int(os.environ.get("WORKER_DOCUMENT_BATCH", "4"))) as events:
                for event in events:
                    await metrics.timed_event(
                        SERVICE, event.event_type, partial(HANDLERS[event.event_type], event)
                    )
                handled = len(events)
        except Exception:
            logging.getLogger(__name__).exception("document worker batch failed")
        metrics.polled(SERVICE)
        if not handled:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    stop.wait(), timeout=float(os.environ.get("WORKER_DOCUMENT_POLL_SECONDS", "1"))
                )


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    # PL-05: /metrics on its own port for Prometheus (deploy/docker/monitoring/prometheus.yml).
    metrics.serve(int(os.environ.get("METRICS_PORT", "0")))
    stop = asyncio.Event()
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    loop.run_until_complete(run(stop))


if __name__ == "__main__":
    main()
