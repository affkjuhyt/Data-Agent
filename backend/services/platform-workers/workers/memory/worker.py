"""Memory extraction worker (ex memory-consumer).

Reads memory.extraction.requested (emitted by workflow-runtime after each turn
— see backend/services/workflow-runtime/memory_middleware.py:apply_memory_policy),
re-reads the turn text from Postgres "Messages", calls the extraction LLM, and
writes Semantic LTM (AgentMemories + Qdrant), Episodic Past, and Episodic
Future memories. Implements the doc's 4-decision lifecycle
(ADD/UPDATE/DELETE/NO_CHANGE) for semantic facts.

Delivery: at-least-once. A message is retried up to 3x with exponential
backoff (RETRY_BACKOFFS); if it still fails it is forwarded to
memory.extraction.dlq and the offset is committed anyway, so one bad message
can't block the partition forever (the audit consumer instead blocks on
failure — this is the documented improvement, see
docs/STM_LTM_Memory_Implementation.md).
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any

from common import kafka as kafka_factory
from common import llm as llm_client
from common import pg
from common import qdrant as qdrant_factory
from common.settings import KafkaSettings, LiteLLMSettings, PostgresSettings, QdrantSettings
from kafka import KafkaProducer
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from qdrant_client import QdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    MatchValue,
    PointIdsList,
    PointStruct,
)
from tenacity import RetryError, Retrying, stop_after_attempt, wait_chain, wait_fixed

from libs.shared.embeddings import embed_one

log = logging.getLogger("platform-workers.memory")


class Settings(BaseSettings):
    """Memory-worker knobs — same env names the memory-consumer container used."""

    model_config = SettingsConfigDict(extra="ignore")

    topic: str = Field(default="memory.extraction.requested", validation_alias="KAFKA_TOPIC")
    dlq_topic: str = Field(default="memory.extraction.dlq", validation_alias="KAFKA_DLQ_TOPIC")
    group_id: str = Field(default="memory-consumer-group", validation_alias="KAFKA_GROUP_ID")


_settings = Settings()
_qdrant_settings = QdrantSettings()
_litellm = LiteLLMSettings()

TOPIC = _settings.topic
DLQ_TOPIC = _settings.dlq_topic
GROUP_ID = _settings.group_id

QDRANT_URL = _qdrant_settings.url
QDRANT_COLLECTION = "agent_memories"
EMBEDDING_DIM = _qdrant_settings.embedding_dim

LITELLM_BASE = _litellm.base_url
LITELLM_KEY = _litellm.api_key

RETRY_BACKOFFS = (0, 1, 2, 4)  # first attempt + 3 retries, exponential (doc §II)

DEFAULT_DOMAIN = "general"
CATCH_ALL_FACT_KEY = "general_note"
DOMAIN_FACT_KEYS = {
    "user_pref": [
        "preferred_report_format", "preferred_language",
        "preferred_notification_time", "preferred_channel", CATCH_ALL_FACT_KEY,
    ],
    "general": ["business_context", "kpi_definition", "org_structure", CATCH_ALL_FACT_KEY],
}
DEFAULT_AGENTMEMORIES_MAX_PER_USER = 200  # doc §3.2
DEFAULT_EPISODIC_RETENTION_DAYS = 30      # doc §3.3 — used only when no MemoryRetentionConfig row exists
MIN_IMPORTANCE_TO_STORE = 0.5             # doc §II Bước 2 — "score >= 5" on the 1-10 scale, normalized to 0-1

_PROMPT_PATH = os.path.join(os.path.dirname(__file__), "extract_prompt.txt")
with open(_PROMPT_PATH, encoding="utf-8") as _f:
    _EXTRACT_PROMPT = _f.read()


def _vocab_block() -> str:
    return "\n".join(f"- {d}: {', '.join(k)}" for d, k in DOMAIN_FACT_KEYS.items())


def _normalize_fact_key(domain: str, fact_key: str) -> tuple[str, str]:
    if domain not in DOMAIN_FACT_KEYS:
        domain = DEFAULT_DOMAIN
    allowed = DOMAIN_FACT_KEYS[domain]
    if not fact_key or fact_key not in allowed:
        fact_key = CATCH_ALL_FACT_KEY
    return domain, fact_key


def _qdrant_point_id(tenant_id: str, agent_id: str, user_ref: str, domain: str, fact_key: str) -> str:
    """Deterministic — the same fact always maps to the same Qdrant point, making
    upsert idempotent across retries (doc §3.2)."""
    name = f"{tenant_id}|{agent_id}|{user_ref or ''}|{domain}|{fact_key}"
    return str(uuid.uuid5(uuid.NAMESPACE_OID, name))


# ─── Infra bootstrap ────────────────────────────────────────────────────────

def ensure_qdrant_collection(qdrant: QdrantClient) -> None:
    """Module-level EMBEDDING_DIM is read at call time on purpose: the
    integration tests monkeypatch it to a tiny width."""
    qdrant_factory.ensure_collection(qdrant, QDRANT_COLLECTION, EMBEDDING_DIM)


# ─── LLM calls ───────────────────────────────────────────────────────────────

def call_llm_extract(query: str, response: str) -> list[dict[str, Any]]:
    # .replace() instead of .format() — the template's own JSON example uses
    # literal braces, and query/response are arbitrary user text (code, JSON,
    # math) that could contain them too. .format() would require escaping the
    # template's braces and stays fragile to future edits; .replace() doesn't care.
    prompt = (
        _EXTRACT_PROMPT
        .replace("{query}", query[:500])
        .replace("{response}", response[:1000])
        .replace("{vocab}", _vocab_block())
    )
    data = llm_client.chat_json(
        [{"role": "user", "content": prompt}], "planner",
        settings=_litellm, max_tokens=400, temperature=0, timeout=20,
    )
    if data is None:
        log.warning("extraction LLM returned non-JSON, skipping turn")
        return []
    return data.get("insights", []) if isinstance(data, dict) else []


def embed(text: str) -> list[float]:
    return embed_one(text, base_url=LITELLM_BASE, api_key=LITELLM_KEY, timeout=30)


# ─── Postgres helpers ────────────────────────────────────────────────────────

def fetch_turn(cur: Any, conversation_id: str, message_id: str) -> tuple[str | None, str | None]:
    """Re-reads the assistant message + the immediately preceding user message.
    Postgres Messages is the source of truth; the Kafka payload is just a pointer."""
    cur.execute('SELECT content, created_at FROM "Messages" WHERE id = %s', (message_id,))
    row = cur.fetchone()
    if not row:
        return None, None
    assistant_text = (row[0] or {}).get("text", "")
    cur.execute(
        """SELECT content FROM "Messages"
           WHERE conversation_id = %s AND role = 'user' AND created_at <= %s
           ORDER BY created_at DESC LIMIT 1""",
        (conversation_id, row[1]),
    )
    urow = cur.fetchone()
    user_text = (urow[0] or {}).get("text", "") if urow else ""
    return user_text, assistant_text


def get_retention_config(cur: Any, tenant_id: str, domain: str) -> tuple[int | None, int]:
    cur.execute(
        'SELECT retention_days, max_records_per_user FROM "MemoryRetentionConfig" WHERE tenant_id=%s AND domain=%s',
        (tenant_id, domain),
    )
    row = cur.fetchone()
    if not row:
        return DEFAULT_EPISODIC_RETENTION_DAYS, DEFAULT_AGENTMEMORIES_MAX_PER_USER
    return row[0], row[1]


def enforce_agent_memories_cap(cur: Any, tenant_id: str, agent_id: str, user_ref: str, domain: str, cap: int) -> None:
    cur.execute(
        """SELECT COUNT(*) FROM "AgentMemories"
           WHERE tenant_id=%s AND agent_id=%s AND user_ref=%s AND domain=%s AND deleted_at IS NULL""",
        (tenant_id, agent_id, user_ref, domain),
    )
    count = cur.fetchone()[0]
    if count <= cap:
        return
    over_by = count - cap
    cur.execute(
        """UPDATE "AgentMemories" SET deleted_at = now()
           WHERE id IN (
             SELECT id FROM "AgentMemories"
             WHERE tenant_id=%s AND agent_id=%s AND user_ref=%s AND domain=%s AND deleted_at IS NULL
             ORDER BY importance ASC NULLS FIRST, updated_at ASC
             LIMIT %s
           )""",
        (tenant_id, agent_id, user_ref, domain, over_by),
    )
    log.info("AgentMemories cap evicted %d rows (tenant=%s domain=%s)", over_by, tenant_id, domain)


# ─── Insight processing (doc §V — 4-decision lifecycle) ────────────────────

def _catch_all_search(qdrant: QdrantClient, vector: list[float], tenant_id: str, agent_id: str, domain: str) -> Any | None:
    """Novelty-detection search scoped to catch-all entries only (doc §V
    thresholds), not against controlled-vocab facts — those represent a
    single canonical value per fact_key and have no novelty question to
    answer. Returns the top hit, or None."""
    hits = qdrant.search(
        QDRANT_COLLECTION,
        query_vector=vector,
        query_filter=Filter(must=[
            FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
            FieldCondition(key="agent_id", match=MatchValue(value=agent_id)),
            FieldCondition(key="domain", match=MatchValue(value=domain)),
            FieldCondition(key="is_catch_all", match=MatchValue(value=True)),
        ]),
        limit=1,
    )
    return hits[0] if hits else None


def _resolve_catch_all_key(qdrant: QdrantClient, vector: list[float], tenant_id: str, agent_id: str,
                            domain: str, message_id: str, index: int) -> tuple[str, str]:
    """doc §V: ADD when sim<0.7, UPDATE when sim>0.85, NO_CHANGE when
    sim>0.95. The 0.7-0.85 band is left unspecified by the doc — resolved
    here as ADD (a new, separate fact) since silently overwriting a
    possibly-unrelated note is the worse failure mode of the two.

    Returns (fact_key, decision). On ADD, fact_key is a fresh composite key
    deterministic from (message_id, index) — same idempotency trick as
    _episodic_id(), so Kafka redelivery of the same insight can't mint two
    rows for what is really one fact."""
    try:
        hit = _catch_all_search(qdrant, vector, tenant_id, agent_id, domain)
    except Exception as e:  # noqa: BLE001 — a search outage degrades to ADD, by design
        log.warning("qdrant catch-all search failed, treating as ADD: %s", e)
        hit = None

    if hit and hit.score > 0.95:
        return hit.payload["fact_key"], "NO_CHANGE"
    if hit and hit.score > 0.85:
        return hit.payload["fact_key"], "UPDATE"

    new_key = f"{CATCH_ALL_FACT_KEY}:{_episodic_id(message_id, 'catch_all_fact', index)}"
    return new_key, "ADD"


def _delete_catch_all_match(cur: Any, qdrant: QdrantClient, tenant_id: str, agent_id: str, user_ref: str,
                             domain: str, content: str) -> None:
    """Best-effort match against the user's catch-all facts (doc §V DELETE
    example) — same spirit as cancel_future_memory()'s fuzzy match. No
    confident match -> no-op, since deleting the wrong fact is worse than
    leaving a stale one."""
    vector = embed(content)
    try:
        hit = _catch_all_search(qdrant, vector, tenant_id, agent_id, domain)
    except Exception as e:  # noqa: BLE001 — no match is the safe outcome for a delete
        log.warning("qdrant catch-all search failed during delete: %s", e)
        hit = None

    if not hit or hit.score <= 0.85:
        log.info("catch_all delete — no confident match (best=%s), skipped", hit.score if hit else None)
        return

    fact_key = hit.payload["fact_key"]
    cur.execute(
        """UPDATE "AgentMemories" SET deleted_at = now()
           WHERE tenant_id=%s AND agent_id=%s AND user_ref=%s AND domain=%s AND fact_key=%s AND deleted_at IS NULL""",
        (tenant_id, agent_id, user_ref, domain, fact_key),
    )
    try:
        qdrant.delete(QDRANT_COLLECTION, points_selector=PointIdsList(points=[hit.id]))
    except Exception as e:  # noqa: BLE001 — the row is soft-deleted; a stale point is tolerable
        log.warning("qdrant delete failed point=%s: %s", hit.id, e)


def process_semantic_insight(cur: Any, qdrant: QdrantClient, tenant_id: str, agent_id: str, user_ref: str,
                              insight: dict[str, Any], message_id: str, index: int) -> None:
    domain, base_fact_key = _normalize_fact_key(insight.get("domain") or "", insight.get("fact_key") or "")
    content = (insight.get("content") or "").strip()
    action = insight.get("action", "add")
    is_catch_all = base_fact_key == CATCH_ALL_FACT_KEY

    if action == "delete":
        if is_catch_all:
            _delete_catch_all_match(cur, qdrant, tenant_id, agent_id, user_ref, domain, content)
            return
        point_id = _qdrant_point_id(tenant_id, agent_id, user_ref, domain, base_fact_key)
        cur.execute(
            """UPDATE "AgentMemories" SET deleted_at = now()
               WHERE tenant_id=%s AND agent_id=%s AND user_ref=%s AND domain=%s AND fact_key=%s AND deleted_at IS NULL""",
            (tenant_id, agent_id, user_ref, domain, base_fact_key),
        )
        try:
            qdrant.delete(QDRANT_COLLECTION, points_selector=PointIdsList(points=[point_id]))
        except Exception as e:  # noqa: BLE001 — the row is soft-deleted; a stale point is tolerable
            log.warning("qdrant delete failed point=%s: %s", point_id, e)
        return

    if not content:
        return

    importance = max(0.0, min(1.0, float(insight.get("importance_score") or 0.5)))
    vector = embed(content)

    if is_catch_all:
        # Catch-all bucket can hold many distinct facts — novelty-detect by
        # similarity across ALL catch-all entries for this user+domain
        # (doc §V), instead of the single-slot-per-fact_key model below
        # (which would otherwise silently overwrite an unrelated note).
        fact_key, decision = _resolve_catch_all_key(qdrant, vector, tenant_id, agent_id, domain, message_id, index)
        if decision == "NO_CHANGE":
            cur.execute(
                """UPDATE "AgentMemories" SET updated_at = now()
                   WHERE tenant_id=%s AND agent_id=%s AND user_ref=%s AND domain=%s AND fact_key=%s AND deleted_at IS NULL""",
                (tenant_id, agent_id, user_ref, domain, fact_key),
            )
            return
    else:
        fact_key = base_fact_key
        sim = None
        try:
            hits = qdrant.search(
                QDRANT_COLLECTION,
                query_vector=vector,
                query_filter=Filter(must=[
                    FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                    FieldCondition(key="agent_id", match=MatchValue(value=agent_id)),
                    FieldCondition(key="domain", match=MatchValue(value=domain)),
                    FieldCondition(key="fact_key", match=MatchValue(value=fact_key)),
                ]),
                limit=1,
            )
            sim = hits[0].score if hits else None
        except Exception as e:  # noqa: BLE001 — a search outage degrades to ADD, by design
            log.warning("qdrant search failed, treating as ADD: %s", e)

        if sim is not None and sim > 0.95:
            # NO_CHANGE — touch updated_at only (doc §V).
            cur.execute(
                """UPDATE "AgentMemories" SET updated_at = now()
                   WHERE tenant_id=%s AND agent_id=%s AND user_ref=%s AND domain=%s AND fact_key=%s AND deleted_at IS NULL""",
                (tenant_id, agent_id, user_ref, domain, fact_key),
            )
            return

    point_id = _qdrant_point_id(tenant_id, agent_id, user_ref, domain, fact_key)

    # ADD or UPDATE — ON CONFLICT makes this idempotent across retries.
    scope = "user" if user_ref and user_ref != "anonymous" else "global"
    cur.execute(
        """INSERT INTO "AgentMemories"
             (id, tenant_id, agent_id, scope, user_ref, memory_type, domain, fact_key, content, embedding_ref, importance)
           VALUES (%s, %s, %s, %s, %s, 'semantic', %s, %s, %s, %s, %s)
           ON CONFLICT (tenant_id, agent_id, scope, user_ref, domain, fact_key)
           DO UPDATE SET content = EXCLUDED.content, importance = EXCLUDED.importance,
                         embedding_ref = EXCLUDED.embedding_ref, updated_at = now(), deleted_at = NULL""",
        (str(uuid.uuid4()), tenant_id, agent_id, scope, user_ref, domain, fact_key, content, point_id, importance),
    )

    qdrant.upsert(QDRANT_COLLECTION, points=[PointStruct(
        id=point_id,
        vector=vector,
        payload={
            "tenant_id": tenant_id, "agent_id": agent_id, "domain": domain, "fact_key": fact_key,
            "pg_id": point_id, "is_catch_all": is_catch_all,
        },
    )])

    _, cap = get_retention_config(cur, tenant_id, domain)
    enforce_agent_memories_cap(cur, tenant_id, agent_id, user_ref, domain, cap)


def _episodic_id(message_id: str, kind: str, index: int) -> str:
    """Deterministic id derived from (message_id, insight position) — makes
    EpisodicMemory/FutureMemory inserts idempotent under Kafka at-least-once
    redelivery, the same way _qdrant_point_id() does for semantic facts."""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"{message_id}|{kind}|{index}"))


def process_episodic_past(cur: Any, tenant_id: str, agent_id: str, user_ref: str, insight: dict[str, Any], episodic_id: str) -> None:
    content = (insight.get("content") or "").strip()
    if not content:
        return
    domain = insight.get("domain") or DEFAULT_DOMAIN
    importance = max(0.0, min(1.0, float(insight.get("importance_score") or 0.5)))
    retention_days, _ = get_retention_config(cur, tenant_id, domain)
    cur.execute(
        """INSERT INTO "EpisodicMemory" (id, tenant_id, agent_id, user_ref, domain, content, importance_score, expires_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s,
                   CASE WHEN %s::int IS NULL THEN NULL ELSE now() + (%s || ' days')::interval END)
           ON CONFLICT (id) DO NOTHING""",
        (episodic_id, tenant_id, agent_id, user_ref, domain, content, importance, retention_days, retention_days),
    )


def process_episodic_future(cur: Any, tenant_id: str, agent_id: str, user_ref: str, insight: dict[str, Any], future_id: str) -> None:
    content = (insight.get("content") or "").strip()
    execution_time = insight.get("execution_time")
    if not content or not execution_time:
        log.warning("episodic_future insight missing content/execution_time — skipped: %s", insight)
        return
    cur.execute(
        """INSERT INTO "FutureMemory" (id, tenant_id, agent_id, user_ref, content, execution_time, recurrence)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (id) DO NOTHING""",
        (future_id, tenant_id, agent_id, user_ref, content, execution_time, insight.get("recurrence")),
    )


def cancel_future_memory(cur: Any, tenant_id: str, agent_id: str, user_ref: str, insight: dict[str, Any]) -> None:
    """Best-effort match against the user's pending reminders (doc §V DELETE example).
    No match -> no-op; ambiguous matches resolve to the soonest one."""
    content = (insight.get("content") or "").strip()
    cur.execute(
        """UPDATE "FutureMemory" SET status = 'cancelled'
           WHERE id = (
             SELECT id FROM "FutureMemory"
             WHERE tenant_id=%s AND agent_id=%s AND user_ref=%s AND status='pending' AND content ILIKE %s
             ORDER BY execution_time ASC LIMIT 1
           )""",
        (tenant_id, agent_id, user_ref, f"%{content[:30]}%"),
    )


def process_insight(cur: Any, qdrant: QdrantClient, tenant_id: str, agent_id: str, user_ref: str, insight: dict[str, Any], message_id: str, index: int) -> None:
    memory_type = insight.get("memory_type")
    action = insight.get("action", "add")

    # doc §II/§VI — only insights worth remembering get stored; importance
    # is what distinguishes "Tôi thích PDF" from small talk. DELETE is a
    # direct command, not an insight being judged for storage-worthiness,
    # so it bypasses the gate.
    importance = float(insight.get("importance_score") or 0.5)
    if action != "delete" and importance < MIN_IMPORTANCE_TO_STORE:
        log.info("insight skipped — importance=%.2f below threshold=%.2f: %r",
                  importance, MIN_IMPORTANCE_TO_STORE, insight.get("content", "")[:80])
        return

    if memory_type == "semantic":
        process_semantic_insight(cur, qdrant, tenant_id, agent_id, user_ref, insight, message_id, index)
    elif memory_type == "episodic_past":
        episodic_id = _episodic_id(message_id, "episodic_past", index)
        process_episodic_past(cur, tenant_id, agent_id, user_ref, insight, episodic_id)
    elif memory_type == "episodic_future":
        if action == "delete":
            cancel_future_memory(cur, tenant_id, agent_id, user_ref, insight)
        else:
            future_id = _episodic_id(message_id, "episodic_future", index)
            process_episodic_future(cur, tenant_id, agent_id, user_ref, insight, future_id)
    else:
        log.warning("unknown memory_type=%r — insight skipped", memory_type)


# ─── Event handling ──────────────────────────────────────────────────────────

def handle_event(conn: Any, qdrant: QdrantClient, event: dict[str, Any]) -> None:
    tenant_id = event["tenant_id"]
    agent_id = event["agent_id"]
    user_ref = event.get("user_ref") or "anonymous"
    conversation_id = event["conversation_id"]
    message_id = event["message_id"]

    with conn.cursor() as cur:
        query, response = fetch_turn(cur, conversation_id, message_id)
    if response is None:
        log.warning("message_id=%s not found — skipping", message_id)
        return

    insights = call_llm_extract(query or "", response)
    if not insights:
        return

    # Each insight gets its own SAVEPOINT — in Postgres, one failed statement
    # aborts the whole transaction (InFailedSqlTransaction) until rolled back,
    # which would otherwise brick every remaining insight in this turn and the
    # final commit too.
    with conn.cursor() as cur:
        for i, insight in enumerate(insights):
            savepoint = f"insight_{i}"
            try:
                cur.execute(f"SAVEPOINT {savepoint}")
                process_insight(cur, qdrant, tenant_id, agent_id, user_ref, insight, message_id, i)
                cur.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception:
                log.exception("failed to process one insight, skipping it: %s", insight)
                cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                cur.execute(f"RELEASE SAVEPOINT {savepoint}")
    conn.commit()


def publish_to_dlq(producer: KafkaProducer, raw_event: Any) -> None:
    try:
        producer.send(DLQ_TOPIC, value=raw_event)
        producer.flush(timeout=5)
        log.error("event sent to DLQ topic=%s event=%s", DLQ_TOPIC, raw_event)
    except Exception as e:  # noqa: BLE001 — last resort; the offset is committed regardless
        log.error("failed to publish to DLQ — event dropped: %s", e)


class _Loop:
    """Holds the Postgres connection so the retry path can swap it out."""

    def __init__(self, conn: Any, qdrant: QdrantClient, pg_settings: PostgresSettings) -> None:
        self.conn = conn
        self.qdrant = qdrant
        self.pg_settings = pg_settings

    def handle_with_retries(self, event: dict[str, Any], offset: int) -> bool:
        """RETRY_BACKOFFS semantics: attempt, then sleep 1/2/4 s between the
        three retries; every failure rolls back and reconnects Postgres.
        Returns False once the budget is spent (caller sends to DLQ)."""
        retrying = Retrying(
            stop=stop_after_attempt(len(RETRY_BACKOFFS)),
            wait=wait_chain(*(wait_fixed(b) for b in RETRY_BACKOFFS[1:])),
            reraise=False,
        )
        try:
            for attempt in retrying:
                with attempt:
                    try:
                        handle_event(self.conn, self.qdrant, event)
                    except Exception as e:
                        log.error(
                            "attempt %d/%d failed for offset=%d: %s",
                            attempt.retry_state.attempt_number, len(RETRY_BACKOFFS), offset, e,
                        )
                        self.conn = pg.reconnect(self.conn, self.pg_settings)
                        raise
        except RetryError:
            return False
        return True


def run() -> None:
    from libs.shared.observability import setup_observability

    setup_observability("platform-workers-memory")

    kafka_settings = KafkaSettings()
    pg_settings = PostgresSettings()

    conn = pg.connect(pg_settings)
    consumer = kafka_factory.consumer([TOPIC], GROUP_ID, auto_offset_reset="earliest", settings=kafka_settings)
    assert consumer is not None  # required=True raises instead of returning None
    dlq_producer = kafka_factory.producer(kafka_settings)

    qdrant = QdrantClient(url=QDRANT_URL)
    ensure_qdrant_collection(qdrant)
    loop = _Loop(conn, qdrant, pg_settings)

    log.info("Consuming from %s ...", TOPIC)
    while True:
        try:
            records = consumer.poll(timeout_ms=2000)
            if not records:
                continue

            for _tp, messages in records.items():
                for msg in messages:
                    event = msg.value
                    if not isinstance(event, dict):
                        log.warning("Skipping non-dict message offset=%d", msg.offset)
                        continue
                    if not loop.handle_with_retries(event, msg.offset):
                        publish_to_dlq(dlq_producer, event)

            consumer.commit()
            log.debug("Committed offsets for %d partitions", len(records))

        except Exception as e:  # noqa: BLE001 — the loop must outlive any single failure
            log.error("Consumer loop error: %s", e)
            time.sleep(5)
