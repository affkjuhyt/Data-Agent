"""
Kafka Audit Consumer → PostgreSQL

Reads from the audit.auth.events Kafka topic (published by audit-bridge)
and inserts rows into the audit_logs partitioned table.

Delivery: at-least-once. Offset committed after successful PostgreSQL COMMIT.
"""
import json
import logging
import os
import time

import psycopg2
from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable

from shared.observability import setup_observability

setup_observability("audit-consumer")
log = logging.getLogger("audit-consumer")


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
TOPIC = os.environ.get("KAFKA_TOPIC", "audit.auth.events")
GROUP_ID = os.environ.get("KAFKA_GROUP_ID", "audit-consumer-group")
DB_HOST = os.environ.get("DB_HOST", "postgres")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "aeroflow")
DB_USER = os.environ.get("DB_USER", "aeroflow")
DB_PASS = os.environ.get("DB_PASSWORD", "aeroflow_secret")


def wait_for_postgres(retries: int = 20, delay: int = 5) -> psycopg2.extensions.connection:
    for attempt in range(1, retries + 1):
        try:
            conn = psycopg2.connect(
                host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS
            )
            conn.autocommit = False
            log.info("PostgreSQL connected")
            return conn
        except psycopg2.OperationalError:
            log.warning("PostgreSQL not ready, attempt %d/%d, retry in %ds", attempt, retries, delay)
            time.sleep(delay)
    raise RuntimeError("PostgreSQL unavailable after retries")


def wait_for_kafka(bootstrap: str, retries: int = 30, delay: int = 5) -> KafkaConsumer:
    for attempt in range(1, retries + 1):
        try:
            kwargs: dict = dict(
                bootstrap_servers=bootstrap,
                group_id=GROUP_ID,
                auto_offset_reset="earliest",
                enable_auto_commit=False,
                value_deserializer=lambda v: _safe_json(v),
                consumer_timeout_ms=1000,
            )
            if KAFKA_SASL_USER:
                kwargs.update(
                    security_protocol=KAFKA_SECURITY_PROTOCOL,
                    sasl_mechanism=KAFKA_SASL_MECHANISM,
                    sasl_plain_username=KAFKA_SASL_USER,
                    sasl_plain_password=KAFKA_SASL_PASS,
                )
            consumer = KafkaConsumer(TOPIC, **kwargs)
            log.info("Kafka consumer ready, subscribed to %s", TOPIC)
            return consumer
        except NoBrokersAvailable:
            log.warning("Kafka not ready, attempt %d/%d, retry in %ds", attempt, retries, delay)
            time.sleep(delay)
    raise RuntimeError("Kafka unavailable after retries")


def get_tenant_id(cur, realm_id: str):
    """Look up platform tenant from Keycloak realmId via keycloak_realm_configs."""
    if not realm_id:
        return None
    cur.execute(
        "SELECT tenant_id FROM keycloak_realm_configs WHERE realm_name = %s LIMIT 1",
        (realm_id,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def insert_event(cur, event: dict) -> bool:
    """Insert event into audit_logs. Returns False if event was already processed (dedup)."""
    source_event_id = event.get("id") or ""
    event_type = event.get("type", "UNKNOWN")
    user_id = event.get("userId") or "system"
    client_id = event.get("clientId") or ""
    status = "FAILED" if "ERROR" in event_type else "SUCCESS"
    tenant_id = get_tenant_id(cur, event.get("realmId"))

    # Dedup: skip if this source event was already written.
    # Partitioned tables can't have a global UNIQUE on source_event_id, so we
    # do a point-read first; the consumer is single-threaded so there's no race.
    if source_event_id:
        cur.execute(
            "SELECT 1 FROM audit_logs WHERE source_event_id = %s LIMIT 1",
            (source_event_id,),
        )
        if cur.fetchone():
            return False

    cur.execute(
        """
        INSERT INTO audit_logs
          (tenant_id, actor, actor_type, action, resource_type, resource_id,
           status, metadata, ip_address, source_event_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (tenant_id, user_id, "USER", event_type, "AUTH", client_id,
         status, json.dumps(event.get("details") or {}),
         event.get("ipAddress") or "", source_event_id or None),
    )
    return True


def main():
    conn = wait_for_postgres()
    consumer = wait_for_kafka(KAFKA_BOOTSTRAP)

    log.info("Consuming from %s ...", TOPIC)
    while True:
        try:
            records = consumer.poll(timeout_ms=2000)
            if not records:
                continue

            batch_failed = False
            for tp, messages in records.items():
                if batch_failed:
                    break
                for msg in messages:
                    event = msg.value
                    if not isinstance(event, dict):
                        log.warning("Skipping non-dict message offset=%d", msg.offset)
                        continue
                    try:
                        with conn.cursor() as cur:
                            inserted = insert_event(cur, event)
                        conn.commit()
                        if not inserted:
                            log.debug("Skipping duplicate event offset=%d", msg.offset)
                    except Exception as db_err:
                        conn.rollback()
                        log.error("DB write failed for event %s: %s", event.get("type"), db_err)
                        # reconnect on broken connection
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = wait_for_postgres()
                        # stop batch — offsets will NOT be committed; message will be retried
                        batch_failed = True
                        break

            if not batch_failed:
                consumer.commit()
                log.debug("Committed offsets for %d partitions", len(records))
            else:
                log.warning("Batch had failures — offsets not committed, will retry")

        except Exception as e:
            log.error("Consumer error: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
