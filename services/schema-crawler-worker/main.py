"""Schema Crawler Worker (sandbox_schema_docs.docx §II).

Background job, structurally the same kind of always-on loop as
services/memory-consumer/main.py (sync psycopg2 + sync Kafka, no asyncio) —
but instead of reacting to one Kafka topic, it has two trigger paths: a
cron-style interval (CRAWL_INTERVAL_SECONDS) and an optional Kafka
DDL-change topic with a debounce window (doc §2.8). Kafka is best-effort
here, not required: cron crawling must keep working even if Kafka is down,
since DDL-event triggering is explicitly an addition ("Ngoài cron...") on
top of it, not a replacement.

Tenants' Snowflake connections come from the existing KBWarehouse /
KBWarehouseConfig tables (services/kb_backend's warehouse-connect flow) —
this worker is a second reader of that data, not a new connection registry.
"""
import json
import logging
import os
import time
import uuid

import psycopg2
import psycopg2.extras
from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

from schema_hash import schema_hash
from snowflake_metadata import discover_and_describe
from llm_describe import describe_table, render_text_description
from embed import embed

logging.basicConfig(level=logging.INFO, format="%(asctime)s [schema-crawler-worker] %(levelname)s %(message)s")
log = logging.getLogger("schema-crawler-worker")

DB_HOST = os.environ.get("DB_HOST", "postgres")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "dataagent")
DB_USER = os.environ.get("DB_USER", "admin")
DB_PASS = os.environ.get("DB_PASSWORD", "dev123")

QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
QDRANT_COLLECTION = "schema_catalog"
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1024"))

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_SASL_USER = os.environ.get("KAFKA_SASL_USERNAME", "")
KAFKA_SASL_PASS = os.environ.get("KAFKA_SASL_PASSWORD", "")
KAFKA_SECURITY_PROTOCOL = os.environ.get("KAFKA_SECURITY_PROTOCOL", "SASL_PLAINTEXT")
KAFKA_SASL_MECHANISM = os.environ.get("KAFKA_SASL_MECHANISM", "PLAIN")
DDL_TOPIC = os.environ.get("KAFKA_DDL_TOPIC", "ddl.changes")
GROUP_ID = os.environ.get("KAFKA_GROUP_ID", "schema-crawler-worker-group")

CRAWL_INTERVAL_SECONDS = int(os.environ.get("CRAWL_INTERVAL_SECONDS", "86400"))  # doc §2.7 — 24h default
DDL_DEBOUNCE_SECONDS = int(os.environ.get("DDL_DEBOUNCE_SECONDS", "60"))         # doc §2.8
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
KAFKA_RECONNECT_INTERVAL_SECONDS = int(os.environ.get("KAFKA_RECONNECT_INTERVAL_SECONDS", "300"))


def _safe_json(raw: bytes):
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def wait_for_postgres(retries: int = 20, delay: int = 5):
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
            conn.autocommit = False
            log.info("PostgreSQL connected")
            return conn
        except psycopg2.OperationalError:
            log.warning("PostgreSQL not ready, attempt %d/%d, retry in %ds", attempt, retries, delay)
            time.sleep(delay)
    raise RuntimeError("PostgreSQL unavailable after retries")


def _kafka_sasl_kwargs() -> dict:
    if not KAFKA_SASL_USER:
        return {}
    return dict(
        security_protocol=KAFKA_SECURITY_PROTOCOL,
        sasl_mechanism=KAFKA_SASL_MECHANISM,
        sasl_plain_username=KAFKA_SASL_USER,
        sasl_plain_password=KAFKA_SASL_PASS,
    )


def try_kafka_consumer(retries: int = 5, delay: int = 3) -> KafkaConsumer | None:
    """Best-effort — unlike memory-consumer's wait_for_kafka_consumer(), this
    does NOT raise: DDL-event triggering is an addition on top of cron, not
    a hard dependency (doc §2.8 frames it as "Ngoài cron, worker có thể
    được trigger bởi DDL event")."""
    for attempt in range(1, retries + 1):
        try:
            consumer = KafkaConsumer(
                DDL_TOPIC,
                bootstrap_servers=KAFKA_BOOTSTRAP,
                group_id=GROUP_ID,
                auto_offset_reset="latest",
                enable_auto_commit=False,
                value_deserializer=_safe_json,
                consumer_timeout_ms=1000,
                **_kafka_sasl_kwargs(),
            )
            log.info("Kafka consumer ready, subscribed to %s", DDL_TOPIC)
            return consumer
        except NoBrokersAvailable:
            log.warning("Kafka not ready, attempt %d/%d, retry in %ds", attempt, retries, delay)
            time.sleep(delay)
    log.warning("Kafka unavailable after %d retries — running in cron-only mode", retries)
    return None


def ensure_qdrant_collection(qdrant: QdrantClient):
    names = [c.name for c in qdrant.get_collections().collections]
    if QDRANT_COLLECTION not in names:
        qdrant.create_collection(QDRANT_COLLECTION, vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE))
        log.info("qdrant collection '%s' created", QDRANT_COLLECTION)


# ─── Tenant warehouse registry (KBWarehouse / KBWarehouseConfig) ───────────

def get_active_snowflake_warehouses(cur) -> list[dict]:
    """Reads the existing per-tenant warehouse-connection registry — see
    services/kb_backend/router/knowledge/warehouse_connect.py — instead of
    inventing a parallel one for this worker."""
    cur.execute(
        """SELECT w.warehouse_id, w.tenant_id, c.config
           FROM "KBWarehouse" w
           JOIN "KBWarehouseConfig" c ON c.warehouse_id = w.warehouse_id
           WHERE w.service = 'Snowflake' AND w.is_deleted = false
             AND c.is_active = true AND c.is_deleted = false"""
    )
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def connection_params(config: dict, warehouse_id: str) -> dict | None:
    """KBWarehouseConfig.config["connection"] never persists a password (see
    select_warehouse_tables() in warehouse_connect.py) — this platform has
    no secret-store integration for warehouse credentials yet (gap, see
    implementation doc §7). SNOWFLAKE_CRAWLER_PASSWORD is a stand-in: a
    single shared crawler-account password, or per-warehouse via
    SNOWFLAKE_PASSWORD__<warehouse_id>."""
    conn = (config or {}).get("connection") or {}
    if not conn.get("account"):
        return None
    # Env var names can't contain "-", but warehouse_id is typically a UUID.
    env_warehouse_id = warehouse_id.replace("-", "_")
    password = os.environ.get(f"SNOWFLAKE_PASSWORD__{env_warehouse_id}") or os.environ.get("SNOWFLAKE_CRAWLER_PASSWORD")
    if not password:
        log.warning("connection_params | no password configured for warehouse=%s, skipping", warehouse_id)
        return None
    return {
        "account": conn["account"],
        "user": conn.get("username", ""),
        "password": password,
        "warehouse": conn.get("warehouse"),
        "database": conn.get("database"),
        "schema": conn.get("schema"),
        "role": conn.get("role"),
    }


def selected_table_names(config: dict) -> list[str] | None:
    tables = (config or {}).get("selected_tables") or []
    names = [t["name"] for t in tables if t.get("name")]
    return names or None


# ─── Crawl + diff detection (doc §2.7) ──────────────────────────────────────

def _point_id(tenant_id: str, warehouse_id: str, schema_name: str, table_name: str) -> str:
    """Deterministic — re-crawling the same table always upserts the same
    Qdrant point instead of leaving an orphaned old one behind (a
    deliberate simplification vs. the doc's literal "delete old point,
    create new one"; see implementation doc §3)."""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"{tenant_id}|{warehouse_id}|{schema_name}|{table_name}"))


def process_table(cur, qdrant: QdrantClient, tenant_id: str, warehouse_id: str, meta: dict) -> bool:
    """Returns True if the table was (re)described+embedded, False if
    skipped because its schema hasn't changed since the last crawl."""
    schema_name = meta["schema_name"]
    table_name = meta["table_name"]
    new_hash = schema_hash(meta["columns"])

    cur.execute(
        """SELECT id, schema_hash FROM "SchemaCatalog"
           WHERE tenant_id=%s AND warehouse_id=%s AND schema_name=%s AND table_name=%s""",
        (tenant_id, warehouse_id, schema_name, table_name),
    )
    existing = cur.fetchone()

    if existing and existing[1] == new_hash:
        cur.execute(
            """UPDATE "SchemaCatalog" SET last_crawled_at = now() WHERE id = %s""",
            (existing[0],),
        )
        return False

    description = describe_table(table_name, meta["columns"], meta.get("row_count"))
    text_description = render_text_description(table_name, description)
    vector = embed(text_description)
    point_id = _point_id(tenant_id, warehouse_id, schema_name, table_name)

    cur.execute(
        """INSERT INTO "SchemaCatalog"
             (id, tenant_id, warehouse_id, schema_name, table_name, columns, row_count,
              last_altered, text_description, join_hints, schema_hash, qdrant_point_id, last_crawled_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
           ON CONFLICT (tenant_id, warehouse_id, schema_name, table_name)
           DO UPDATE SET columns = EXCLUDED.columns, row_count = EXCLUDED.row_count,
                         last_altered = EXCLUDED.last_altered, text_description = EXCLUDED.text_description,
                         join_hints = EXCLUDED.join_hints, schema_hash = EXCLUDED.schema_hash,
                         qdrant_point_id = EXCLUDED.qdrant_point_id, last_crawled_at = now()""",
        (
            str(uuid.uuid4()), tenant_id, warehouse_id, schema_name, table_name,
            json.dumps(meta["columns"]), meta.get("row_count"), meta.get("last_altered"),
            text_description, json.dumps(description["join_hints"]), new_hash, point_id,
        ),
    )

    qdrant.upsert(QDRANT_COLLECTION, points=[PointStruct(
        id=point_id,
        vector=vector,
        payload={
            "tenant_id": tenant_id,
            "warehouse_id": warehouse_id,
            "schema_name": schema_name,
            "table_name": table_name,
            "text_description": text_description,
            "columns": [c["name"] for c in meta["columns"]],
            "row_count": meta.get("row_count"),
            "join_hints": description["join_hints"],
        },
    )])
    log.info("process_table | crawled tenant=%s table=%s.%s (changed)", tenant_id, schema_name, table_name)
    return True


def crawl_warehouse(cur, qdrant: QdrantClient, tenant_id: str, warehouse_id: str, conn_params: dict, table_names: list[str] | None):
    metadata = discover_and_describe(conn_params, table_names)
    changed = 0
    for table_name, meta in metadata.items():
        try:
            cur.execute("SAVEPOINT table_crawl")
            if process_table(cur, qdrant, tenant_id, warehouse_id, meta):
                changed += 1
            cur.execute("RELEASE SAVEPOINT table_crawl")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT table_crawl")
            log.exception("crawl_warehouse | failed table=%s warehouse=%s", table_name, warehouse_id)
    log.info("crawl_warehouse | warehouse=%s: %d/%d table(s) changed", warehouse_id, changed, len(metadata))


def crawl_all(conn, qdrant: QdrantClient):
    with conn.cursor() as cur:
        warehouses = get_active_snowflake_warehouses(cur)
    log.info("crawl_all | %d active Snowflake warehouse(s) registered", len(warehouses))

    for w in warehouses:
        conn_params = connection_params(w["config"], str(w["warehouse_id"]))
        if not conn_params:
            continue
        table_names = selected_table_names(w["config"])
        try:
            with conn.cursor() as cur:
                crawl_warehouse(cur, qdrant, str(w["tenant_id"]), str(w["warehouse_id"]), conn_params, table_names)
            conn.commit()
        except Exception:
            log.exception("crawl_all | warehouse=%s failed, rolling back", w["warehouse_id"])
            conn.rollback()


def handle_ddl_event(conn, qdrant: QdrantClient, event: dict):
    """doc §2.8 — targeted single-table crawl, called after the debounce
    window in main()'s loop elapses."""
    tenant_id = event["tenant_id"]
    warehouse_id = event["warehouse_id"]
    table_name = event["table_name"]

    with conn.cursor() as cur:
        cur.execute(
            """SELECT c.config FROM "KBWarehouseConfig" c
               WHERE c.warehouse_id = %s AND c.is_active = true AND c.is_deleted = false""",
            (warehouse_id,),
        )
        row = cur.fetchone()
    if not row:
        log.warning("handle_ddl_event | no active config for warehouse=%s, skipping", warehouse_id)
        return

    conn_params = connection_params(row[0], warehouse_id)
    if not conn_params:
        return

    with conn.cursor() as cur:
        crawl_warehouse(cur, qdrant, tenant_id, warehouse_id, conn_params, [table_name])
    conn.commit()


def poll_ddl_events(consumer: KafkaConsumer, pending_ddl: dict[tuple, dict]) -> None:
    """doc §2.8 — one poll cycle: drain whatever's available, (re)stamp each
    table's debounce deadline. A second event for the same table within the
    window overwrites the first's `due` rather than queuing a second
    trigger — that overwrite *is* the debounce."""
    records = consumer.poll(timeout_ms=1000)
    for _tp, msgs in records.items():
        for msg in msgs:
            event = msg.value
            if isinstance(event, dict) and event.get("table_name"):
                key = (event.get("tenant_id"), event.get("warehouse_id"), event.get("table_name"))
                pending_ddl[key] = {"due": time.time() + DDL_DEBOUNCE_SECONDS, "event": event}
                log.info("ddl event received, debouncing %ds: %s", DDL_DEBOUNCE_SECONDS, event)
    if records:
        consumer.commit()


def process_due_ddl_events(conn, qdrant: QdrantClient, pending_ddl: dict[tuple, dict]) -> list[dict]:
    """Pops and crawls every entry whose debounce window has elapsed.
    Returns the events actually processed (for logging/tests)."""
    now = time.time()
    processed = []
    for key in [k for k, v in pending_ddl.items() if v["due"] <= now]:
        event = pending_ddl.pop(key)["event"]
        try:
            handle_ddl_event(conn, qdrant, event)
            processed.append(event)
        except Exception:
            log.exception("ddl event handling failed: %s", event)
            conn.rollback()
    return processed


def maybe_reconnect_kafka(
    consumer: "KafkaConsumer | None", now: float, last_attempt: float
) -> "tuple[KafkaConsumer | None, float]":
    """If the DDL-trigger consumer is down (Kafka was unavailable at boot, or
    died later), retry establishing it every KAFKA_RECONNECT_INTERVAL_SECONDS
    instead of staying cron-only forever — cron is a backstop, not a reason
    to give up on the lower-latency DDL-trigger path once Kafka recovers."""
    if consumer is not None or now - last_attempt < KAFKA_RECONNECT_INTERVAL_SECONDS:
        return consumer, last_attempt
    consumer = try_kafka_consumer(retries=1, delay=0)
    if consumer is not None:
        log.info("Kafka consumer recovered — DDL-trigger path restored")
    return consumer, now


def main():
    conn = wait_for_postgres()
    qdrant = QdrantClient(url=QDRANT_URL)
    ensure_qdrant_collection(qdrant)
    consumer = try_kafka_consumer()

    pending_ddl: dict[tuple, dict] = {}
    last_crawl = 0.0
    last_kafka_attempt = time.time()

    log.info("schema-crawler-worker started. crawl_interval=%ss kafka=%s", CRAWL_INTERVAL_SECONDS, bool(consumer))
    while True:
        try:
            consumer, last_kafka_attempt = maybe_reconnect_kafka(consumer, time.time(), last_kafka_attempt)

            if consumer is not None:
                try:
                    poll_ddl_events(consumer, pending_ddl)
                except Exception:
                    log.exception("Kafka consumer failed — falling back to cron-only until next retry")
                    try:
                        consumer.close()
                    except Exception:
                        pass
                    consumer = None
                    last_kafka_attempt = time.time()

            process_due_ddl_events(conn, qdrant, pending_ddl)

            now = time.time()
            if now - last_crawl >= CRAWL_INTERVAL_SECONDS:
                crawl_all(conn, qdrant)
                last_crawl = now

            time.sleep(POLL_INTERVAL_SECONDS)
        except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
            log.error("main loop | postgres connection lost, reconnecting: %s", e)
            try:
                conn.close()
            except Exception:
                pass
            conn = wait_for_postgres()
        except Exception as e:
            log.error("main loop error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
