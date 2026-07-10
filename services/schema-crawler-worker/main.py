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
from datetime import datetime, timedelta, timezone

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
MAX_PENDING_DDL_EVENTS = int(os.environ.get("MAX_PENDING_DDL_EVENTS", "500"))    # caps cost-abuse via event flooding


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


def _cleanup_missing_tables(
    cur, qdrant: QdrantClient, tenant_id: str, warehouse_id: str, schema_name: str,
    discovered_table_names: set[str], cutoff: datetime,
) -> int:
    """Only called for a full-warehouse crawl — removes SchemaCatalog rows
    (+ their Qdrant point) for tables no longer seen in Snowflake
    INFORMATION_SCHEMA, so the catalog doesn't keep suggesting dropped/
    renamed tables to the agent forever.

    Requires the row to ALSO have been stale before this crawl started
    (last_crawled_at < cutoff, 1.5x CRAWL_INTERVAL_SECONDS ago — see
    crawl_warehouse() for why 1.5x and not 1x) — i.e. missing across two
    consecutive full crawls, not just this one — so a one-off Snowflake-side
    blip (timeout, a table transiently reporting zero columns) doesn't get
    mistaken for a real DROP TABLE and delete a still-valid catalog entry."""
    cur.execute(
        """SELECT id, table_name, qdrant_point_id FROM "SchemaCatalog"
           WHERE tenant_id=%s AND warehouse_id=%s AND schema_name=%s AND last_crawled_at < %s""",
        (tenant_id, warehouse_id, schema_name, cutoff),
    )
    stale = [(r[0], r[1], r[2]) for r in cur.fetchall() if r[1] not in discovered_table_names]
    if not stale:
        return 0

    cur.execute("""DELETE FROM "SchemaCatalog" WHERE id = ANY(%s)""", ([s[0] for s in stale],))
    point_ids = [s[2] for s in stale if s[2]]
    if point_ids:
        qdrant.delete(QDRANT_COLLECTION, points_selector=point_ids)
    for _, table_name, _ in stale:
        log.info(
            "crawl_warehouse | removed stale catalog entry table=%s.%s warehouse=%s (no longer in Snowflake)",
            schema_name, table_name, warehouse_id,
        )
    return len(stale)


def crawl_warehouse(
    conn, qdrant: QdrantClient, tenant_id: str, warehouse_id: str, conn_params: dict,
    table_names: list[str] | None, full_crawl: bool = False,
):
    """Commits once per table (not once for the whole warehouse) — keeps the
    window where a Postgres rollback could leave an already-written Qdrant
    point orphaned (doc §3 / PR review) down to "this one table", instead of
    every table already processed earlier in this warehouse's batch. The
    deterministic point_id (_point_id) still does the actual healing on the
    next crawl that touches this table; this just shrinks how much can be
    orphaned in the meantime.

    full_crawl=True means table_names represents the *complete* set this
    warehouse is configured to track this cycle (cron path: every table, or
    the user's full selected_tables list) — only then is it safe to treat a
    table absent from the result as dropped/renamed rather than merely not
    requested (the single-table DDL-event path must never pass this).

    cutoff uses 1.5x CRAWL_INTERVAL_SECONDS, not 1x: _warehouse_due() only
    fires once *every* row's last_crawled_at is already >= 1x the interval
    old, so at 1x a still-present table that was simply crawled together
    with everything else last cycle would sit almost exactly ON the cutoff
    (off by only the poll/processing jitter between "became due" and "this
    crawl actually ran") — a table missing for just one cycle could get
    deleted immediately instead of getting the two-cycle grace period the
    docstring below promises. 1.5x leaves half an interval of slack, which
    dwarfs realistic jitter (seconds/minutes vs. a half day by default)."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=1.5 * CRAWL_INTERVAL_SECONDS)
    metadata = discover_and_describe(conn_params, table_names)
    changed = 0
    for table_name, meta in metadata.items():
        try:
            with conn.cursor() as cur:
                if process_table(cur, qdrant, tenant_id, warehouse_id, meta):
                    changed += 1
            conn.commit()
        except Exception:
            conn.rollback()
            log.exception("crawl_warehouse | failed table=%s warehouse=%s", table_name, warehouse_id)
    log.info("crawl_warehouse | warehouse=%s: %d/%d table(s) changed", warehouse_id, changed, len(metadata))

    if full_crawl:
        try:
            with conn.cursor() as cur:
                _cleanup_missing_tables(
                    cur, qdrant, tenant_id, warehouse_id, conn_params.get("schema", ""),
                    set(metadata.keys()), cutoff,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            log.exception("crawl_warehouse | cleanup of missing tables failed warehouse=%s", warehouse_id)


def _last_crawled_by_warehouse(cur) -> "dict[tuple[str, str], datetime]":
    """One aggregate query for every (tenant, warehouse)'s most recent
    last_crawled_at, instead of one query per warehouse — crawl_all() now
    runs every POLL_INTERVAL_SECONDS tick (see main()), so an N+1 pattern
    here would mean N extra Postgres round-trips every few seconds for
    every registered warehouse, most of which aren't due yet."""
    cur.execute("""SELECT tenant_id, warehouse_id, MAX(last_crawled_at) FROM "SchemaCatalog" GROUP BY tenant_id, warehouse_id""")
    return {(str(t), str(w)): last for t, w, last in cur.fetchall()}


def _is_due(last_crawled_at: "datetime | None") -> bool:
    """Per-warehouse crawl schedule derived from SchemaCatalog.last_crawled_at
    instead of one global timestamp shared by every tenant — avoids every
    warehouse landing on the exact same 24h boundary and spiking LLM/
    embedding/Snowflake load all at once. No rows yet (first crawl ever, or
    every table failed last time) always counts as due."""
    if last_crawled_at is None:
        return True
    if last_crawled_at.tzinfo is None:
        last_crawled_at = last_crawled_at.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last_crawled_at).total_seconds() >= CRAWL_INTERVAL_SECONDS


def crawl_all(conn, qdrant: QdrantClient, pending_ddl: "dict[tuple, dict] | None" = None):
    """pending_ddl, if given, lets already-due DDL events get processed
    between each warehouse of this cron batch instead of only after the
    whole batch finishes — a warehouse list with many tenants/tables can
    otherwise take long enough to starve the DDL-trigger path of its main
    selling point (fast turnaround) for the whole duration of one cron pass."""
    with conn.cursor() as cur:
        warehouses = get_active_snowflake_warehouses(cur)
        last_crawled = _last_crawled_by_warehouse(cur)
    log.info("crawl_all | %d active Snowflake warehouse(s) registered", len(warehouses))

    for w in warehouses:
        tenant_id, warehouse_id = str(w["tenant_id"]), str(w["warehouse_id"])

        if not _is_due(last_crawled.get((tenant_id, warehouse_id))):
            continue

        conn_params = connection_params(w["config"], warehouse_id)
        if not conn_params:
            continue
        table_names = selected_table_names(w["config"])
        try:
            crawl_warehouse(conn, qdrant, tenant_id, warehouse_id, conn_params, table_names, full_crawl=True)
        except Exception:
            log.exception("crawl_all | warehouse=%s failed, rolling back", w["warehouse_id"])
            conn.rollback()

        if pending_ddl is not None:
            process_due_ddl_events(conn, qdrant, pending_ddl)


def handle_ddl_event(conn, qdrant: QdrantClient, event: dict):
    """doc §2.8 — targeted single-table crawl, called after the debounce
    window in main()'s loop elapses.

    tenant_id is re-derived from KBWarehouse by warehouse_id, NOT trusted
    from the event payload — the event only travels over Kafka, which this
    worker doesn't control the producer side of, so a wrong/spoofed
    tenant_id in the message must not be able to attribute another
    warehouse's schema metadata to the wrong tenant in SchemaCatalog."""
    warehouse_id = event["warehouse_id"]
    table_name = event["table_name"]

    with conn.cursor() as cur:
        cur.execute(
            """SELECT w.tenant_id, c.config FROM "KBWarehouse" w
               JOIN "KBWarehouseConfig" c ON c.warehouse_id = w.warehouse_id
               WHERE w.warehouse_id = %s AND w.is_deleted = false
                 AND c.is_active = true AND c.is_deleted = false""",
            (warehouse_id,),
        )
        row = cur.fetchone()
    if not row:
        log.warning("handle_ddl_event | no active warehouse/config for warehouse=%s, skipping", warehouse_id)
        return

    tenant_id = str(row[0])
    if event.get("tenant_id") and event["tenant_id"] != tenant_id:
        log.warning(
            "handle_ddl_event | event tenant_id=%s does not match warehouse=%s owner tenant_id=%s — "
            "dropping suspicious event", event["tenant_id"], warehouse_id, tenant_id,
        )
        return

    conn_params = connection_params(row[1], warehouse_id)
    if not conn_params:
        return

    crawl_warehouse(conn, qdrant, tenant_id, warehouse_id, conn_params, [table_name])


def _valid_ddl_event(event) -> bool:
    """Schema check before trusting an event enough to even queue it — every
    field must be a non-empty string. Doesn't validate that tenant_id is the
    *correct* owner of warehouse_id (handle_ddl_event() re-derives that from
    KBWarehouse instead of trusting this field at all); this is just
    malformed-message hygiene."""
    if not isinstance(event, dict):
        return False
    return all(isinstance(event.get(f), str) and event.get(f) for f in ("tenant_id", "warehouse_id", "table_name"))


def poll_ddl_events(consumer: KafkaConsumer, pending_ddl: dict[tuple, dict]) -> None:
    """doc §2.8 — one poll cycle: drain whatever's available, (re)stamp each
    table's debounce deadline. A second event for the same table within the
    window overwrites the first's `due` rather than queuing a second
    trigger — that overwrite *is* the debounce.

    Does NOT commit the consumer offset — that only happens once pending_ddl
    is fully drained (see main()'s loop), so a crash between here and the
    table actually being crawled doesn't lose the event."""
    records = consumer.poll(timeout_ms=1000)
    for _tp, msgs in records.items():
        for msg in msgs:
            event = msg.value
            if not _valid_ddl_event(event):
                log.warning("ddl event | malformed, dropping: %r", event)
                continue
            key = (event["tenant_id"], event["warehouse_id"], event["table_name"])
            if key not in pending_ddl and len(pending_ddl) >= MAX_PENDING_DDL_EVENTS:
                log.warning("ddl event | pending_ddl at capacity (%d), dropping: %s", MAX_PENDING_DDL_EVENTS, event)
                continue
            pending_ddl[key] = {"due": time.time() + DDL_DEBOUNCE_SECONDS, "event": event}
            log.info("ddl event received, debouncing %ds: %s", DDL_DEBOUNCE_SECONDS, event)


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

            # No global CRAWL_INTERVAL_SECONDS gate here anymore — each
            # warehouse tracks its own next-due time off SchemaCatalog.
            # last_crawled_at (_is_due()), and crawl_all() skips whatever
            # isn't due yet, so calling it every tick is cheap and
            # naturally staggers load across warehouses instead of every
            # tenant landing on the same 24h boundary at once. pending_ddl
            # is threaded through so already-due DDL events get serviced
            # between warehouses instead of only after the whole batch.
            crawl_all(conn, qdrant, pending_ddl)

            # Only advance the committed Kafka offset once every event
            # polled so far has actually been crawled (pending_ddl fully
            # drained) — committing earlier can lose an event if the worker
            # crashes while it's still sitting in the debounce window (see
            # poll_ddl_events()).
            if consumer is not None and not pending_ddl:
                try:
                    consumer.commit()
                except Exception:
                    log.exception("Kafka offset commit failed")

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
