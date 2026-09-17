"""Web Crawler Worker — cron-scheduled fetch of registered `KBSource` rows
(V1 connectors: http_file / web_crawl / web_crawl_headless) into the same
Bronze tier a manual Document Hub / Web Portal upload lands in.

Structurally the same kind of always-on loop as `schema_crawler`: sync
psycopg2, no asyncio in the loop itself. Two trigger paths: a per-source cron
schedule (no single global interval — `libs.shared.cron.cron_due` decides
per source, off `KBSourceRun.started_at`) and a best-effort Kafka
'web-crawl.manual-trigger' topic for the UI's "Run Now" button — Kafka is
optional here for the same reason schema_crawler's DDL-change trigger is:
cron crawling must keep working even if Kafka is down.

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
from typing import Any

import psycopg2
from kafka import KafkaConsumer
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from libs.ingestion.connectors.registry import fetch as connector_fetch
from libs.shared.cron import cron_due
from services.platform_workers.common import kafka as kafka_factory
from services.platform_workers.common import pg
from services.platform_workers.common.settings import KafkaSettings, PostgresSettings
from services.platform_workers.workers.web_crawler import bronze

log = logging.getLogger("platform-workers.web-crawler")


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


def _last_run_by_source(cur: Any) -> dict[str, datetime]:
    """One aggregate query up front, like schema_crawler's
    _last_crawled_by_warehouse — avoids an N+1 per-source query every tick."""
    cur.execute("""SELECT source_id, MAX(started_at) FROM "KBSourceRun" GROUP BY source_id""")
    return {str(sid): started for sid, started in cur.fetchall()}


def _is_due(cron_expr: str, last_run: datetime | None, now: datetime) -> bool:
    """No run yet always counts as due (mirrors schema_crawler's _is_due)."""
    if not cron_expr:
        return False
    if last_run is None:
        return True
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=UTC)
    return cron_due(cron_expr, last_run, now)


def run_source(conn: Any, source: dict[str, Any], trigger: str) -> None:
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
            """INSERT INTO "KBSourceRun" (run_id, source_id, status, trigger, started_at)
               VALUES (%s, %s, %s, %s, now())""",
            (run_id, source_id, "running", trigger),
        )
    conn.commit()

    try:
        artifact = connector_fetch(connector_type, config)
        if connector_type == "http_file":
            data_id = bronze.register_http_file_bronze(
                conn, tenant_id=tenant_id, role_id=role_id, title=title, added_by=added_by, artifact=artifact,
            )
            quality_metrics = None
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
        conn.commit()
        log.info("run_source | source=%s (%r) connector=%s -> data_id=%s", source_id, title, connector_type, data_id)
    except Exception as e:
        conn.rollback()
        log.exception("run_source | source=%s (%r) connector=%s failed", source_id, title, connector_type)
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE "KBSourceRun" SET status = %s, error = %s, finished_at = now() WHERE run_id = %s""",
                ("failed", str(e)[:2000], run_id),
            )
        conn.commit()


def crawl_due_sources(conn: Any) -> None:
    with conn.cursor() as cur:
        sources = get_active_sources(cur)
        last_run = _last_run_by_source(cur)

    now = datetime.now(UTC)
    due = [s for s in sources if _is_due((s.get("config") or {}).get("cron", ""), last_run.get(str(s["source_id"])), now)]
    if due:
        log.info("crawl_due_sources | %d/%d source(s) due", len(due), len(sources))
    for source in due:
        run_source(conn, source, trigger="scheduled")


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

            time.sleep(POLL_INTERVAL_SECONDS)
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            log.error("main loop | postgres connection lost, reconnecting: %s", e)
            conn = pg.reconnect(conn, pg_settings)
        except Exception as e:  # noqa: BLE001 — the loop must outlive any single failure
            log.error("main loop error: %s", e)
            time.sleep(5)
