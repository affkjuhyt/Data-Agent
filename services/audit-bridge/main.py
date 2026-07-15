"""
Keycloak Auth Event Bridge → Kafka

Polls Keycloak Admin REST API for auth events and publishes them to
the audit.auth.events Kafka topic. The consumer (audit-consumer service)
reads from that topic and writes to the audit_logs PostgreSQL table.

Delivery: at-least-once (Kafka acks=all, manual offset commit).
State: last-seen event timestamp persisted to /tmp/last_event_time.txt.
"""
from typing import Any, cast
import os
import json
import logging
import time

import requests
from kafka import KafkaProducer
from kafka.errors import NoBrokersAvailable

from shared.atomic_io import atomic_write_text
from shared.observability import setup_observability

setup_observability("audit-bridge")
log = logging.getLogger("audit-bridge")

KC_BASE = os.environ.get("KEYCLOAK_URL", "http://keycloak-lb:8080")
REALM = os.environ.get("KEYCLOAK_REALM", "aeroflow")
KC_ADMIN = os.environ.get("KEYCLOAK_ADMIN", "admin")
KC_PASS = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "admin")
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
KAFKA_SASL_USER = os.environ.get("KAFKA_SASL_USERNAME", "")
KAFKA_SASL_PASS = os.environ.get("KAFKA_SASL_PASSWORD", "")
KAFKA_SECURITY_PROTOCOL = os.environ.get("KAFKA_SECURITY_PROTOCOL", "SASL_PLAINTEXT")
KAFKA_SASL_MECHANISM = os.environ.get("KAFKA_SASL_MECHANISM", "PLAIN")
TOPIC = os.environ.get("KAFKA_TOPIC", "audit.auth.events")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "10"))
# Mount /data as a Docker volume so state survives container restarts.
STATE_FILE = os.environ.get("STATE_FILE", "/data/last_event_time.txt")


def wait_for_kafka(bootstrap: str, retries: int = 30, delay: int = 5) -> KafkaProducer:
    for attempt in range(1, retries + 1):
        try:
            kwargs: dict[str, Any] = dict(
                bootstrap_servers=bootstrap,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                acks="all",
                retries=5,
            )
            if KAFKA_SASL_USER:
                kwargs.update(
                    security_protocol=KAFKA_SECURITY_PROTOCOL,
                    sasl_mechanism=KAFKA_SASL_MECHANISM,
                    sasl_plain_username=KAFKA_SASL_USER,
                    sasl_plain_password=KAFKA_SASL_PASS,
                )
            producer = KafkaProducer(**kwargs)
            log.info("Kafka connected")
            return producer
        except NoBrokersAvailable:
            log.warning(f"Kafka not ready, attempt {attempt}/{retries}, retry in {delay}s")
            time.sleep(delay)
    raise RuntimeError("Kafka unavailable after retries")


_TOKEN_URL = f"{KC_BASE}/realms/master/protocol/openid-connect/token"
# Refresh this many seconds before the token actually expires, so a request
# never races the expiry boundary.
TOKEN_REFRESH_MARGIN = 30

# Cached admin token state. Deadlines use a monotonic clock (immune to NTP/system
# clock jumps). Reusing the token across poll cycles avoids re-authenticating with
# username/password every POLL_INTERVAL — we only hit the token endpoint when the
# access token is near expiry, and prefer the (cheaper) refresh_token grant.
_token: str | None = None
_token_deadline: float = 0.0
_refresh_token: str | None = None
_refresh_deadline: float = 0.0


def _post_token(data: dict[str, Any]) -> dict[str, Any]:
    r = requests.post(_TOKEN_URL, data={"client_id": "admin-cli", **data}, timeout=10)
    r.raise_for_status()
    return cast(dict[str, Any], r.json())


def _store_token(tok: dict[str, Any]) -> None:
    global _token, _token_deadline, _refresh_token, _refresh_deadline
    now = time.monotonic()
    _token = tok["access_token"]
    _token_deadline = now + max(1, tok.get("expires_in", 60) - TOKEN_REFRESH_MARGIN)
    _refresh_token = tok.get("refresh_token")
    # refresh_expires_in == 0 means "non-expiring" in some Keycloak configs.
    refresh_ttl = tok.get("refresh_expires_in", 1800)
    _refresh_deadline = now + (refresh_ttl - TOKEN_REFRESH_MARGIN if refresh_ttl else 86400)


def get_admin_token(force: bool = False) -> str:
    """Return a valid admin access token, refreshing only when needed.

    `force=True` discards the cached access token (used after a 401) but still
    tries the refresh_token grant before falling back to a full password grant.
    """
    now = time.monotonic()
    if not force and _token and now < _token_deadline:
        return _token

    if _refresh_token and now < _refresh_deadline:
        try:
            _store_token(_post_token({"grant_type": "refresh_token", "refresh_token": _refresh_token}))
            assert _token is not None  # _store_token just set the global
            return _token
        except requests.HTTPError:
            log.warning("token refresh failed; falling back to password grant")

    _store_token(_post_token({
        "grant_type": "password",
        "username": KC_ADMIN,
        "password": KC_PASS,
    }))
    assert _token is not None  # _store_token just set the global
    return _token


def load_last_ts() -> int:
    try:
        return int(open(STATE_FILE).read().strip())
    except Exception:
        # Default: 10 minutes ago (milliseconds, Keycloak epoch)
        return (int(time.time()) - 600) * 1000


def save_last_ts(ts: int) -> None:
    atomic_write_text(STATE_FILE, str(ts))


def poll_events(token: str, since_ms: int) -> list[Any]:
    # dateFrom narrows the server-side query so we don't fetch events we'll filter out anyway.
    # Keycloak expects a date string; subtract 1 minute to avoid dropping events at boundary.
    from datetime import datetime, timezone, timedelta
    date_from = datetime.fromtimestamp(
        max(0, since_ms / 1000 - 60), tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    r = requests.get(
        f"{KC_BASE}/admin/realms/{REALM}/events",
        headers={"Authorization": f"Bearer {token}"},
        params={"first": 0, "max": 200, "dateFrom": date_from},
        timeout=10,
    )
    r.raise_for_status()
    return [e for e in r.json() if e.get("time", 0) > since_ms]


def main() -> None:
    producer = wait_for_kafka(KAFKA_BOOTSTRAP)

    while True:
        try:
            last_ts = load_last_ts()
            try:
                new_events = poll_events(get_admin_token(), last_ts)
            except requests.HTTPError as e:
                # Token revoked mid-cycle (e.g. Keycloak restart) → force re-auth, retry once.
                if e.response is not None and e.response.status_code == 401:
                    log.warning("Keycloak rejected token (401); forcing re-auth")
                    new_events = poll_events(get_admin_token(force=True), last_ts)
                else:
                    raise

            if new_events:
                for event in sorted(new_events, key=lambda e: e.get("time", 0)):
                    # Partition by userId for ordered per-user delivery; use event id when
                    # available so the consumer can deduplicate on source_event_id.
                    msg_key = (event.get("id") or event.get("userId") or "")
                    producer.send(TOPIC, key=msg_key.encode("utf-8"), value=event)
                    log.info(
                        "published type=%-30s user=%s",
                        event.get("type", "?"),
                        event.get("userId", "anonymous"),
                    )
                producer.flush()
                max_ts = max(e.get("time", 0) for e in new_events)
                save_last_ts(max_ts)
                log.info("Flushed %d events to %s", len(new_events), TOPIC)

        except requests.HTTPError as e:
            log.error("Keycloak API error: %s", e)
        except Exception as e:
            log.error("Bridge error: %s", e)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
