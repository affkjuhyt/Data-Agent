"""Debug consumer — reads a few recent messages for local troubleshooting.
NOT for production use: no schema validation, minimal ACL requirements.
"""
import json
import os
import sys

from kafka import KafkaConsumer

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "kafka:9092")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "release.pipeline.triggered")
KAFKA_USER = os.environ.get("KAFKA_SASL_USERNAME", "release-worker")
KAFKA_PASS = os.environ.get("KAFKA_SASL_PASSWORD", "")
# KAFKA_SECURITY_PROTOCOL: SASL_PLAINTEXT (dev) or SASL_SSL (production)
KAFKA_SECURITY_PROTOCOL = os.environ.get("KAFKA_SECURITY_PROTOCOL", "SASL_PLAINTEXT")
KAFKA_SASL_MECHANISM = os.environ.get("KAFKA_SASL_MECHANISM", "SCRAM-SHA-512")
MAX_MESSAGES = int(os.environ.get("DEBUG_MAX_MESSAGES", "3"))


def _safe_deserialize(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return raw.decode("utf-8", errors="replace")


consumer = None
try:
    consumer = KafkaConsumer(
        KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=None,                   # no group — no committed offsets
        auto_offset_reset="latest",      # only recent messages; avoid replaying full history
        consumer_timeout_ms=8000,
        value_deserializer=_safe_deserialize,
        security_protocol=KAFKA_SECURITY_PROTOCOL,
        sasl_mechanism=KAFKA_SASL_MECHANISM,
        sasl_plain_username=KAFKA_USER,
        sasl_plain_password=KAFKA_PASS,
    )
    print(f"Consumer created — listening on {KAFKA_TOPIC}", flush=True)
    count = 0
    for msg in consumer:
        count += 1
        value = msg.value
        if isinstance(value, dict):
            value = json.dumps(value)
        print(f"MSG partition={msg.partition} offset={msg.offset}: {str(value)[:200]}", flush=True)
        if count >= MAX_MESSAGES:
            break
    print(f"Total: {count} messages", flush=True)
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
    sys.exit(1)
finally:
    if consumer is not None:
        consumer.close()
