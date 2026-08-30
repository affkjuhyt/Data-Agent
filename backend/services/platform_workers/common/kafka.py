"""Kafka consumer/producer factories with broker wait built in.

Every worker used to carry its own ``for attempt in range(retries)`` loop
around ``KafkaConsumer(...)``; the loops differed only in how many attempts
and whether exhaustion raised (memory/audit) or returned ``None`` so cron
work could continue without a broker (schema-crawler's DDL trigger). Both
shapes are one factory here, with ``required`` selecting the behaviour and
tenacity doing the waiting.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import NoBrokersAvailable
from tenacity import (
    RetryError,
    Retrying,
    before_sleep_log,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from services.platform_workers.common.settings import KafkaSettings

log = logging.getLogger("platform-workers.kafka")

DEFAULT_ATTEMPTS = 30
DEFAULT_WAIT_SECONDS = 5


def _safe_json(raw: bytes) -> Any:
    """Value deserializer: a message that is not UTF-8 JSON becomes ``None``
    so the consumer loop can skip it instead of dying on the partition."""
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value).encode("utf-8")


def _retrying(attempts: int, wait_seconds: float) -> Retrying:
    return Retrying(
        retry=retry_if_exception_type(NoBrokersAvailable),
        wait=wait_fixed(wait_seconds),
        stop=stop_after_attempt(max(1, attempts)),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=False,
    )


def consumer(
    topics: Sequence[str],
    group_id: str,
    *,
    auto_offset_reset: str = "earliest",
    settings: KafkaSettings | None = None,
    required: bool = True,
    attempts: int = DEFAULT_ATTEMPTS,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> KafkaConsumer | None:
    """A manual-commit JSON consumer subscribed to ``topics``.

    ``required=True`` raises ``RuntimeError`` once the broker wait is exhausted;
    ``required=False`` logs and returns ``None`` (the caller runs degraded).
    """
    settings = settings or KafkaSettings()
    sasl = settings.sasl_kwargs()

    def _build() -> KafkaConsumer:
        return KafkaConsumer(
            *topics,
            bootstrap_servers=settings.bootstrap_servers,
            group_id=group_id,
            auto_offset_reset=auto_offset_reset,
            enable_auto_commit=False,
            value_deserializer=_safe_json,
            consumer_timeout_ms=1000,
            **sasl,
        )

    try:
        built: KafkaConsumer = _retrying(attempts, wait_seconds)(_build)
    except RetryError as e:
        if required:
            raise RuntimeError("Kafka unavailable after retries") from e
        log.warning("Kafka unavailable after %d attempt(s) — continuing without a consumer", attempts)
        return None
    log.info("Kafka consumer ready, subscribed to %s", ", ".join(topics))
    return built


def producer(
    settings: KafkaSettings | None = None,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> KafkaProducer:
    """A JSON producer with ``acks=all``; raises ``RuntimeError`` if no broker
    answers within the wait budget."""
    settings = settings or KafkaSettings()
    sasl = settings.sasl_kwargs()

    def _build() -> KafkaProducer:
        return KafkaProducer(
            bootstrap_servers=settings.bootstrap_servers,
            value_serializer=_json_bytes,
            acks="all",
            retries=5,
            **sasl,
        )

    try:
        built: KafkaProducer = _retrying(attempts, wait_seconds)(_build)
    except RetryError as e:
        raise RuntimeError("Kafka unavailable after retries") from e
    log.info("Kafka producer ready")
    return built
