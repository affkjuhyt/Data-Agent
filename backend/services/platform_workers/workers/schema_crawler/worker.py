"""Schema Crawler Worker (sandbox_schema_docs.docx §II) — ex schema-crawler-worker.

Background job, structurally the same kind of always-on loop as the memory
worker (sync SQLAlchemy + sync Kafka, no asyncio) — but instead of reacting to
one Kafka topic, it has two trigger paths: a cron-style interval
(CRAWL_INTERVAL_SECONDS) and an optional Kafka DDL-change topic with a
debounce window (doc §2.8). Kafka is best-effort here, not required: cron
crawling must keep working even if Kafka is down, since DDL-event triggering
is explicitly an addition ("Ngoài cron...") on top of it, not a replacement.

Tenants' warehouse connections come from the existing KBWarehouse /
KBWarehouseConfig tables (backend/services/kb_backend's warehouse-connect flow) —
this worker is a second reader of that data, not a new connection registry.
"""
from __future__ import annotations

import contextlib
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from services.platform_workers.common import kafka as kafka_factory
from services.platform_workers.common import db as worker_db
from services.platform_workers.common import qdrant as qdrant_factory
from services.platform_workers.common.settings import KafkaSettings, PostgresSettings, QdrantSettings
from kafka import KafkaConsumer
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBSourceCrawlQueueORM,
    KBWarehouseConfigORM,
    KBWarehouseORM,
)
from libs.basemodel.services_databaseconnector.postgres_orm.workflow_orm import SchemaCatalogORM

from services.platform_workers.workers.schema_crawler import governance
from services.platform_workers.workers.schema_crawler import metadata as metadata_dispatch
from services.platform_workers.workers.schema_crawler.embed import embed
from services.platform_workers.workers.schema_crawler.llm_describe import describe_table, render_text_description
from services.platform_workers.workers.schema_crawler.schema_hash import schema_hash

log = logging.getLogger("platform-workers.schema-crawler")


class Settings(BaseSettings):
    """Crawler knobs — same env names the schema-crawler-worker container used."""

    model_config = SettingsConfigDict(extra="ignore")

    ddl_topic: str = Field(default="ddl.changes", validation_alias="KAFKA_DDL_TOPIC")
    group_id: str = Field(default="schema-crawler-worker-group", validation_alias="KAFKA_GROUP_ID")
    crawl_interval_seconds: int = Field(default=86400, validation_alias="CRAWL_INTERVAL_SECONDS")  # doc §2.7 — 24h
    ddl_debounce_seconds: int = Field(default=60, validation_alias="DDL_DEBOUNCE_SECONDS")          # doc §2.8
    poll_interval_seconds: int = Field(default=5, validation_alias="POLL_INTERVAL_SECONDS")
    kafka_reconnect_interval_seconds: int = Field(
        default=300, validation_alias="KAFKA_RECONNECT_INTERVAL_SECONDS",
    )
    # caps cost-abuse via event flooding
    max_pending_ddl_events: int = Field(default=500, validation_alias="MAX_PENDING_DDL_EVENTS")


_settings = Settings()
_qdrant_settings = QdrantSettings()

QDRANT_URL = _qdrant_settings.url
QDRANT_COLLECTION = "schema_catalog"
EMBEDDING_DIM = _qdrant_settings.embedding_dim

DDL_TOPIC = _settings.ddl_topic
GROUP_ID = _settings.group_id

CRAWL_INTERVAL_SECONDS = _settings.crawl_interval_seconds
DDL_DEBOUNCE_SECONDS = _settings.ddl_debounce_seconds
POLL_INTERVAL_SECONDS = _settings.poll_interval_seconds
KAFKA_RECONNECT_INTERVAL_SECONDS = _settings.kafka_reconnect_interval_seconds
MAX_PENDING_DDL_EVENTS = _settings.max_pending_ddl_events


def try_kafka_consumer(retries: int = 5, delay: int = 3) -> KafkaConsumer | None:
    """Best-effort — unlike the memory worker's consumer, this does NOT raise:
    DDL-event triggering is an addition on top of cron, not a hard dependency
    (doc §2.8 frames it as "Ngoài cron, worker có thể được trigger bởi DDL
    event")."""
    consumer = kafka_factory.consumer(
        [DDL_TOPIC], GROUP_ID,
        auto_offset_reset="latest", settings=KafkaSettings(),
        required=False, attempts=retries, wait_seconds=delay,
    )
    if consumer is None:
        log.warning("Kafka unavailable after %d retries — running in cron-only mode", retries)
    return consumer


def ensure_qdrant_collection(qdrant: QdrantClient) -> None:
    qdrant_factory.ensure_collection(qdrant, QDRANT_COLLECTION, EMBEDDING_DIM)


# ─── Tenant warehouse registry (KBWarehouse / KBWarehouseConfig) ───────────

def get_active_warehouses(session: Session) -> list[dict[str, Any]]:
    """Every registered warehouse this worker can crawl, tagged with its engine.

    Reads the existing per-tenant warehouse-connection registry — see
    backend/services/kb_backend/router/knowledge/warehouse_connect.py — instead of
    inventing a parallel one for this worker.

    The filter used to be `w.service = 'Snowflake'`, which is why the Schema
    Catalog described one set of databases while the DB Connector node queried
    another. `service` is a free string from the connect UI, so the supported
    set is matched after normalisation rather than in SQL; an unrecognised
    value is skipped with a warning, because crawling a warehouse with the
    wrong driver puts confident nonsense in the catalog.
    """
    rows = session.execute(
        select(
            KBWarehouseORM.warehouse_id,
            KBWarehouseORM.tenant_id,
            KBWarehouseORM.service,
            KBWarehouseConfigORM.config,
        )
        .join(KBWarehouseConfigORM, KBWarehouseConfigORM.warehouse_id == KBWarehouseORM.warehouse_id)
        .where(
            KBWarehouseORM.is_deleted.is_(False),
            KBWarehouseConfigORM.is_active.is_(True),
            KBWarehouseConfigORM.is_deleted.is_(False),
        )
    ).mappings().all()

    supported: list[dict[str, Any]] = []
    for row in rows:
        engine = metadata_dispatch.normalize_engine(row.get("service"))
        if engine is None:
            log.warning(
                "get_active_warehouses | warehouse=%s has unsupported service=%r, skipping",
                row.get("warehouse_id"), row.get("service"),
            )
            continue
        supported.append({**row, "engine": engine})
    return supported


def selected_table_names(config: dict[str, Any]) -> list[str] | None:
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


def _iso(value: Any) -> str | None:
    """Timestamps go into the Qdrant payload as ISO strings — the payload is
    JSON, and a datetime is not."""
    return value.isoformat() if isinstance(value, datetime) else None


def _governance_payload(policy: dict[str, Any], watermark: Any, checked_at: Any) -> dict[str, Any]:
    """The governance half of a Qdrant payload.

    Carried in the payload rather than left in Postgres alone because
    retrieve_schema_catalog() returns payloads straight to the SQL-generation
    prompt (doc §2.5) — a table whose data stops at last Tuesday is something
    the model should see while choosing a date filter, not only something the
    answer discloses afterwards.
    """
    return {
        "owner": policy.get("owner") or "",
        "classification": policy.get("classification") or "",
        "upstream": policy.get("upstream") or [],
        "freshness_column": policy.get("freshness_column") or "",
        "data_as_of": _iso(watermark),
        "freshness_checked_at": _iso(checked_at) if watermark is not None else None,
        "freshness_sla_seconds": policy.get("freshness_sla_seconds"),
    }


def resolve_freshness_column(declared: str, columns: list[dict[str, Any]], table_name: str) -> str:
    """The warehouse's own spelling of the operator's declared freshness column.

    Matched case-insensitively against the columns just discovered, and returned
    in the spelling the warehouse reports: Snowflake upper-cases what an
    operator wrote as `created_at`, and Postgres does not.

    A declared column that does not exist returns "" and logs. Catching it here
    costs one dictionary lookup; not catching it means a failing MAX() query
    against that table on every crawl, forever, with the table silently reported
    as freshness-unknown either way.
    """
    if not declared:
        return ""
    by_lower = {str(c.get("name", "")).lower(): str(c.get("name", "")) for c in columns}
    actual = by_lower.get(declared.strip().lower())
    if not actual:
        log.warning(
            "process_table | declared freshness_column=%r does not exist on %s — "
            "freshness will stay unknown for this table", declared, table_name,
        )
        return ""
    return actual


def _governance_values(policy: dict[str, Any], watermark: Any, checked_at: Any) -> dict[str, Any]:
    """Row values for the seven governance columns, in a fixed order shared by
    both write paths below."""
    return {
        "owner": policy.get("owner") or None,
        "classification": policy.get("classification") or None,
        "upstream": policy.get("upstream") or [],
        "freshness_column": policy.get("freshness_column") or None,
        "data_as_of": watermark,
        # Only stamped when a watermark was actually attempted and returned;
        # a checked_at without a value would read as "measured, and it's null".
        "freshness_checked_at": checked_at if watermark is not None else None,
        "freshness_sla_seconds": policy.get("freshness_sla_seconds"),
    }


def process_table(
    session: Session, qdrant: QdrantClient, tenant_id: str, warehouse_id: str, meta: dict[str, Any],
    policy: dict[str, Any] | None = None, watermark: Any = None, checked_at: Any = None,
) -> bool:
    """Returns True if the table was (re)described+embedded, False if
    skipped because its schema hasn't changed since the last crawl.

    Governance fields are written on BOTH paths. The schema-unchanged path is
    the normal one — a table's DDL is stable for months — and freshness moves
    every time the pipeline runs, so writing the watermark only when the schema
    changed would leave `data_as_of` frozen at whenever the table was last
    altered. That is the exact failure this field exists to prevent.
    """
    schema_name = meta["schema_name"]
    table_name = meta["table_name"]
    new_hash = schema_hash(meta["columns"])
    policy = policy or {}
    gov = _governance_values(policy, watermark, checked_at)

    existing = session.scalar(
        select(SchemaCatalogORM).where(
            SchemaCatalogORM.tenant_id == tenant_id,
            SchemaCatalogORM.warehouse_id == warehouse_id,
            SchemaCatalogORM.schema_name == schema_name,
            SchemaCatalogORM.table_name == table_name,
        )
    )

    existing_point_id = existing.qdrant_point_id if existing else None

    if existing and existing.schema_hash == new_hash:
        for field, value in gov.items():
            setattr(existing, field, value)
        existing.last_crawled_at = datetime.now(UTC)
        # Payload-only update — no re-embedding, because nothing the vector is
        # built from has changed. Keeps the catalog the agent reads in step with
        # the row the answer's provenance is read from.
        if existing_point_id:
            try:
                qdrant.set_payload(
                    QDRANT_COLLECTION,
                    payload=_governance_payload(policy, watermark, checked_at),
                    points=[existing_point_id],
                )
            except Exception:  # noqa: BLE001 — payload refresh is best-effort; the row is written
                log.warning("process_table | qdrant payload refresh failed for %s.%s", schema_name, table_name)
        return False

    description = describe_table(table_name, meta["columns"], meta.get("row_count"))
    text_description = render_text_description(table_name, description)
    vector = embed(text_description)
    point_id = _point_id(tenant_id, warehouse_id, schema_name, table_name)

    stmt = pg_insert(SchemaCatalogORM).values(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        warehouse_id=warehouse_id,
        schema_name=schema_name,
        table_name=table_name,
        columns=meta["columns"],
        row_count=meta.get("row_count"),
        last_altered=meta.get("last_altered"),
        text_description=text_description,
        join_hints=description["join_hints"],
        schema_hash=new_hash,
        qdrant_point_id=point_id,
        last_crawled_at=datetime.now(UTC),
        **gov,
    )
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_schemacatalog_table",
            set_={
                "columns": stmt.excluded.columns,
                "row_count": stmt.excluded.row_count,
                "last_altered": stmt.excluded.last_altered,
                "text_description": stmt.excluded.text_description,
                "join_hints": stmt.excluded.join_hints,
                "schema_hash": stmt.excluded.schema_hash,
                "qdrant_point_id": stmt.excluded.qdrant_point_id,
                "owner": stmt.excluded.owner,
                "classification": stmt.excluded.classification,
                "upstream": stmt.excluded.upstream,
                "freshness_column": stmt.excluded.freshness_column,
                "data_as_of": stmt.excluded.data_as_of,
                "freshness_checked_at": stmt.excluded.freshness_checked_at,
                "freshness_sla_seconds": stmt.excluded.freshness_sla_seconds,
                "last_crawled_at": func.now(),
            },
        )
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
            **_governance_payload(policy, watermark, checked_at),
        },
    )])
    log.info("process_table | crawled tenant=%s table=%s.%s (changed)", tenant_id, schema_name, table_name)
    return True


def _cleanup_missing_tables(
    session: Session, qdrant: QdrantClient, tenant_id: str, warehouse_id: str, schema_name: str,
    discovered_table_names: set[str], cutoff: datetime,
) -> int:
    """Only called for a full-warehouse crawl — removes SchemaCatalog rows
    (+ their Qdrant point) for tables no longer seen in the warehouse's
    INFORMATION_SCHEMA, so the catalog doesn't keep suggesting dropped/
    renamed tables to the agent forever.

    Requires the row to ALSO have been stale before this crawl started
    (last_crawled_at < cutoff, 1.5x CRAWL_INTERVAL_SECONDS ago — see
    crawl_warehouse() for why 1.5x and not 1x) — i.e. missing across two
    consecutive full crawls, not just this one — so a one-off warehouse-side
    blip (timeout, a table transiently reporting zero columns) doesn't get
    mistaken for a real DROP TABLE and delete a still-valid catalog entry."""
    rows = session.execute(
        select(SchemaCatalogORM.id, SchemaCatalogORM.table_name, SchemaCatalogORM.qdrant_point_id)
        .where(
            SchemaCatalogORM.tenant_id == tenant_id,
            SchemaCatalogORM.warehouse_id == warehouse_id,
            SchemaCatalogORM.schema_name == schema_name,
            SchemaCatalogORM.last_crawled_at < cutoff,
        )
    ).all()
    stale = [(r.id, r.table_name, r.qdrant_point_id) for r in rows if r.table_name not in discovered_table_names]
    if not stale:
        return 0

    session.execute(delete(SchemaCatalogORM).where(SchemaCatalogORM.id.in_([s[0] for s in stale])))
    point_ids = [s[2] for s in stale if s[2]]
    if point_ids:
        qdrant.delete(QDRANT_COLLECTION, points_selector=point_ids)
    for _, table_name, _ in stale:
        log.info(
            "crawl_warehouse | removed stale catalog entry table=%s.%s warehouse=%s (no longer in warehouse)",
            schema_name, table_name, warehouse_id,
        )
    return len(stale)


def crawl_warehouse(
    SessionLocal: sessionmaker[Session], qdrant: QdrantClient, tenant_id: str, warehouse_id: str, conn_params: dict[str, Any],
    table_names: list[str] | None, full_crawl: bool = False, engine: str = metadata_dispatch.SNOWFLAKE,
    config: dict[str, Any] | None = None,
) -> None:
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

    cutoff uses 1.5x CRAWL_INTERVAL_SECONDS, not 1x: _is_due() only fires
    once *every* row's last_crawled_at is already >= 1x the interval old, so
    at 1x a still-present table that was simply crawled together with
    everything else last cycle would sit almost exactly ON the cutoff (off by
    only the poll/processing jitter between "became due" and "this crawl
    actually ran") — a table missing for just one cycle could get deleted
    immediately instead of getting the two-cycle grace period
    _cleanup_missing_tables() promises. 1.5x leaves half an interval of
    slack, which dwarfs realistic jitter (seconds/minutes vs. a half day by
    default)."""
    cutoff = datetime.now(UTC) - timedelta(seconds=1.5 * CRAWL_INTERVAL_SECONDS)
    metadata = metadata_dispatch.discover_and_describe(engine, conn_params, table_names)
    changed = 0
    for table_name, meta in metadata.items():
        try:
            # Declared governance (owner / classification / lineage / SLA) and
            # the measured watermark are gathered per table, before the write,
            # so a failure to read either leaves the row's other facts intact.
            policy = governance.table_policy(config or {}, meta["schema_name"], table_name)
            policy["freshness_column"] = resolve_freshness_column(
                policy.get("freshness_column", ""), meta["columns"], table_name,
            )
            watermark, checked_at = None, None
            if policy["freshness_column"]:
                watermark = metadata_dispatch.table_watermark(
                    engine, conn_params, meta["schema_name"], table_name, policy["freshness_column"],
                )
                checked_at = datetime.now(UTC)

            with SessionLocal() as session:
                if process_table(session, qdrant, tenant_id, warehouse_id, meta, policy, watermark, checked_at):
                    changed += 1
                session.commit()
        except Exception:
            log.exception("crawl_warehouse | failed table=%s warehouse=%s", table_name, warehouse_id)
    log.info("crawl_warehouse | warehouse=%s: %d/%d table(s) changed", warehouse_id, changed, len(metadata))

    if full_crawl:
        try:
            with SessionLocal() as session:
                _cleanup_missing_tables(
                    session, qdrant, tenant_id, warehouse_id, conn_params.get("schema", ""),
                    set(metadata.keys()), cutoff,
                )
                session.commit()
        except Exception:
            log.exception("crawl_warehouse | cleanup of missing tables failed warehouse=%s", warehouse_id)


def _last_crawled_by_warehouse(session: Session) -> dict[tuple[str, str], datetime]:
    """One aggregate query for every (tenant, warehouse)'s most recent
    last_crawled_at, instead of one query per warehouse — crawl_all() now
    runs every POLL_INTERVAL_SECONDS tick (see run()), so an N+1 pattern
    here would mean N extra Postgres round-trips every few seconds for
    every registered warehouse, most of which aren't due yet."""
    rows = session.execute(
        select(
            SchemaCatalogORM.tenant_id,
            SchemaCatalogORM.warehouse_id,
            func.max(SchemaCatalogORM.last_crawled_at),
        ).group_by(SchemaCatalogORM.tenant_id, SchemaCatalogORM.warehouse_id)
    ).all()
    return {(str(t), str(w)): last for t, w, last in rows}


def _is_due(last_crawled_at: datetime | None) -> bool:
    """Per-warehouse crawl schedule derived from SchemaCatalog.last_crawled_at
    instead of one global timestamp shared by every tenant — avoids every
    warehouse landing on the exact same 24h boundary and spiking LLM/
    embedding/warehouse load all at once. No rows yet (first crawl ever, or
    every table failed last time) always counts as due."""
    if last_crawled_at is None:
        return True
    if last_crawled_at.tzinfo is None:
        last_crawled_at = last_crawled_at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - last_crawled_at).total_seconds() >= CRAWL_INTERVAL_SECONDS


# A warehouse that has never been crawled successfully has no SchemaCatalog rows, so
# _is_due() says "due" on every POLL_INTERVAL_SECONDS tick: a wrong password or an
# incomplete config was retried (and logged) every 5s forever. Failures back off
# exponentially instead, capped at the normal crawl interval. Process-local on purpose:
# a restart retrying at once is what an operator who just fixed the config wants.
_FIRST_RETRY_SECONDS = 60
_failure_backoff: dict[tuple[str, str], tuple[int, datetime]] = {}


def _in_backoff(key: tuple[str, str], now: datetime) -> bool:
    entry = _failure_backoff.get(key)
    return entry is not None and now < entry[1]


def _record_failure(key: tuple[str, str], now: datetime) -> None:
    failures = _failure_backoff.get(key, (0, now))[0] + 1
    delay = min(_FIRST_RETRY_SECONDS * 2 ** (failures - 1), CRAWL_INTERVAL_SECONDS)
    _failure_backoff[key] = (failures, now + timedelta(seconds=delay))
    log.info("crawl_all | warehouse=%s failed %d time(s), next attempt in %ds", key[1], failures, delay)


def crawl_all(SessionLocal: sessionmaker[Session], qdrant: QdrantClient, pending_ddl: dict[tuple[Any, ...], dict[str, Any]] | None = None) -> None:
    """pending_ddl, if given, lets already-due DDL events get processed
    between each warehouse of this cron batch instead of only after the
    whole batch finishes — a warehouse list with many tenants/tables can
    otherwise take long enough to starve the DDL-trigger path of its main
    selling point (fast turnaround) for the whole duration of one cron pass."""
    with SessionLocal() as session:
        warehouses = get_active_warehouses(session)
        last_crawled = _last_crawled_by_warehouse(session)
    # debug, not info: this runs every POLL_INTERVAL_SECONDS tick.
    log.debug(
        "crawl_all | %d active warehouse(s) registered across %s",
        len(warehouses), sorted({w["engine"] for w in warehouses}) or "-",
    )

    for w in warehouses:
        tenant_id, warehouse_id = str(w["tenant_id"]), str(w["warehouse_id"])
        key = (tenant_id, warehouse_id)

        if not _is_due(last_crawled.get(key)) or _in_backoff(key, datetime.now(UTC)):
            continue

        engine = w["engine"]
        conn_params = metadata_dispatch.connection_params(engine, w["config"], warehouse_id)
        if not conn_params:
            _record_failure(key, datetime.now(UTC))
            continue
        table_names = selected_table_names(w["config"])
        try:
            crawl_warehouse(
                SessionLocal, qdrant, tenant_id, warehouse_id, conn_params, table_names,
                full_crawl=True, engine=engine, config=w["config"],
            )
        except Exception:
            log.exception("crawl_all | warehouse=%s failed", w["warehouse_id"])
            _record_failure(key, datetime.now(UTC))
        else:
            _failure_backoff.pop(key, None)

        if pending_ddl is not None:
            process_due_ddl_events(SessionLocal, qdrant, pending_ddl)


def handle_ddl_event(SessionLocal: sessionmaker[Session], qdrant: QdrantClient, event: dict[str, Any]) -> None:
    """doc §2.8 — targeted single-table crawl, called after the debounce
    window in run()'s loop elapses.

    tenant_id is re-derived from KBWarehouse by warehouse_id, NOT trusted
    from the event payload — the event only travels over Kafka, which this
    worker doesn't control the producer side of, so a wrong/spoofed
    tenant_id in the message must not be able to attribute another
    warehouse's schema metadata to the wrong tenant in SchemaCatalog."""
    warehouse_id = event["warehouse_id"]
    table_name = event["table_name"]

    with SessionLocal() as session:
        row = session.execute(
            select(KBWarehouseORM.tenant_id, KBWarehouseConfigORM.config, KBWarehouseORM.service)
            .join(KBWarehouseConfigORM, KBWarehouseConfigORM.warehouse_id == KBWarehouseORM.warehouse_id)
            .where(
                KBWarehouseORM.warehouse_id == warehouse_id,
                KBWarehouseORM.is_deleted.is_(False),
                KBWarehouseConfigORM.is_active.is_(True),
                KBWarehouseConfigORM.is_deleted.is_(False),
            )
        ).one_or_none()
    if not row:
        log.warning("handle_ddl_event | no active warehouse/config for warehouse=%s, skipping", warehouse_id)
        return

    tenant_id = str(row.tenant_id)
    if event.get("tenant_id") and event["tenant_id"] != tenant_id:
        log.warning(
            "handle_ddl_event | event tenant_id=%s does not match warehouse=%s owner tenant_id=%s — "
            "dropping suspicious event", event["tenant_id"], warehouse_id, tenant_id,
        )
        return

    engine = metadata_dispatch.normalize_engine(row.service)
    if engine is None:
        log.warning(
            "handle_ddl_event | warehouse=%s has unsupported service=%r, skipping",
            warehouse_id, row.service,
        )
        return

    conn_params = metadata_dispatch.connection_params(engine, row.config, warehouse_id)
    if not conn_params:
        return

    crawl_warehouse(SessionLocal, qdrant, tenant_id, warehouse_id, conn_params, [table_name], engine=engine, config=row.config)


def _valid_ddl_event(event: Any) -> bool:
    """Schema check before trusting an event enough to even queue it — every
    field must be a non-empty string. Doesn't validate that tenant_id is the
    *correct* owner of warehouse_id (handle_ddl_event() re-derives that from
    KBWarehouse instead of trusting this field at all); this is just
    malformed-message hygiene."""
    if not isinstance(event, dict):
        return False
    return all(isinstance(event.get(f), str) and event.get(f) for f in ("tenant_id", "warehouse_id", "table_name"))


def poll_ddl_events(consumer: KafkaConsumer, pending_ddl: dict[tuple[Any, ...], dict[str, Any]]) -> None:
    """doc §2.8 — one poll cycle: drain whatever's available, (re)stamp each
    table's debounce deadline. A second event for the same table within the
    window overwrites the first's `due` rather than queuing a second
    trigger — that overwrite *is* the debounce.

    Does NOT commit the consumer offset — that only happens once pending_ddl
    is fully drained (see run()'s loop), so a crash between here and the
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


def process_due_ddl_events(SessionLocal: sessionmaker[Session], qdrant: QdrantClient, pending_ddl: dict[tuple[Any, ...], dict[str, Any]]) -> list[dict[str, Any]]:
    """Pops and crawls every entry whose debounce window has elapsed.
    Returns the events actually processed (for logging/tests)."""
    now = time.time()
    processed = []
    for key in [k for k, v in pending_ddl.items() if v["due"] <= now]:
        event = pending_ddl.pop(key)["event"]
        try:
            handle_ddl_event(SessionLocal, qdrant, event)
            processed.append(event)
        except Exception:
            log.exception("ddl event handling failed: %s", event)
    return processed


def maybe_reconnect_kafka(
    consumer: KafkaConsumer | None, now: float, last_attempt: float
) -> tuple[KafkaConsumer | None, float]:
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


def run() -> None:
    from libs.shared.observability import setup_observability

    setup_observability("platform-workers-schema-crawler")

    SessionLocal = worker_db.make_session_factory(PostgresSettings())
    qdrant = QdrantClient(url=QDRANT_URL)
    ensure_qdrant_collection(qdrant)
    consumer = try_kafka_consumer()

    pending_ddl: dict[tuple[Any, ...], dict[str, Any]] = {}
    last_kafka_attempt = time.time()

    log.info("schema-crawler started. crawl_interval=%ss kafka=%s", CRAWL_INTERVAL_SECONDS, bool(consumer))
    while True:
        try:
            consumer, last_kafka_attempt = maybe_reconnect_kafka(consumer, time.time(), last_kafka_attempt)

            if consumer is not None:
                try:
                    poll_ddl_events(consumer, pending_ddl)
                except Exception:
                    log.exception("Kafka consumer failed — falling back to cron-only until next retry")
                    with contextlib.suppress(Exception):
                        consumer.close()
                    consumer = None
                    last_kafka_attempt = time.time()

            process_due_ddl_events(SessionLocal, qdrant, pending_ddl)

            # No global CRAWL_INTERVAL_SECONDS gate here — each warehouse
            # tracks its own next-due time off SchemaCatalog.last_crawled_at
            # (_is_due()), and crawl_all() skips whatever isn't due yet, so
            # calling it every tick is cheap and naturally staggers load
            # across warehouses instead of every tenant landing on the same
            # 24h boundary at once. pending_ddl is threaded through so
            # already-due DDL events get serviced between warehouses instead
            # of only after the whole batch.
            crawl_all(SessionLocal, qdrant, pending_ddl)

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
        except Exception as e:  # noqa: BLE001 — the loop must outlive any single failure
            log.error("main loop error: %s", e)
            time.sleep(5)
