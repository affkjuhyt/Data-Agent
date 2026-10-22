"""One crawl run: queued → fetching → extracting → in_review (or failed), UC-14-03/04/06.

Each step is its own short transaction as svc_acquire in the run's organisation; fetching and the
MinIO writes happen between them, never inside one. A run that is no longer queued when its event
arrives (claimed twice, cancelled) is left alone. A `retry` run re-extracts the stored originals of
the source's latest pages instead of fetching (UC-14-03 step 5); an original past its retention is
skipped, as UC-14-03 E1 says.

Publishing is a person's decision (POST /v1/crawl-runs/{id}:publish): the crawler stops at
in_review. Records with a reason are flagged; the others are pending under the `all` gate and
approved under `exceptions` and `auto`, where only the exceptions and the random sample are read.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import acquire
from services.crawler import extract, fetch
from services.crawler.events import Event
from services.crawler.secrets import Reader, SecretUnavailable
from services.crawler.storage import RawStore, retain_until

log = logging.getLogger(__name__)

ROLE = "svc_acquire"
QUEUED = "acquire.run.queued"
# UC-14-06 E1: a source blocked (403/429) on three runs in a row pauses itself.
MAX_BLOCKED_RUNS = 3
# Sources whose pages hold links worth following; the others are read at their seed URLs only.
FOLLOWS_LINKS = frozenset({"web_listing"})


@dataclass
class Page:
    id: uuid.UUID
    content: bytes
    content_type: str
    fetched_at: datetime.datetime


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _emit(
    session: AsyncSession,
    org_id: uuid.UUID,
    table: str,
    row_id: uuid.UUID,
    kind: str,
    **payload: Any,
) -> None:
    session.add(
        acquire.Outbox(
            id=uuid7(),
            org_id=org_id,
            aggregate_type=table,
            aggregate_id=row_id,
            event_type=kind,
            payload={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in payload.items()},
        )
    )


def _legal(source: acquire.CrawlSource, now: datetime.datetime) -> bool:
    due = source.legal_review_due_at
    return source.legal_status == "approved" and due is not None and due > now


async def _fail(org_id: uuid.UUID, run_id: uuid.UUID, reason: str) -> None:
    log.warning("crawl run %s failed: %s", run_id, reason)
    async with scoped_session(ROLE, org_id) as session:
        run = await session.get(acquire.CrawlRun, run_id, with_for_update=True)
        if run is None or run.status in ("failed", "published"):
            return
        source = await session.get(acquire.CrawlSource, run.source_id)
        run.status, run.finished_at = "failed", _now()
        _emit(
            session,
            org_id,
            "crawl_run",
            run.id,
            "acquire.run.completed",
            run_id=run.id,
            source_id=run.source_id,
            data_space_id=source.data_space_id if source else None,
            status="failed",
            reason=reason,
        )


async def _start(
    org_id: uuid.UUID, run_id: uuid.UUID
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The source and schema as plain values, with the run moved to fetching; None to skip."""
    async with scoped_session(ROLE, org_id) as session:
        run = await session.get(acquire.CrawlRun, run_id, with_for_update=True)
        if run is not None and run.status in ("fetching", "extracting"):
            # The event came back: the worker holding it died mid-run before marking it published.
            # Failing the run frees the source (one running run per source, unique index).
            run.status, run.finished_at = "failed", _now()
            _emit(
                session,
                org_id,
                "crawl_run",
                run.id,
                "acquire.run.completed",
                run_id=run.id,
                source_id=run.source_id,
                status="failed",
                reason="abandoned by a crawler that stopped mid-run",
            )
            return None
        if run is None or run.status != "queued":
            return None
        source = await session.get(acquire.CrawlSource, run.source_id)
        assert source is not None
        version_id = run.schema_version_id or source.active_schema_version_id
        version = await session.get(acquire.CrawlSchemaVersion, version_id) if version_id else None
        if source.status != "active" or not _legal(source, _now()) or version is None:
            why = (
                f"the source is {source.status}"
                if source.status != "active"
                else "the legal review is not in force (BR-174)"
                if not _legal(source, _now())
                else "the source has no schema version"
            )
            run.status, run.finished_at = "failed", _now()
            _emit(
                session,
                org_id,
                "crawl_run",
                run.id,
                "acquire.run.completed",
                run_id=run.id,
                source_id=source.id,
                data_space_id=source.data_space_id,
                status="failed",
                reason=why,
            )
            return None
        run.status, run.started_at, run.schema_version_id = "fetching", _now(), version.id
        return (
            {
                "id": source.id,
                "kind": source.kind,
                "seed_urls": list(source.seed_urls or []),
                "domains": list(source.allowed_domains or []),
                "max_depth": source.max_depth,
                "rate": float(source.rate_limit_rps),
                "auth_mode": source.auth_mode,
                "secret_ref": source.secret_ref,
                "review_gate": source.review_gate,
                "data_space_id": source.data_space_id,
                "trigger": run.trigger,
            },
            {
                "id": version.id,
                "selector": version.record_selector,
                "fields": list(version.fields or []),
            },
        )


async def _headers(org_id: uuid.UUID, source: dict[str, Any], reader: Reader) -> dict[str, str]:
    if source["auth_mode"] != "api_key":
        return {}
    return {"Authorization": f"Bearer {await reader.api_key(org_id, source['secret_ref'])}"}


async def _store_pages(
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    source: dict[str, Any],
    fetched: list[fetch.Fetched],
    store: RawStore,
) -> tuple[list[Page], bool]:
    """crawl_page rows (and originals) for what was fetched; (pages with content, blocked)."""
    now = _now()
    keyed: list[tuple[fetch.Fetched, str, str | None]] = []
    for page in fetched:
        sha = hashlib.sha256(page.content).hexdigest()
        key = None
        if page.content:
            key = f"{org_id}/{source['id']}/{sha}"
            await store.put(key, page.content, page.content_type or "application/octet-stream")
        keyed.append((page, sha, key))
    readable: list[Page] = []
    async with scoped_session(ROLE, org_id) as session:
        run = await session.get(acquire.CrawlRun, run_id, with_for_update=True)
        assert run is not None
        changed = 0
        for page, sha, key in keyed:
            previous = await session.scalar(
                select(acquire.CrawlPage.sha256)
                .where(
                    acquire.CrawlPage.source_id == source["id"], acquire.CrawlPage.url == page.url
                )
                .order_by(acquire.CrawlPage.fetched_at.desc())
                .limit(1)
            )
            is_changed = bool(key) and previous != sha
            changed += is_changed
            row = acquire.CrawlPage(
                id=uuid7(),
                org_id=org_id,
                run_id=run_id,
                source_id=source["id"],
                url=page.url,
                fetched_at=now,
                http_status=page.status,
                content_type=page.content_type,
                sha256=sha,
                object_key=key,
                byte_size=len(page.content) if key else None,
                changed=is_changed,
                retain_until=retain_until(now) if key else None,
            )
            session.add(row)
            if key:
                readable.append(Page(row.id, page.content, page.content_type or "", now))
        run.pages_fetched, run.pages_changed, run.status = len(keyed), changed, "extracting"
    return readable, any(p.status in fetch.BLOCKED for p, _, _ in keyed)


async def _stored_pages(org_id: uuid.UUID, source_id: uuid.UUID, store: RawStore) -> list[Page]:
    """The latest original of each URL of the source still within its retention."""
    now = _now()
    async with scoped_session(ROLE, org_id) as session:
        rows = (
            (
                await session.execute(
                    select(acquire.CrawlPage)
                    .where(
                        acquire.CrawlPage.source_id == source_id,
                        acquire.CrawlPage.object_key.is_not(None),
                    )
                    .order_by(acquire.CrawlPage.url, acquire.CrawlPage.fetched_at.desc())
                    .distinct(acquire.CrawlPage.url)
                )
            )
            .scalars()
            .all()
        )
    pages = []
    for row in rows:
        if row.retain_until is not None and row.retain_until <= now:
            continue
        assert row.object_key is not None
        content = await store.get(row.object_key)
        pages.append(Page(row.id, content, row.content_type or "", row.fetched_at))
    return pages


async def _write_records(
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    source: dict[str, Any],
    schema: dict[str, Any],
    pages: list[Page],
    blocked: bool,
) -> None:
    found: list[tuple[Page, extract.Record]] = []
    unreadable = 0
    for page in pages:
        try:
            found += [
                (page, r)
                for r in extract.extract(
                    page.content, page.content_type, schema["selector"], schema["fields"]
                )
            ]
        except extract.ExtractionError as exc:
            unreadable += 1
            log.info("page %s not extracted: %s", page.id, exc)
    extract.sample([r for _, r in found], seed=str(run_id))
    async with scoped_session(ROLE, org_id) as session:
        run = await session.get(acquire.CrawlRun, run_id, with_for_update=True)
        src = await session.get(acquire.CrawlSource, source["id"], with_for_update=True)
        assert run is not None and src is not None
        now = _now()
        flagged = 0
        for page, record in found:
            if record.review_reasons:
                status = "flagged"
                flagged += 1
            else:
                status = "pending" if source["review_gate"] == "all" else "approved"
            session.add(
                acquire.ExtractedRecord(
                    id=uuid7(),
                    org_id=org_id,
                    run_id=run_id,
                    page_id=page.id,
                    schema_version_id=schema["id"],
                    entity_key=record.entity_key,
                    period=page.fetched_at.date().isoformat(),
                    values=record.values,
                    field_confidence=record.field_confidence,
                    record_confidence=record.record_confidence,
                    status=status,
                    review_reasons=record.review_reasons,
                    is_random_sample=record.review_reasons == ["random_sample"],
                )
            )
        run.records_extracted, run.records_flagged = len(found), flagged
        run.status = "in_review" if pages else "failed"
        run.finished_at = now
        src.last_run_at = now
        src.consecutive_blocked = src.consecutive_blocked + 1 if blocked else 0
        if src.consecutive_blocked >= MAX_BLOCKED_RUNS and src.status == "active":
            src.status = "paused"
            _emit(
                session,
                org_id,
                "crawl_source",
                src.id,
                "acquire.source.status_changed",
                source_id=src.id,
                data_space_id=src.data_space_id,
                previous_status="active",
                status="paused",
                reason="blocked",
            )
        _emit(
            session,
            org_id,
            "crawl_run",
            run.id,
            "acquire.run.completed",
            run_id=run.id,
            source_id=src.id,
            data_space_id=src.data_space_id,
            status=run.status,
            pages_fetched=run.pages_fetched,
            pages_unreadable=unreadable,
            records_extracted=run.records_extracted,
            records_flagged=flagged,
            blocked=blocked,
        )


async def handle(event: Event, store: RawStore, reader: Reader | None = None) -> None:
    org_id = event.org_id
    run_id = uuid.UUID(str(event.payload.get("run_id") or event.aggregate_id))
    started = await _start(org_id, run_id)
    if started is None:
        return
    source, schema = started
    try:
        if source["trigger"] == "retry":
            pages, blocked = await _stored_pages(org_id, source["id"], store), False
            async with scoped_session(ROLE, org_id) as session:
                run = await session.get(acquire.CrawlRun, run_id, with_for_update=True)
                assert run is not None
                run.status = "extracting"
        else:
            headers = await _headers(org_id, source, reader or Reader())
            async with fetch.client() as http:
                fetcher = fetch.Fetcher(
                    http, domains=source["domains"], rate_limit_rps=source["rate"]
                )
                fetched, _refused = await fetch.crawl(
                    fetcher,
                    source["seed_urls"],
                    source["domains"],
                    follow=source["kind"] in FOLLOWS_LINKS,
                    max_depth=source["max_depth"],
                    headers=headers,
                )
            pages, blocked = await _store_pages(org_id, run_id, source, fetched, store)
        await _write_records(org_id, run_id, source, schema, pages, blocked)
    except (SecretUnavailable, DBAPIError) as exc:
        # DBAPIError covers the legal guard on crawl_page (BR-174) firing mid-run.
        await _fail(org_id, run_id, f"{type(exc).__name__}: {str(exc)[:200]}")
    except Exception as exc:
        await _fail(org_id, run_id, f"{type(exc).__name__}: {str(exc)[:200]}")
        raise
