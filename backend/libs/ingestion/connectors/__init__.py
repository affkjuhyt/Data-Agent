"""Shared fetch connectors for scheduled/manual source ingestion.

Deliberately synchronous (plain `httpx.Client` / `playwright.sync_api`), not
`async` — the primary caller is the platform_workers `web-crawler` worker,
which is a sync SQLAlchemy/Kafka loop, not asyncio. Async callers
(kb_backend's Silver stage) bridge in via
`loop.run_in_executor(...)`, the same pattern already used there for the
CPU-bound trafilatura extraction.
"""
