"""
Kafka Memory Extraction Consumer

Reads memory.extraction.requested (emitted by workflow-runtime after each turn
— see services/workflow-runtime/memory_middleware.py:apply_memory_policy),
re-reads the turn text from Postgres "Messages", calls the extraction LLM, and
writes Semantic LTM (AgentMemories + Qdrant), Episodic Past, and Episodic
Future memories. Implements the doc's 4-decision lifecycle
(ADD/UPDATE/DELETE/NO_CHANGE) for semantic facts.

Delivery: at-least-once. A message is retried up to 3x with exponential
backoff; if it still fails it is forwarded to memory.extraction.dlq and the
offset is committed anyway, so one bad message can't block the partition
forever (audit-consumer instead blocks indefinitely on failure — this is the
documented improvement, see docs/STM_LTM_Memory_Implementation.md).
"""
import json
import logging
import os
import time
import uuid

import httpx
import psycopg2
from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import NoBrokersAvailable
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, Filter, FieldCondition, MatchValue, PointStruct, PointIdsList,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [memory-consumer] %(levelname)s %(message)s")
log = logging.getLogger("memory-consumer")


def _safe_json(raw: bytes):
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_SASL_USER = os.environ.get("KAFKA_SASL_USERNAME", "")
KAFKA_SASL_PASS = os.environ.get("KAFKA_SASL_PASSWORD", "")
KAFKA_SECURITY_PROTOCOL = os.environ.get("KAFKA_SECURITY_PROTOCOL", "SASL_PLAINTEXT")
KAFKA_SASL_MECHANISM = os.environ.get("KAFKA_SASL_MECHANISM", "PLAIN")
TOPIC = os.environ.get("KAFKA_TOPIC", "memory.extraction.requested")
DLQ_TOPIC = os.environ.get("KAFKA_DLQ_TOPIC", "memory.extraction.dlq")
GROUP_ID = os.environ.get("KAFKA_GROUP_ID", "memory-consumer-group")

DB_HOST = os.environ.get("DB_HOST", "postgres")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "dataagent")
DB_USER = os.environ.get("DB_USER", "admin")
DB_PASS = os.environ.get("DB_PASSWORD", "dev123")

QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
QDRANT_COLLECTION = "agent_memories"
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1536"))

LITELLM_BASE = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_KEY = os.environ.get("LITELLM_API_KEY", "sk-dev")

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


def wait_for_kafka_consumer(retries: int = 30, delay: int = 5) -> KafkaConsumer:
    for attempt in range(1, retries + 1):
        try:
            consumer = KafkaConsumer(
                TOPIC,
                bootstrap_servers=KAFKA_BOOTSTRAP,
                group_id=GROUP_ID,
                auto_offset_reset="earliest",
                enable_auto_commit=False,
                value_deserializer=_safe_json,
                consumer_timeout_ms=1000,
                **_kafka_sasl_kwargs(),
            )
            log.info("Kafka consumer ready, subscribed to %s", TOPIC)
            return consumer
        except NoBrokersAvailable:
            log.warning("Kafka not ready, attempt %d/%d, retry in %ds", attempt, retries, delay)
            time.sleep(delay)
    raise RuntimeError("Kafka unavailable after retries")


def wait_for_kafka_producer(retries: int = 30, delay: int = 5) -> KafkaProducer:
    for attempt in range(1, retries + 1):
        try:
            return KafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                acks="all",
                retries=5,
                **_kafka_sasl_kwargs(),
            )
        except NoBrokersAvailable:
            log.warning("Kafka producer not ready, attempt %d/%d, retry in %ds", attempt, retries, delay)
            time.sleep(delay)
    raise RuntimeError("Kafka unavailable after retries")


def ensure_qdrant_collection(qdrant: QdrantClient):
    names = [c.name for c in qdrant.get_collections().collections]
    if QDRANT_COLLECTION not in names:
        qdrant.create_collection(QDRANT_COLLECTION, vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE))
        log.info("qdrant collection '%s' created", QDRANT_COLLECTION)


# ─── LLM calls ───────────────────────────────────────────────────────────────

def call_llm_extract(query: str, response: str) -> list[dict]:
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
    r = httpx.post(
        f"{LITELLM_BASE}/v1/chat/completions",
        headers={"Authorization": f"Bearer {LITELLM_KEY}"},
        json={
            "model": "planner",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 400,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        },
        timeout=20,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"].strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        log.warning("extraction LLM returned non-JSON, skipping turn: %r", content[:200])
        return []
    return data.get("insights", []) if isinstance(data, dict) else []


def embed(text: str) -> list[float]:
    r = httpx.post(
        f"{LITELLM_BASE}/v1/embeddings",
        headers={"Authorization": f"Bearer {LITELLM_KEY}"},
        json={"model": "embedder", "input": text},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["data"][0]["embedding"]


# ─── Postgres helpers ────────────────────────────────────────────────────────

def fetch_turn(cur, conversation_id: str, message_id: str) -> tuple[str, str]:
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


def get_retention_config(cur, tenant_id: str, domain: str) -> tuple[int | None, int]:
    cur.execute(
        'SELECT retention_days, max_records_per_user FROM "MemoryRetentionConfig" WHERE tenant_id=%s AND domain=%s',
        (tenant_id, domain),
    )
    row = cur.fetchone()
    if not row:
        return DEFAULT_EPISODIC_RETENTION_DAYS, DEFAULT_AGENTMEMORIES_MAX_PER_USER
    return row[0], row[1]


def enforce_agent_memories_cap(cur, tenant_id: str, agent_id: str, user_ref: str, domain: str, cap: int):
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

def process_semantic_insight(cur, qdrant: QdrantClient, tenant_id: str, agent_id: str, user_ref: str, insight: dict):
    domain, fact_key = _normalize_fact_key(insight.get("domain"), insight.get("fact_key"))
    content = (insight.get("content") or "").strip()
    action = insight.get("action", "add")
    point_id = _qdrant_point_id(tenant_id, agent_id, user_ref, domain, fact_key)

    if action == "delete":
        cur.execute(
            """UPDATE "AgentMemories" SET deleted_at = now()
               WHERE tenant_id=%s AND agent_id=%s AND user_ref=%s AND domain=%s AND fact_key=%s AND deleted_at IS NULL""",
            (tenant_id, agent_id, user_ref, domain, fact_key),
        )
        try:
            qdrant.delete(QDRANT_COLLECTION, points_selector=PointIdsList(points=[point_id]))
        except Exception as e:
            log.warning("qdrant delete failed point=%s: %s", point_id, e)
        return

    if not content:
        return

    importance = max(0.0, min(1.0, float(insight.get("importance_score") or 0.5)))
    vector = embed(content)

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
    except Exception as e:
        log.warning("qdrant search failed, treating as ADD: %s", e)

    if sim is not None and sim > 0.95:
        # NO_CHANGE — touch updated_at only (doc §V).
        cur.execute(
            """UPDATE "AgentMemories" SET updated_at = now()
               WHERE tenant_id=%s AND agent_id=%s AND user_ref=%s AND domain=%s AND fact_key=%s AND deleted_at IS NULL""",
            (tenant_id, agent_id, user_ref, domain, fact_key),
        )
        return

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
        payload={"tenant_id": tenant_id, "agent_id": agent_id, "domain": domain, "fact_key": fact_key, "pg_id": point_id},
    )])

    _, cap = get_retention_config(cur, tenant_id, domain)
    enforce_agent_memories_cap(cur, tenant_id, agent_id, user_ref, domain, cap)


def _episodic_id(message_id: str, kind: str, index: int) -> str:
    """Deterministic id derived from (message_id, insight position) — makes
    EpisodicMemory/FutureMemory inserts idempotent under Kafka at-least-once
    redelivery, the same way _qdrant_point_id() does for semantic facts."""
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"{message_id}|{kind}|{index}"))


def process_episodic_past(cur, tenant_id: str, agent_id: str, user_ref: str, insight: dict, episodic_id: str):
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


def process_episodic_future(cur, tenant_id: str, agent_id: str, user_ref: str, insight: dict, future_id: str):
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


def cancel_future_memory(cur, tenant_id: str, agent_id: str, user_ref: str, insight: dict):
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


def process_insight(cur, qdrant: QdrantClient, tenant_id: str, agent_id: str, user_ref: str, insight: dict, message_id: str, index: int):
    memory_type = insight.get("memory_type")
    action = insight.get("action", "add")
    if memory_type == "semantic":
        process_semantic_insight(cur, qdrant, tenant_id, agent_id, user_ref, insight)
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

def handle_event(conn, qdrant: QdrantClient, event: dict):
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


def publish_to_dlq(producer: KafkaProducer, raw_event):
    try:
        producer.send(DLQ_TOPIC, value=raw_event)
        producer.flush(timeout=5)
        log.error("event sent to DLQ topic=%s event=%s", DLQ_TOPIC, raw_event)
    except Exception as e:
        log.error("failed to publish to DLQ — event dropped: %s", e)


def main():
    conn = wait_for_postgres()
    consumer = wait_for_kafka_consumer()
    dlq_producer = wait_for_kafka_producer()

    qdrant = QdrantClient(url=QDRANT_URL)
    ensure_qdrant_collection(qdrant)

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

                    succeeded = False
                    for attempt, backoff in enumerate(RETRY_BACKOFFS, start=1):
                        if backoff:
                            time.sleep(backoff)
                        try:
                            handle_event(conn, qdrant, event)
                            succeeded = True
                            break
                        except Exception as e:
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                            log.error("attempt %d/%d failed for offset=%d: %s", attempt, len(RETRY_BACKOFFS), msg.offset, e)
                            try:
                                conn.close()
                            except Exception:
                                pass
                            conn = wait_for_postgres()

                    if not succeeded:
                        publish_to_dlq(dlq_producer, event)

            consumer.commit()
            log.debug("Committed offsets for %d partitions", len(records))

        except Exception as e:
            log.error("Consumer loop error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
