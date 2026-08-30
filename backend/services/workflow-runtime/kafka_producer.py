import datetime
import json
import logging
import os
import threading
import uuid
from typing import Any, Optional

from kafka import KafkaProducer

logger = logging.getLogger(__name__)

_KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
_KAFKA_SASL_USER = os.environ.get("KAFKA_SASL_USERNAME", "")
_KAFKA_SASL_PASS = os.environ.get("KAFKA_SASL_PASSWORD", "")
_KAFKA_SECURITY_PROTOCOL = os.environ.get("KAFKA_SECURITY_PROTOCOL", "SASL_PLAINTEXT")
_KAFKA_SASL_MECHANISM = os.environ.get("KAFKA_SASL_MECHANISM", "PLAIN")

_producer: Optional[KafkaProducer] = None
_lock = threading.Lock()


def _json_default(obj: Any) -> Any:
    """tenant_id/agent_id/conversation_id/message_id often arrive as raw UUID
    objects from SQLAlchemy/asyncpg — json.dumps can't serialize those by default."""
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


async def init_kafka_producer() -> None:
    """Connect lazily/fire-and-forget — a Kafka outage must never block the
    request/response cycle. produce_event() degrades to a no-op + log if
    the broker is unreachable."""
    global _producer
    try:
        kwargs: dict[str, Any] = dict(
            bootstrap_servers=_KAFKA_BOOTSTRAP,
            value_serializer=lambda v: json.dumps(v, default=_json_default, ensure_ascii=False).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
            acks="all",
            retries=3,
        )
        if _KAFKA_SASL_USER:
            kwargs.update(
                security_protocol=_KAFKA_SECURITY_PROTOCOL,
                sasl_mechanism=_KAFKA_SASL_MECHANISM,
                sasl_plain_username=_KAFKA_SASL_USER,
                sasl_plain_password=_KAFKA_SASL_PASS,
            )
        with _lock:
            _producer = KafkaProducer(**kwargs)
        logger.info("kafka producer | connected %s", _KAFKA_BOOTSTRAP)
    except Exception as e:
        logger.warning("kafka producer | unavailable, events will be dropped: %s", e)
        _producer = None


async def close_kafka_producer() -> None:
    global _producer
    if _producer:
        try:
            _producer.flush(timeout=5)
            _producer.close(timeout=5)
        except Exception as e:
            logger.warning("kafka producer | close failed: %s", e)
        _producer = None


def produce_event(topic: str, key: Optional[str], value: dict[str, Any]) -> None:
    """Fire-and-forget — does not block on broker ack, matches release-worker's produce_event()."""
    if not _producer:
        logger.warning("kafka producer | not connected, dropping event for topic=%s", topic)
        return
    try:
        _producer.send(topic, key=key, value=value)
        logger.info("kafka producer | sent topic=%s key=%s", topic, key)
    except Exception as e:
        logger.error("kafka producer | send failed topic=%s: %s", topic, e)
