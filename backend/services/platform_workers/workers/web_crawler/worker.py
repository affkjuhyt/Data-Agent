"""Web Crawler Worker — cron-scheduled fetch of registered `KBSource` rows
(http_file / web_crawl / web_crawl_headless / rss / rest_api / sdmx) into the
same Bronze tier a manual Document Hub / Web Portal upload lands in.

Structurally the same kind of always-on loop as `schema_crawler`: sync
psycopg2, no asyncio in the loop itself. Three trigger paths per tick:
- a per-source cron schedule (no single global interval —
  `libs.shared.cron.cron_due` decides per source, off `KBSourceRun.started_at`);
- a best-effort Kafka 'web-crawl.manual-trigger' topic for the UI's "Run Now"
  button — optional, for the same reason schema_crawler's DDL-change trigger
  is: cron crawling must keep working even if Kafka is down;
- a bounded retry for a recently-failed run (MAX_RETRIES, exponential
  backoff), and `KBSourceCrawlQueue` draining for a `recursive` web_crawl
  source's discovered links (see drain_crawl_queue).

`KBSource`/`KBSourceConfig` are a second reader's worth of data (this worker
doesn't own the registry — kb_backend's `/api/knowledge/sources` API does),
exactly like schema_crawler is a second reader of `KBWarehouse`.
"""
from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid
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
    poll_interval_seconds: int = Field(default=15, validation_alias="POLL_INTERVAL_SECONDS")
    kafka_reconnect_interval_seconds: int = Field(
        default=300, validation_alias="KAFKA_RECONNECT_INTERVAL_SECONDS",
    )


_settings = Settings()

MANUAL_TRIGGER_TOPIC = _settings.manual_trigger_topic
GROUP_ID = _settings.group_id
POLL_INTERVAL_SECONDS = _settings.poll_interval_seconds
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
    """No run yet always counts as due (mirrors schema_crawler's _is_due)."""
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


def run_source(conn: Any, source: dict[str, Any], trigger: str, retry_count: int = 0) -> None:
    """`retry_count` is how many prior retries led to THIS attempt (0 for a
    normal cron/manual dispatch) — each attempt is its own KBSourceRun row
    (never reused across retries), so the row carries the count rather than
    accumulating it via an UPDATE."""
    source_id = str(source["source_id"])
    tenant_id = str(source["tenant_id"])
    role_id = str(source["role_id"])
    connector_type = source["connector_type"]
    config = source.get("config") or {}
    title = source["title"]
    added_by = source.get("created_by") or "web-crawler-worker"

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
        if connector_type in _RAW_BYTES_CONNECTOR_TYPES:
            data_id = bronze.register_http_file_bronze(
                conn, tenant_id=tenant_id, role_id=role_id, title=title, added_by=added_by, artifact=artifact,
            )
        else:
            data_id = bronze.register_web_bronze(
                conn, tenant_id=tenant_id, role_id=role_id, title=title, url=config["url"], added_by=added_by,
            )
        quality_metrics = artifact.extra or None

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


def crawl_due_sources(conn: Any) -> None:
    with conn.cursor() as cur:
        sources = get_active_sources(cur)
        latest = _latest_run_by_source(cur)

    now = datetime.now(UTC)
    due: list[tuple[dict[str, Any], int]] = []
    for s in sources:
        info = latest.get(str(s["source_id"]))
        cron_expr = (s.get("config") or {}).get("cron", "")
        if _is_due(cron_expr, info["started_at"] if info else None, now):
            due.append((s, 0))
        elif _is_retry_due(info, now):
            assert info is not None  # _is_retry_due already ruled out None
            due.append((s, info["retry_count"] + 1))
    if due:
        log.info("crawl_due_sources | %d/%d source(s) due", len(due), len(sources))
    for source, retry_count in due:
        run_source(conn, source, trigger="scheduled", retry_count=retry_count)


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


def drain_manual_triggers(conn: Any, consumer: KafkaConsumer) -> None:
    """Poll pending 'web-crawl.manual-trigger' messages and run each named
    source immediately, ignoring its cron schedule (the UI's "Run Now")."""
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
        sources = {str(s["source_id"]): s for s in get_active_sources(cur)}
    for source_id in source_ids:
        source = sources.get(source_id)
        if source is None:
            log.warning("drain_manual_triggers | source=%s not found or inactive, skipping", source_id)
            continue
        run_source(conn, source, trigger="manual")

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
    consumer = try_kafka_consumer()
    last_kafka_attempt = time.time()

    log.info("web-crawler started. kafka=%s", bool(consumer))
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

            # No global interval gate — each source tracks its own next-due
            # time off KBSourceRun.started_at (_is_due()), so calling this
            # every tick is cheap and naturally staggers load across sources.
            crawl_due_sources(conn)
            drain_crawl_queue(conn)

            time.sleep(POLL_INTERVAL_SECONDS)
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            log.error("main loop | postgres connection lost, reconnecting: %s", e)
            conn = pg.reconnect(conn, pg_settings)
        except Exception as e:  # noqa: BLE001 — the loop must outlive any single failure
            log.error("main loop error: %s", e)
            time.sleep(5)
