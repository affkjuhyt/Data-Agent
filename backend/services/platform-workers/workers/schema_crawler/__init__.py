"""Schema crawler worker — ``run()`` runs the cron + DDL-event crawl loop."""
from __future__ import annotations


def run() -> None:
    from workers.schema_crawler.worker import run as _run

    _run()
