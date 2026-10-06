"""Web Crawler Worker — cron-scheduled fetch of registered `KBSource` rows
(http_file / web_crawl / web_crawl_headless / rss / rest_api / sdmx) into the
same Bronze tier a manual Document Hub / Web Portal upload lands in.

Structurally the same kind of always-on loop as `schema_crawler`: sync
SQLAlchemy ORM, no asyncio in the loop itself. Three trigger paths per tick:
- a per-source cron schedule (no single global interval —
  `libs.shared.cron.cron_due` decides per source, off `KBSourceRun.started_at`);
- the UI's "Run Now": kb_backend inserts a `queued` KBSourceRun row and this
  worker claims queued rows each tick, so it works with Kafka down;
- a bounded retry for a recently-failed run (MAX_RETRIES, exponential
  backoff), and `KBSourceCrawlQueue` draining for a `recursive` web_crawl
  source's discovered links (see drain_crawl_queue).

Sources run concurrently (MAX_PARALLEL_RUNS, each on its own DB connection);
a source never runs twice at once.

`KBSource`/`KBSourceConfig` are a second reader's worth of data (this worker
doesn't own the registry — kb_backend's `/api/knowledge/sources` API does),
exactly like schema_crawler is a second reader of `KBWarehouse`.
"""
from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any, TypeAlias, TypedDict

from kafka import KafkaProducer
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBSourceConfigORM,
    KBSourceCrawlQueueORM,
    KBSourceORM,
    KBSourceRunORM,
)
from libs.ingestion.connectors.registry import fetch as connector_fetch
from libs.shared import outbox
from libs.shared.cron import cron_due
from libs.shared.storage_ownership import is_v2_structured_source
from services.platform_workers.common import db as worker_db
from services.platform_workers.common import kafka as kafka_factory
from services.platform_workers.common.settings import KafkaSettings, PostgresSettings
from services.platform_workers.workers.web_crawler import bronze, raw, secrets

log = logging.getLogger("platform-workers.web-crawler")

# A failed run retries up to this many times (exponential backoff off
# retry_count), then sits `status='failed'` until its next natural cron tick
# — same terminal behavior V1 always had, just delayed instead of immediate.
MAX_RETRIES = 5
_RAW_BYTES_CONNECTOR_TYPES = {"http_file", "rss", "rest_api", "sdmx"}


def _retry_backoff_seconds(retry_count: int) -> int:
    # `int ** int` is typed Any (a negative exponent gives a float); the exponent here is >= 0.
    multiplier: int = 2 ** min(retry_count, 6)
    return 300 * multiplier  # 5min, 10min, 20min, ... capped at ~5h


class Settings(BaseSettings):
    """Crawler knobs — mirrors schema_crawler/worker.py's Settings shape."""

    model_config = SettingsConfigDict(extra="ignore")

    poll_interval_seconds: int = Field(default=5, validation_alias="POLL_INTERVAL_SECONDS")
    max_parallel_runs: int = Field(default=3, validation_alias="MAX_PARALLEL_RUNS")
    stale_run_minutes: int = Field(default=30, validation_alias="STALE_RUN_MINUTES")


_settings = Settings()
SessionFactory: TypeAlias = sessionmaker[Session]

POLL_INTERVAL_SECONDS = _settings.poll_interval_seconds
MAX_PARALLEL_RUNS = max(1, _settings.max_parallel_runs)
STALE_RUN_MINUTES = _settings.stale_run_minutes

# promotion_mode="auto": queue Silver ourselves after a changed crawl, same
# topic/payload kb_backend's own POST /promote/{id}/silver route publishes
# (the promote_silver worker then chains Gold itself — see libs/ingestion/consumer.py's
# maybe_publish_auto_gold). Hardcoded, not imported from kb_backend: workers
# only import libs.* and their own package, never another service's code
# (see bronze.py's module docstring).
_PROMOTE_SILVER_TOPIC = "kb.promote.silver"
_promote_producer: KafkaProducer | None = None
_promote_producer_lock = threading.Lock()


def _get_promote_producer() -> KafkaProducer | None:
    """One producer shared by every RunPool thread — lazy (a Kafka outage at
    start-up must not block crawling) and best-effort (a publish failure logs
    and moves on; it never fails the crawl that already succeeded)."""
    global _promote_producer
    if _promote_producer is not None:
        return _promote_producer
    with _promote_producer_lock:
        if _promote_producer is None:
            try:
                _promote_producer = kafka_factory.producer(KafkaSettings(), attempts=1, wait_seconds=0)
            except Exception:  # noqa: BLE001 — a down Kafka must not block crawling, only auto-promote
                log.warning("auto-promote: Kafka producer unavailable, will retry on the next eligible run", exc_info=True)
                return None
    return _promote_producer


# Where every fetch's bytes are kept immutably (raw.py). Set by run(); a caller
# driving run_source directly (tests) sets it or runs without the raw layer.
RAW: raw.RawStore | None = None


# Owner tag of this worker's OutboxEvents rows (its relay drains only these).
_OUTBOX_PRODUCER = "web-crawler"


def queue_auto_promote_silver(session: Session, data_id: str, tenant_id: str) -> None:
    """Record "queue Silver for this document" in the SAME transaction that registers its
    Bronze copy and marks the run successful (the caller commits). The publish used to
    happen after the commit and was best-effort: a Kafka outage or a crash in between
    left the document at Bronze, and the "unchanged" dedupe then never re-sent it
    (hit for real on 2026-09-25). Now the event commits with the state it describes and
    `relay_outbox` delivers it, retrying until the broker accepts it."""
    outbox.enqueue_sync_orm(
        session,
        producer=_OUTBOX_PRODUCER,
        topic=_PROMOTE_SILVER_TOPIC,
        value={
            "event": "promote_silver",
            "data_id": data_id,
            "tenant_id": tenant_id,
            "approved_by": "web-crawler-worker",
        },
        key=data_id,
    )


def _publish_outbox_event(topic: str, value: dict[str, Any], key: str | None) -> None:
    """Relay callback: send one event and raise unless the broker acknowledged it, so the
    outbox row stays pending and is retried on the next pass."""
    producer = _get_promote_producer()
    if producer is None:
        raise RuntimeError("Kafka producer unavailable")
    try:
        future = producer.send(topic, value=value, key=key.encode() if key else None)
        # `producer.flush(timeout=...)` does NOT raise on timeout — it just returns once
        # the deadline passes, whether or not the send went through (that silently dropped
        # every auto-promote after a broker restart left this cached producer with stale
        # metadata). `future.get(timeout=...)` blocks on the SAME send and raises the real
        # error.
        future.get(timeout=10)
        producer.flush(timeout=5)
    except Exception:
        # The cached producer may hold a connection to a broker identity that no longer
        # exists (Kafka itself restarted): drop it so the next pass builds a fresh one.
        global _promote_producer
        with _promote_producer_lock:
            _promote_producer = None
        raise


def relay_outbox(SessionLocal: SessionFactory) -> int:
    """Publish this worker's pending outbox events. Called every tick of the main loop."""
    return outbox.drain_sync_orm(SessionLocal, _publish_outbox_event, producer=_OUTBOX_PRODUCER)


def _promotion_mode(source_id: str, config: dict[str, Any]) -> str:
    """A V2 structured source (one with an `extraction_profile`) never
    auto-promotes: its numbers go to series_extractor, not through Silver/Gold
    chunking (docs/v2/00 invariant 4, NOP-163). Stamping "manual" rather than
    only skipping the publish keeps Data Layers from showing the document as
    "auto-processing" forever; a manual promote is then refused by kb_backend
    and by the Silver consumer itself."""
    if is_v2_structured_source(config):
        if config.get("promotion_mode") == "auto":
            log.info("source=%s has an extraction_profile: auto-promote to Silver disabled", source_id)
        return "manual"
    return str(config.get("promotion_mode", "manual"))


# ─── Source registry (KBSource / KBSourceConfig) ───────────────────────────

def get_active_sources(session: Session) -> list[dict[str, Any]]:
    """Every registered, active-config source this worker can crawl — reads
    the registry kb_backend's `/api/knowledge/sources` API owns, the same
    "second reader" relationship schema_crawler has with KBWarehouse."""
    query = (
        select(
            KBSourceORM.source_id,
            KBSourceORM.tenant_id,
            KBSourceORM.role_id,
            KBSourceORM.connector_type,
            KBSourceORM.title,
            KBSourceORM.created_by,
            KBSourceConfigORM.config,
        )
        .join(KBSourceConfigORM, KBSourceConfigORM.source_id == KBSourceORM.source_id)
        .where(
            KBSourceORM.is_deleted.is_(False),
            KBSourceConfigORM.is_active.is_(True),
            KBSourceConfigORM.is_deleted.is_(False),
        )
    )
    # Row-level security shows one tenant's sources at a time.
    sources: list[dict[str, Any]] = []
    for tenant_id in worker_db.tenant_ids(session):
        worker_db.set_tenant(session, tenant_id)
        sources += [dict(row) for row in session.execute(query).mappings()]
    return sources


class _RunInfo(TypedDict):
    started_at: datetime | None
    status: str
    retry_count: int
    next_retry_at: datetime | None


def _latest_run_by_source(session: Session) -> dict[str, _RunInfo]:
    """Latest KBSourceRun per source (started_at + retry bookkeeping) — one
    query, not N+1, same idea as schema_crawler's _last_crawled_by_warehouse."""
    # DISTINCT ON keeps it one row per source in Postgres; selecting whole ORM rows and
    # de-duplicating here loaded every run (JSONB included) on each 5s tick.
    query = (
        select(
            KBSourceRunORM.source_id,
            KBSourceRunORM.started_at,
            KBSourceRunORM.status,
            KBSourceRunORM.retry_count,
            KBSourceRunORM.next_retry_at,
        )
        .distinct(KBSourceRunORM.source_id)
        .order_by(KBSourceRunORM.source_id, KBSourceRunORM.started_at.desc())
    )
    latest: dict[str, _RunInfo] = {}
    # KBSourceRun's row-level policy goes through its source's tenant.
    for tenant_id in worker_db.tenant_ids(session):
        worker_db.set_tenant(session, tenant_id)
        for row in session.execute(query):
            latest[str(row.source_id)] = {
                "started_at": row.started_at,
                "status": row.status,
                "retry_count": row.retry_count,
                "next_retry_at": row.next_retry_at,
            }
    return latest


def _is_due(cron_expr: str, last_run: datetime | None, now: datetime) -> bool:
    """`last_run=None` counts as due; crawl_due_sources never asks about a source
    that has not started yet (its first run is a manual one)."""
    if not cron_expr:
        return False
    if last_run is None:
        return True
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=UTC)
    return cron_due(cron_expr, last_run, now)


def _is_retry_due(latest: _RunInfo | None, now: datetime) -> bool:
    """A failed run gets a bounded number of extra attempts between its
    source's normal cron ticks — see MAX_RETRIES/_retry_backoff_seconds."""
    if latest is None or latest["status"] != "failed" or latest["retry_count"] >= MAX_RETRIES:
        return False
    next_retry_at = latest["next_retry_at"]
    if next_retry_at is None:
        return True
    if next_retry_at.tzinfo is None:
        next_retry_at = next_retry_at.replace(tzinfo=UTC)
    return next_retry_at <= now


def _enqueue_discovered_links(
    session: Session, source_id: str, tenant_id: str, links: list[str], depth: int, max_depth: int, discovered_from: str,
) -> None:
    """Feeds a `recursive` web_crawl source's next depth — `ON CONFLICT DO
    NOTHING` on (source_id, url) means a link already seen (at any status) is
    never re-queued, so this is naturally a "visit once" crawl, not a
    re-poll."""
    if depth > max_depth or not links:
        return
    for link in links:
        stmt = (
            pg_insert(KBSourceCrawlQueueORM)
            .values(
                queue_id=uuid.uuid4(),
                source_id=uuid.UUID(str(source_id)),
                tenant_id=uuid.UUID(str(tenant_id)),
                url=link,
                depth=depth,
                discovered_from=discovered_from,
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=["source_id", "url"])
        )
        session.execute(stmt)


def run_source(
    SessionLocal: SessionFactory, source: dict[str, Any], trigger: str, retry_count: int = 0, run_id: str | None = None,
) -> None:
    """`retry_count` is how many prior retries led to THIS attempt (0 for a
    normal cron/manual dispatch) — each attempt is its own KBSourceRun row
    (never reused across retries), so the row carries the count rather than
    accumulating it via an UPDATE. `run_id` is set when a queued row was
    already claimed (status now 'running'), so no new row is inserted."""
    source_id = str(source["source_id"])
    tenant_id = str(source["tenant_id"])
    role_id = str(source["role_id"])
    connector_type = source["connector_type"]
    config = source.get("config") or {}
    title = source["title"]
    added_by = source.get("created_by") or "web-crawler-worker"

    if run_id is None:
        run_id = str(uuid.uuid4())
        with worker_db.tenant_session(SessionLocal, tenant_id) as session:
            session.add(
                KBSourceRunORM(
                    run_id=uuid.UUID(run_id),
                    source_id=uuid.UUID(source_id),
                    status="running",
                    trigger=trigger,
                    retry_count=retry_count,
                    started_at=datetime.now(UTC),
                )
            )
            session.commit()

    fetch_config = config
    if connector_type in ("rest_api", "sdmx") and config.get("openbao_path"):
        secret = secrets.resolve_openbao_secret(config["openbao_path"])
        if secret:
            fetch_config = {**config, "_resolved_secret": secret}

    promotion_mode = _promotion_mode(source_id, config)
    purpose = config.get("purpose", "production")
    try:
        artifact = connector_fetch(connector_type, fetch_config)
        if RAW is not None:
            RAW.keep(artifact, tenant_id=tenant_id, source_id=source_id, run_id=run_id)
        outcome: dict[str, Any] = {}
        with worker_db.tenant_session(SessionLocal, tenant_id) as session:
            if connector_type in _RAW_BYTES_CONNECTOR_TYPES:
                data_id = bronze.register_http_file_bronze(
                    session, tenant_id=tenant_id, role_id=role_id, title=title, added_by=added_by, artifact=artifact,
                    outcome=outcome, source_id=source_id, promotion_mode=promotion_mode, purpose=purpose,
                )
            else:
                data_id = bronze.register_web_bronze(
                    session, tenant_id=tenant_id, role_id=role_id, title=title, url=config["url"], added_by=added_by,
                    source_id=source_id, promotion_mode=promotion_mode, purpose=purpose,
                    artifact=artifact, outcome=outcome,
                )
        quality_metrics = {**(artifact.extra or {}), **outcome} or None

        with worker_db.tenant_session(SessionLocal, tenant_id) as session:
            run_row = session.get(KBSourceRunORM, uuid.UUID(run_id))
            if run_row is not None:
                run_row.status = "success"
                run_row.data_ids = [data_id]
                run_row.quality_metrics = quality_metrics
                run_row.finished_at = datetime.now(UTC)
            if connector_type in ("web_crawl", "web_crawl_headless") and config.get("recursive"):
                _enqueue_discovered_links(
                    session, source_id, tenant_id, (artifact.extra or {}).get("discovered_links", []),
                    depth=1, max_depth=int(config.get("max_depth", 1)), discovered_from=config["url"],
                )
            # A worker can't click "Process to Silver" — for an auto-promote source
            # with a real change (not the dedup short-circuit), queue it ourselves,
            # atomically with the state it follows.
            if promotion_mode == "auto" and not outcome.get("unchanged"):
                queue_auto_promote_silver(session, data_id, tenant_id)
            session.commit()
        log.info("run_source | source=%s (%r) connector=%s -> data_id=%s", source_id, title, connector_type, data_id)
    except Exception as e:
        log.exception("run_source | source=%s (%r) connector=%s failed", source_id, title, connector_type)
        with worker_db.tenant_session(SessionLocal, tenant_id) as session:
            run_row = session.get(KBSourceRunORM, uuid.UUID(run_id))
            if run_row is not None:
                now = datetime.now(UTC)
                run_row.status = "failed"
                run_row.error = str(e)[:2000]
                run_row.finished_at = now
                run_row.next_retry_at = now + timedelta(seconds=_retry_backoff_seconds(retry_count))
            session.commit()


def crawl_due_sources(SessionLocal: SessionFactory, pool: "RunPool | None" = None) -> None:
    """Without a `pool` each due source runs inline (the original behaviour,
    kept for callers/tests that drive one step at a time)."""
    with SessionLocal() as session:
        sources = get_active_sources(session)
        latest = _latest_run_by_source(session)

    now = datetime.now(UTC)
    due: list[tuple[dict[str, Any], int]] = []
    for s in sources:
        info = latest.get(str(s["source_id"]))
        if info and info["status"] in ("queued", "running"):
            continue  # already in progress; its own run will move it on
        if info is None:
            # A schedule starts with the first run (Run Now), not at creation or
            # import: a bulk import must not fire dozens of crawls by itself.
            continue
        cron_expr = (s.get("config") or {}).get("cron", "")
        if _is_due(cron_expr, info["started_at"], now):
            due.append((s, 0))
        elif _is_retry_due(info, now):
            assert info is not None  # _is_retry_due already ruled out None
            due.append((s, info["retry_count"] + 1))
    if due:
        log.info("crawl_due_sources | %d/%d source(s) due", len(due), len(sources))
    for source, retry_count in due:
        if pool is None:
            run_source(SessionLocal, source, trigger="scheduled", retry_count=retry_count)
        elif not pool.submit(source, "scheduled", retry_count):
            log.info("crawl_due_sources | no free slot for source=%s, next tick", source["source_id"])


def drain_crawl_queue(SessionLocal: SessionFactory, batch_size: int = 10) -> None:
    """One tick's worth of a `recursive` web_crawl source's discovered-link
    backlog — same loop slot as crawl_due_sources, see
    run()."""
    rows: list[dict[str, Any]] = []
    with SessionLocal() as session:
        # Row-level security shows one tenant's queue at a time; the batch is
        # filled tenant by tenant, oldest link first within each.
        for tenant_id in worker_db.tenant_ids(session):
            if len(rows) >= batch_size:
                break
            worker_db.set_tenant(session, tenant_id)
            rows += [
                dict(row)
                for row in session.execute(
                    select(
                        KBSourceCrawlQueueORM.queue_id,
                        KBSourceCrawlQueueORM.source_id,
                        KBSourceCrawlQueueORM.tenant_id,
                        KBSourceCrawlQueueORM.url,
                        KBSourceCrawlQueueORM.depth,
                        KBSourceORM.role_id,
                        KBSourceORM.title,
                        KBSourceORM.created_by,
                        KBSourceConfigORM.config,
                    )
                    .join(KBSourceORM, KBSourceORM.source_id == KBSourceCrawlQueueORM.source_id)
                    .join(KBSourceConfigORM, KBSourceConfigORM.source_id == KBSourceORM.source_id)
                    .where(
                        KBSourceCrawlQueueORM.status == "pending",
                        KBSourceORM.is_deleted.is_(False),
                        KBSourceConfigORM.is_active.is_(True),
                        KBSourceConfigORM.is_deleted.is_(False),
                    )
                    .order_by(KBSourceCrawlQueueORM.inserted_at)
                    .limit(batch_size - len(rows))
                ).mappings()
            ]
    if rows:
        log.info("drain_crawl_queue | %d pending link(s)", len(rows))
    for row in rows:
        _run_queue_item(SessionLocal, row)


def _run_queue_item(SessionLocal: SessionFactory, row: dict[str, Any]) -> None:
    queue_id = str(row["queue_id"])
    source_id = str(row["source_id"])
    tenant_id = str(row["tenant_id"])
    url = row["url"]
    depth = row["depth"]
    config = {**(row.get("config") or {}), "url": url}
    max_depth = int(config.get("max_depth", 1))
    added_by = row.get("created_by") or "web-crawler-worker"
    title = f"{row['title']} — {url}"

    run_id = str(uuid.uuid4())
    with worker_db.tenant_session(SessionLocal, tenant_id) as session:
        session.add(
            KBSourceRunORM(
                run_id=uuid.UUID(run_id),
                source_id=uuid.UUID(source_id),
                status="running",
                trigger="recursive",
                started_at=datetime.now(UTC),
            )
        )
        queue_row = session.get(KBSourceCrawlQueueORM, uuid.UUID(queue_id))
        if queue_row is not None:
            queue_row.status = "running"
        session.commit()

    promotion_mode = _promotion_mode(source_id, config)
    purpose = config.get("purpose", "production")
    try:
        artifact = connector_fetch("web_crawl", config)
        if RAW is not None:
            RAW.keep(artifact, tenant_id=tenant_id, source_id=source_id, run_id=run_id)
        outcome: dict[str, Any] = {}
        with worker_db.tenant_session(SessionLocal, tenant_id) as session:
            data_id = bronze.register_web_bronze(
                session, tenant_id=tenant_id, role_id=str(row["role_id"]), title=title, url=url, added_by=added_by,
                source_id=source_id, promotion_mode=promotion_mode, purpose=purpose,
                artifact=artifact, outcome=outcome,
            )
        queue_metrics = {**(artifact.extra or {}), **outcome} or None
        with worker_db.tenant_session(SessionLocal, tenant_id) as session:
            run_row = session.get(KBSourceRunORM, uuid.UUID(run_id))
            if run_row is not None:
                run_row.status = "success"
                run_row.data_ids = [data_id]
                run_row.quality_metrics = queue_metrics
                run_row.finished_at = datetime.now(UTC)
            queue_row = session.get(KBSourceCrawlQueueORM, uuid.UUID(queue_id))
            if queue_row is not None:
                queue_row.status = "done"
            if depth < max_depth:
                _enqueue_discovered_links(
                    session, source_id, tenant_id, (artifact.extra or {}).get("discovered_links", []),
                    depth=depth + 1, max_depth=max_depth, discovered_from=url,
                )
            if promotion_mode == "auto" and not outcome.get("unchanged"):
                queue_auto_promote_silver(session, data_id, tenant_id)
            session.commit()
        log.info("_run_queue_item | source=%s url=%s depth=%d -> data_id=%s", source_id, url, depth, data_id)
    except Exception as e:
        log.exception("_run_queue_item | source=%s url=%s failed", source_id, url)
        with worker_db.tenant_session(SessionLocal, tenant_id) as session:
            run_row = session.get(KBSourceRunORM, uuid.UUID(run_id))
            if run_row is not None:
                run_row.status = "failed"
                run_row.error = str(e)[:2000]
                run_row.finished_at = datetime.now(UTC)
            queue_row = session.get(KBSourceCrawlQueueORM, uuid.UUID(queue_id))
            if queue_row is not None:
                queue_row.status = "failed"
            session.commit()


class RunPool:
    """Runs sources concurrently, with each thread opening its own ORM session.
    A source that is already running is refused, so the same source never runs
    twice at once."""

    def __init__(self, SessionLocal: SessionFactory, max_workers: int) -> None:
        self._SessionLocal = SessionLocal
        self._max = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="crawl")
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()

    def is_running(self, source_id: str) -> bool:
        with self._lock:
            return source_id in self._in_flight

    def free_slots(self) -> int:
        with self._lock:
            return self._max - len(self._in_flight)

    def submit(self, source: dict[str, Any], trigger: str, retry_count: int = 0, run_id: str | None = None) -> bool:
        source_id = str(source["source_id"])
        with self._lock:
            if source_id in self._in_flight or len(self._in_flight) >= self._max:
                return False
            self._in_flight.add(source_id)
        self._executor.submit(self._task, source, trigger, retry_count, run_id)
        return True

    def _task(self, source: dict[str, Any], trigger: str, retry_count: int, run_id: str | None) -> None:
        source_id = str(source["source_id"])
        try:
            run_source(self._SessionLocal, source, trigger, retry_count, run_id)
        except Exception as exc:  # noqa: BLE001 — a crash in one run must not take the pool down
            log.exception("RunPool | source=%s crashed outside run_source", source_id)
            if run_id is not None:
                with contextlib.suppress(Exception):
                    _finish_run(
                        self._SessionLocal, source["tenant_id"], run_id, "failed", f"worker error: {exc}"[:2000]
                    )
        finally:
            with self._lock:
                self._in_flight.discard(source_id)


def _finish_run(SessionLocal: SessionFactory, tenant_id: Any, run_id: Any, status: str, error: str) -> None:
    with worker_db.tenant_session(SessionLocal, tenant_id) as session:
        run_row = session.get(KBSourceRunORM, uuid.UUID(str(run_id)))
        if run_row is not None:
            run_row.status = status
            run_row.error = error
            run_row.finished_at = datetime.now(UTC)
        session.commit()


def dispatch_queued_runs(SessionLocal: SessionFactory, pool: RunPool) -> None:
    """Claims queued runs (oldest first) into free pool slots. A run whose
    source is busy simply stays queued until that source finishes."""
    if pool.free_slots() <= 0:
        return
    queued: list[tuple[Any, Any, int, str]] = []
    with SessionLocal() as session:
        # One tenant at a time (row-level security), oldest first within each.
        for tenant_id in worker_db.tenant_ids(session):
            worker_db.set_tenant(session, tenant_id)
            queued += [
                (run_id, source_id, retry_count, tenant_id)
                for run_id, source_id, retry_count in session.execute(
                    select(KBSourceRunORM.run_id, KBSourceRunORM.source_id, KBSourceRunORM.retry_count)
                    .where(KBSourceRunORM.status == "queued")
                    .order_by(KBSourceRunORM.started_at)
                    .limit(50)
                ).all()
            ]
        sources = {str(s["source_id"]): s for s in get_active_sources(session)} if queued else {}
    for run_id, source_id, retry_count, tenant_id in queued:
        if pool.free_slots() <= 0:
            break
        source = sources.get(str(source_id))
        if source is None:
            _finish_run(SessionLocal, tenant_id, run_id, "failed", "source not found or inactive")
            continue
        if pool.is_running(str(source_id)):
            continue
        with worker_db.tenant_session(SessionLocal, tenant_id) as session:
            result = session.execute(
                update(KBSourceRunORM)
                .where(KBSourceRunORM.run_id == run_id, KBSourceRunORM.status == "queued")
                .values(status="running", started_at=datetime.now(UTC))
            )
            claimed = result.rowcount == 1
            session.commit()
        if claimed and not pool.submit(source, "manual", retry_count, str(run_id)):
            # the slot went away between the check and the submit
            with worker_db.tenant_session(SessionLocal, tenant_id) as session:
                session.execute(
                    update(KBSourceRunORM)
                    .where(KBSourceRunORM.run_id == run_id)
                    .values(status="queued")
                )
                session.commit()


def reset_stale_runs(SessionLocal: SessionFactory, minutes: int = STALE_RUN_MINUTES) -> None:
    """A queued/running row this old belongs to a worker that died; without
    this the UI would keep the source locked forever."""
    cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
    _update_every_tenant(
        SessionLocal,
        update(KBSourceRunORM)
        .where(KBSourceRunORM.status.in_(("queued", "running")), KBSourceRunORM.started_at < cutoff)
        .values(
            status="failed",
            error="timed out without finishing (worker lost?)",
            finished_at=datetime.now(UTC),
        ),
    )


def abandon_running_runs(SessionLocal: SessionFactory) -> None:
    """At start-up nothing of this process is running, so a 'running' row was
    left by the previous process."""
    _update_every_tenant(
        SessionLocal,
        update(KBSourceRunORM)
        .where(KBSourceRunORM.status == "running")
        .values(
            status="failed",
            error="interrupted by a worker restart",
            finished_at=datetime.now(UTC),
        ),
    )


def _update_every_tenant(SessionLocal: SessionFactory, stmt: Any) -> None:
    """Run one UPDATE under each tenant in turn: row-level security lets it see
    one tenant's rows at a time. One transaction, committed at the end."""
    with SessionLocal() as session:
        for tenant_id in worker_db.tenant_ids(session):
            worker_db.set_tenant(session, tenant_id)
            session.execute(stmt)
        session.commit()


def run() -> None:
    from libs.shared.observability import setup_observability

    setup_observability("platform-workers-web-crawler")

    SessionLocal = worker_db.make_session_factory(PostgresSettings())
    global RAW
    RAW = raw.RawStore(
        objects=bronze._minio_client(),
        # crawler_svc (ADR-V2-46): INSERT/SELECT on ingest.artifact and ingest.fetch only.
        ingest_sessions=worker_db.make_session_factory(
            PostgresSettings(user=os.environ["INGEST_DB_USER"], password=os.environ["INGEST_DB_PASSWORD"])
        ),
    )
    abandon_running_runs(SessionLocal)
    pool = RunPool(SessionLocal, MAX_PARALLEL_RUNS)

    log.info("web-crawler started. parallel=%d", MAX_PARALLEL_RUNS)
    while True:
        try:
            reset_stale_runs(SessionLocal)
            dispatch_queued_runs(SessionLocal, pool)
            # No global interval gate — each source tracks its own next-due
            # time off KBSourceRun.started_at (_is_due()), so calling this
            # every tick is cheap and naturally staggers load across sources.
            crawl_due_sources(SessionLocal, pool)
            drain_crawl_queue(SessionLocal)
            relay_outbox(SessionLocal)

            time.sleep(POLL_INTERVAL_SECONDS)
        except Exception as e:  # noqa: BLE001 — the loop must outlive any single failure
            log.error("main loop error: %s", e)
            time.sleep(5)
