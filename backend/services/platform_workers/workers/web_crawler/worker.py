"""Web Crawler Worker — cron-scheduled fetch of registered `KBSource` rows
(http_file / web_crawl / web_crawl_headless / rss / rest_api / sdmx) into the
same Bronze tier a manual Document Hub / Web Portal upload lands in.

Structurally the same kind of always-on loop as `schema_crawler`: sync
psycopg2, no asyncio in the loop itself. Three trigger paths per tick:
- a per-source cron schedule (no single global interval —
  `libs.shared.cron.cron_due` decides per source, off `KBSourceRun.started_at`);
- the UI's "Run Now": kb_backend inserts a `queued` KBSourceRun row and this
  worker claims queued rows each tick, so it works with Kafka down. The old
  'web-crawl.manual-trigger' topic is still consumed (best-effort) and turned
  into a queued row for any producer that still uses it;
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
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, TypedDict

import psycopg2
from kafka import KafkaConsumer
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from libs.ingestion.connectors.registry import fetch as connector_fetch
from libs.shared.cron import cron_due
from services.platform_workers.common import kafka as kafka_factory
from services.platform_workers.common import pg
from services.platform_workers.common.settings import KafkaSettings, PostgresSettings
from services.platform_workers.workers.web_crawler import bronze, secrets

log = logging.getLogger("platform-workers.web-crawler")

# A failed run retries up to this many times (exponential backoff off
# retry_count), then sits `status='failed'` until its next natural cron tick
# — same terminal behavior V1 always had, just delayed instead of immediate.
MAX_RETRIES = 5
_RAW_BYTES_CONNECTOR_TYPES = {"http_file", "rss", "rest_api", "sdmx"}


def _retry_backoff_seconds(retry_count: int) -> int:
    return 300 * (2 ** min(retry_count, 6))  # 5min, 10min, 20min, ... capped at ~5h


class Settings(BaseSettings):
    """Crawler knobs — mirrors schema_crawler/worker.py's Settings shape."""

    model_config = SettingsConfigDict(extra="ignore")

    manual_trigger_topic: str = Field(default="web-crawl.manual-trigger", validation_alias="KAFKA_WEB_CRAWL_TRIGGER_TOPIC")
    group_id: str = Field(default="web-crawler-worker-group", validation_alias="KAFKA_GROUP_ID")
    poll_interval_seconds: int = Field(default=5, validation_alias="POLL_INTERVAL_SECONDS")
    max_parallel_runs: int = Field(default=3, validation_alias="MAX_PARALLEL_RUNS")
    stale_run_minutes: int = Field(default=30, validation_alias="STALE_RUN_MINUTES")
    kafka_reconnect_interval_seconds: int = Field(
        default=300, validation_alias="KAFKA_RECONNECT_INTERVAL_SECONDS",
    )


_settings = Settings()

MANUAL_TRIGGER_TOPIC = _settings.manual_trigger_topic
GROUP_ID = _settings.group_id
POLL_INTERVAL_SECONDS = _settings.poll_interval_seconds
MAX_PARALLEL_RUNS = max(1, _settings.max_parallel_runs)
STALE_RUN_MINUTES = _settings.stale_run_minutes
KAFKA_RECONNECT_INTERVAL_SECONDS = _settings.kafka_reconnect_interval_seconds


def try_kafka_consumer(retries: int = 5, delay: int = 3) -> KafkaConsumer | None:
    """Best-effort, like schema_crawler's DDL-change consumer — "Run Now" is
    an addition on top of cron scheduling, not a hard dependency."""
    consumer = kafka_factory.consumer(
        [MANUAL_TRIGGER_TOPIC], GROUP_ID,
        auto_offset_reset="latest", settings=KafkaSettings(),
        required=False, attempts=retries, wait_seconds=delay,
    )
    if consumer is None:
        log.warning("Kafka unavailable after %d retries — running in cron-only mode", retries)
    return consumer


# ─── Source registry (KBSource / KBSourceConfig) ───────────────────────────

def get_active_sources(cur: Any) -> list[dict[str, Any]]:
    """Every registered, active-config source this worker can crawl — reads
    the registry kb_backend's `/api/knowledge/sources` API owns, the same
    "second reader" relationship schema_crawler has with KBWarehouse."""
    cur.execute(
        """SELECT s.source_id, s.tenant_id, s.role_id, s.connector_type, s.title,
                  s.created_by, c.config
           FROM "KBSource" s
           JOIN "KBSourceConfig" c ON c.source_id = s.source_id
           WHERE s.is_deleted = false
             AND c.is_active = true AND c.is_deleted = false"""
    )
    return pg.dict_rows(cur)


class _RunInfo(TypedDict):
    started_at: datetime | None
    status: str
    retry_count: int
    next_retry_at: datetime | None


def _latest_run_by_source(cur: Any) -> dict[str, _RunInfo]:
    """Latest KBSourceRun per source (started_at + retry bookkeeping) — one
    query, not N+1, same idea as schema_crawler's _last_crawled_by_warehouse."""
    cur.execute(
        """SELECT DISTINCT ON (source_id) source_id, started_at, status, retry_count, next_retry_at
           FROM "KBSourceRun" ORDER BY source_id, started_at DESC"""
    )
    return {
        str(row[0]): {"started_at": row[1], "status": row[2], "retry_count": row[3], "next_retry_at": row[4]}
        for row in cur.fetchall()
    }


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
    cur: Any, source_id: str, tenant_id: str, links: list[str], depth: int, max_depth: int, discovered_from: str,
) -> None:
    """Feeds a `recursive` web_crawl source's next depth — `ON CONFLICT DO
    NOTHING` on (source_id, url) means a link already seen (at any status) is
    never re-queued, so this is naturally a "visit once" crawl, not a
    re-poll."""
    if depth > max_depth or not links:
        return
    for link in links:
        cur.execute(
            """INSERT INTO "KBSourceCrawlQueue" (queue_id, source_id, tenant_id, url, depth, discovered_from, status)
                   VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                   ON CONFLICT (source_id, url) DO NOTHING""",
            (str(uuid.uuid4()), source_id, tenant_id, link, depth, discovered_from),
        )


def run_source(
    conn: Any, source: dict[str, Any], trigger: str, retry_count: int = 0, run_id: str | None = None,
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
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO "KBSourceRun" (run_id, source_id, status, trigger, retry_count, started_at)
                   VALUES (%s, %s, %s, %s, %s, now())""",
                (run_id, source_id, "running", trigger, retry_count),
            )
        conn.commit()

    fetch_config = config
    if connector_type in ("rest_api", "sdmx") and config.get("openbao_path"):
        secret = secrets.resolve_openbao_secret(config["openbao_path"])
        if secret:
            fetch_config = {**config, "_resolved_secret": secret}

    try:
        artifact = connector_fetch(connector_type, fetch_config)
        outcome: dict[str, Any] = {}
        if connector_type in _RAW_BYTES_CONNECTOR_TYPES:
            data_id = bronze.register_http_file_bronze(
                conn, tenant_id=tenant_id, role_id=role_id, title=title, added_by=added_by, artifact=artifact,
                outcome=outcome,
            )
        else:
            data_id = bronze.register_web_bronze(
                conn, tenant_id=tenant_id, role_id=role_id, title=title, url=config["url"], added_by=added_by,
            )
        quality_metrics = {**(artifact.extra or {}), **outcome} or None

        with conn.cursor() as cur:
            cur.execute(
                """UPDATE "KBSourceRun"
                       SET status = %s, data_ids = %s, quality_metrics = %s, finished_at = now()
                     WHERE run_id = %s""",
                ("success", json.dumps([data_id]), json.dumps(quality_metrics) if quality_metrics else None, run_id),
            )
            if connector_type in ("web_crawl", "web_crawl_headless") and config.get("recursive"):
                _enqueue_discovered_links(
                    cur, source_id, tenant_id, (artifact.extra or {}).get("discovered_links", []),
                    depth=1, max_depth=int(config.get("max_depth", 1)), discovered_from=config["url"],
                )
        conn.commit()
        log.info("run_source | source=%s (%r) connector=%s -> data_id=%s", source_id, title, connector_type, data_id)
    except Exception as e:
        conn.rollback()
        log.exception("run_source | source=%s (%r) connector=%s failed", source_id, title, connector_type)
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE "KBSourceRun"
                       SET status = %s, error = %s, finished_at = now(),
                           next_retry_at = now() + (%s || ' seconds')::interval
                     WHERE run_id = %s""",
                ("failed", str(e)[:2000], _retry_backoff_seconds(retry_count), run_id),
            )
        conn.commit()


def crawl_due_sources(conn: Any, pool: "RunPool | None" = None) -> None:
    """Without a `pool` each due source runs inline (the original behaviour,
    kept for callers/tests that drive one step at a time)."""
    with conn.cursor() as cur:
        sources = get_active_sources(cur)
        latest = _latest_run_by_source(cur)

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
            run_source(conn, source, trigger="scheduled", retry_count=retry_count)
        elif not pool.submit(source, "scheduled", retry_count):
            log.info("crawl_due_sources | no free slot for source=%s, next tick", source["source_id"])


def drain_crawl_queue(conn: Any, batch_size: int = 10) -> None:
    """One tick's worth of a `recursive` web_crawl source's discovered-link
    backlog — same loop slot as crawl_due_sources/drain_manual_triggers, see
    run()."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT q.queue_id, q.source_id, q.tenant_id, q.url, q.depth,
                      s.role_id, s.title, s.created_by, c.config
                 FROM "KBSourceCrawlQueue" q
                 JOIN "KBSource" s ON s.source_id = q.source_id AND s.is_deleted = false
                 JOIN "KBSourceConfig" c ON c.source_id = s.source_id
                                        AND c.is_active = true AND c.is_deleted = false
                WHERE q.status = 'pending'
                ORDER BY q.inserted_at
                LIMIT %s""",
            (batch_size,),
        )
        rows = pg.dict_rows(cur)
    if rows:
        log.info("drain_crawl_queue | %d pending link(s)", len(rows))
    for row in rows:
        _run_queue_item(conn, row)


def _run_queue_item(conn: Any, row: dict[str, Any]) -> None:
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
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO "KBSourceRun" (run_id, source_id, status, trigger, started_at)
               VALUES (%s, %s, 'running', 'recursive', now())""",
            (run_id, source_id),
        )
        cur.execute("""UPDATE "KBSourceCrawlQueue" SET status = 'running' WHERE queue_id = %s""", (queue_id,))
    conn.commit()

    try:
        artifact = connector_fetch("web_crawl", config)
        data_id = bronze.register_web_bronze(
            conn, tenant_id=tenant_id, role_id=row["role_id"], title=title, url=url, added_by=added_by,
        )
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE "KBSourceRun" SET status='success', data_ids=%s, quality_metrics=%s, finished_at=now()
                     WHERE run_id=%s""",
                (json.dumps([data_id]), json.dumps(artifact.extra) if artifact.extra else None, run_id),
            )
            cur.execute("""UPDATE "KBSourceCrawlQueue" SET status = 'done' WHERE queue_id = %s""", (queue_id,))
            if depth < max_depth:
                _enqueue_discovered_links(
                    cur, source_id, tenant_id, (artifact.extra or {}).get("discovered_links", []),
                    depth=depth + 1, max_depth=max_depth, discovered_from=url,
                )
        conn.commit()
        log.info("_run_queue_item | source=%s url=%s depth=%d -> data_id=%s", source_id, url, depth, data_id)
    except Exception as e:
        conn.rollback()
        log.exception("_run_queue_item | source=%s url=%s failed", source_id, url)
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE "KBSourceRun" SET status='failed', error=%s, finished_at=now() WHERE run_id=%s""",
                (str(e)[:2000], run_id),
            )
            cur.execute("""UPDATE "KBSourceCrawlQueue" SET status = 'failed' WHERE queue_id = %s""", (queue_id,))
        conn.commit()


class RunPool:
    """Runs sources concurrently, one DB connection per run (a psycopg2
    connection is not safe to share across threads). A source that is already
    running is refused, so the same source never runs twice at once."""

    def __init__(self, pg_settings: Any, max_workers: int) -> None:
        self._pg_settings = pg_settings
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
        conn = None
        try:
            conn = pg.connect(self._pg_settings)
            run_source(conn, source, trigger, retry_count, run_id)
        except Exception as exc:  # noqa: BLE001 — a crash in one run must not take the pool down
            log.exception("RunPool | source=%s crashed outside run_source", source_id)
            if conn is not None and run_id is not None:
                with contextlib.suppress(Exception):
                    _finish_run(conn, run_id, "failed", f"worker error: {exc}"[:2000])
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()
            with self._lock:
                self._in_flight.discard(source_id)


def _finish_run(conn: Any, run_id: Any, status: str, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE "KBSourceRun" SET status = %s, error = %s, finished_at = now() WHERE run_id = %s""",
            (status, error, str(run_id)),
        )
    conn.commit()


def enqueue_manual_run(conn: Any, source_id: str) -> None:
    """Turns a legacy Kafka trigger into a queued row (none if one is already
    queued or running for that source)."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO "KBSourceRun" (run_id, source_id, status, trigger, retry_count)
               SELECT %s, %s, 'queued', 'manual', 0
                WHERE NOT EXISTS (SELECT 1 FROM "KBSourceRun"
                                   WHERE source_id = %s AND status IN ('queued', 'running'))""",
            (str(uuid.uuid4()), source_id, source_id),
        )
    conn.commit()


def dispatch_queued_runs(conn: Any, pool: RunPool) -> None:
    """Claims queued runs (oldest first) into free pool slots. A run whose
    source is busy simply stays queued until that source finishes."""
    if pool.free_slots() <= 0:
        return
    with conn.cursor() as cur:
        cur.execute(
            """SELECT run_id, source_id, retry_count FROM "KBSourceRun"
                WHERE status = 'queued' ORDER BY started_at LIMIT 50"""
        )
        queued = cur.fetchall()
        sources = {str(s["source_id"]): s for s in get_active_sources(cur)} if queued else {}
    for run_id, source_id, retry_count in queued:
        if pool.free_slots() <= 0:
            break
        source = sources.get(str(source_id))
        if source is None:
            _finish_run(conn, run_id, "failed", "source not found or inactive")
            continue
        if pool.is_running(str(source_id)):
            continue
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE "KBSourceRun" SET status = 'running', started_at = now()
                    WHERE run_id = %s AND status = 'queued'""",
                (str(run_id),),
            )
            claimed = cur.rowcount == 1
        conn.commit()
        if claimed and not pool.submit(source, "manual", retry_count, str(run_id)):
            with conn.cursor() as cur:  # the slot went away between the check and the submit
                cur.execute("""UPDATE "KBSourceRun" SET status = 'queued' WHERE run_id = %s""", (str(run_id),))
            conn.commit()


def reset_stale_runs(conn: Any, minutes: int = STALE_RUN_MINUTES) -> None:
    """A queued/running row this old belongs to a worker that died; without
    this the UI would keep the source locked forever."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE "KBSourceRun"
                  SET status = 'failed', error = 'timed out without finishing (worker lost?)', finished_at = now()
                WHERE status IN ('queued', 'running') AND started_at < now() - (%s || ' minutes')::interval""",
            (str(minutes),),
        )
    conn.commit()


def abandon_running_runs(conn: Any) -> None:
    """At start-up nothing of this process is running, so a 'running' row was
    left by the previous process."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE "KBSourceRun"
                  SET status = 'failed', error = 'interrupted by a worker restart', finished_at = now()
                WHERE status = 'running'"""
        )
    conn.commit()


def drain_manual_triggers(conn: Any, consumer: KafkaConsumer) -> None:
    """Legacy trigger: turn each 'web-crawl.manual-trigger' message into a
    queued run, which dispatch_queued_runs then picks up like any other."""
    records = consumer.poll(timeout_ms=200)
    source_ids: set[str] = set()
    for messages in records.values():
        for message in messages:
            source_id = (message.value or {}).get("source_id")
            if source_id:
                source_ids.add(str(source_id))
    if not source_ids:
        return

    with conn.cursor() as cur:
        known = {str(s["source_id"]) for s in get_active_sources(cur)}
    for source_id in source_ids:
        if source_id in known:
            enqueue_manual_run(conn, source_id)
        else:
            log.warning("drain_manual_triggers | source=%s not found or inactive, skipping", source_id)

    with contextlib.suppress(Exception):
        consumer.commit()


def maybe_reconnect_kafka(
    consumer: KafkaConsumer | None, now: float, last_attempt: float,
) -> tuple[KafkaConsumer | None, float]:
    if consumer is not None or now - last_attempt < KAFKA_RECONNECT_INTERVAL_SECONDS:
        return consumer, last_attempt
    consumer = try_kafka_consumer(retries=1, delay=0)
    if consumer is not None:
        log.info("Kafka consumer recovered — Run Now trigger path restored")
    return consumer, now


def run() -> None:
    from libs.shared.observability import setup_observability

    setup_observability("platform-workers-web-crawler")

    pg_settings = PostgresSettings()
    conn = pg.connect(pg_settings)
    abandon_running_runs(conn)
    pool = RunPool(pg_settings, MAX_PARALLEL_RUNS)
    consumer = try_kafka_consumer()
    last_kafka_attempt = time.time()

    log.info("web-crawler started. kafka=%s parallel=%d", bool(consumer), MAX_PARALLEL_RUNS)
    while True:
        try:
            consumer, last_kafka_attempt = maybe_reconnect_kafka(consumer, time.time(), last_kafka_attempt)

            if consumer is not None:
                try:
                    drain_manual_triggers(conn, consumer)
                except Exception:
                    log.exception("Kafka consumer failed — falling back to cron-only until next retry")
                    with contextlib.suppress(Exception):
                        consumer.close()
                    consumer = None
                    last_kafka_attempt = time.time()

            reset_stale_runs(conn)
            dispatch_queued_runs(conn, pool)
            # No global interval gate — each source tracks its own next-due
            # time off KBSourceRun.started_at (_is_due()), so calling this
            # every tick is cheap and naturally staggers load across sources.
            crawl_due_sources(conn, pool)
            drain_crawl_queue(conn)

            time.sleep(POLL_INTERVAL_SECONDS)
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            log.error("main loop | postgres connection lost, reconnecting: %s", e)
            conn = pg.reconnect(conn, pg_settings)
        except Exception as e:  # noqa: BLE001 — the loop must outlive any single failure
            log.error("main loop error: %s", e)
            time.sleep(5)
