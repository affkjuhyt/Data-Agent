"""Memory extraction worker — ``run()`` consumes memory.extraction.requested."""
from __future__ import annotations


def run() -> None:
    from services.platform_workers.workers.memory.worker import run as _run

    _run()
