"""Audit worker (ex audit-consumer + audit-bridge).

Main thread: Kafka ``audit.data.access`` -> ``"DataAccessLog"`` (who ran which
query). Delivery is at-least-once — the offset is committed only after the
PostgreSQL COMMIT, and a failed batch is left uncommitted and retried.

Daemon thread: the Keycloak auth-event bridge (``bridge.py``) polling the
Admin API and writing ``audit_logs`` directly. If that thread ever dies, the
consumer loop notices on its next poll and restarts it after a short delay.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from services.platform_workers.common import db as worker_db
from services.platform_workers.common import kafka as kafka_factory
from services.platform_workers.common.settings import KafkaSettings, KeycloakSettings, PostgresSettings
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.orm import Session, sessionmaker

from services.platform_workers.workers.audit import data_access
from services.platform_workers.workers.audit.bridge import Bridge

log = logging.getLogger("platform-workers.audit")

BRIDGE_RESTART_DELAY_SECONDS = 5


class Settings(BaseSettings):
    """Audit-worker knobs — same env names the two containers used."""

    model_config = SettingsConfigDict(extra="ignore")

    data_access_topic: str = Field(
        default=data_access.TOPIC, validation_alias="KAFKA_DATA_ACCESS_TOPIC",
    )
    group_id: str = Field(default="audit-consumer-group", validation_alias="KAFKA_GROUP_ID")
    poll_interval_seconds: int = Field(default=10, validation_alias="POLL_INTERVAL_SECONDS")
    # Mount /data as a Docker volume so state survives container restarts.
    state_file: str = Field(default="/data/last_event_time.txt", validation_alias="STATE_FILE")


class BridgeSupervisor:
    """Keeps the bridge thread alive across unexpected deaths."""

    def __init__(self, bridge: Bridge, restart_delay: float = BRIDGE_RESTART_DELAY_SECONDS) -> None:
        self.bridge = bridge
        self.restart_delay = restart_delay
        self.thread: threading.Thread | None = None
        self._died_at: float | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self.bridge.run_forever, name="audit-bridge", daemon=True)
        self.thread.start()
        self._died_at = None

    def check(self, now: float | None = None) -> None:
        """Call from the main loop; restarts a dead thread after restart_delay."""
        if self.thread is None or self.thread.is_alive():
            return
        now = time.time() if now is None else now
        if self._died_at is None:
            self._died_at = now
            log.error("audit bridge thread died — restarting in %ss", self.restart_delay)
            return
        if now - self._died_at >= self.restart_delay:
            log.warning("restarting audit bridge thread")
            self.start()


def consume_batch(SessionLocal: sessionmaker[Session], records: dict[Any, list[Any]]) -> bool:
    """Write one poll's worth of records. Returns ``False`` when the batch
    must not be committed to Kafka."""
    for _tp, messages in records.items():
        for msg in messages:
            event = msg.value
            if not isinstance(event, dict):
                log.warning("Skipping non-dict message offset=%d", msg.offset)
                continue
            try:
                with SessionLocal() as session:
                    inserted = data_access.insert_data_access(session, event)
                    session.commit()
                if not inserted:
                    log.debug("Skipping duplicate event offset=%d", msg.offset)
            except Exception as db_err:  # noqa: BLE001 — any DB failure stops the batch
                log.error("DB write failed for event %s: %s", event.get("action"), db_err)
                # stop batch — offsets will NOT be committed; message will be retried
                return False
    return True


def run() -> None:
    from libs.shared.observability import setup_observability

    setup_observability("platform-workers-audit")

    settings = Settings()
    pg_settings = PostgresSettings()

    supervisor = BridgeSupervisor(Bridge(
        keycloak=KeycloakSettings(),
        postgres=pg_settings,
        state_file=settings.state_file,
        poll_interval=settings.poll_interval_seconds,
    ))
    supervisor.start()

    SessionLocal = worker_db.make_session_factory(pg_settings)
    consumer = kafka_factory.consumer(
        [settings.data_access_topic], settings.group_id,
        auto_offset_reset="earliest", settings=KafkaSettings(),
    )
    assert consumer is not None  # required=True raises instead of returning None

    log.info("Consuming from %s ...", settings.data_access_topic)
    while True:
        try:
            supervisor.check()
            records = consumer.poll(timeout_ms=2000)
            if not records:
                continue

            ok = consume_batch(SessionLocal, records)
            if ok:
                consumer.commit()
                log.debug("Committed offsets for %d partitions", len(records))
            else:
                log.warning("Batch had failures — offsets not committed, will retry")

        except Exception as e:  # noqa: BLE001 — the loop must outlive any single failure
            log.error("Consumer error: %s", e)
            time.sleep(5)
