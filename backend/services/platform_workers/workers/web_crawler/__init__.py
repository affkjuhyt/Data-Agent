"""Web crawler worker — ``run()`` runs the cron + manual-trigger crawl loop."""
from __future__ import annotations


def run() -> None:
    from services.platform_workers.workers.web_crawler.worker import run as _run

    _run()
