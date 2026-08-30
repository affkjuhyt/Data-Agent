"""Keycloak auth-event bridge (ex audit-bridge) — runs as a thread of the audit worker.

Polls the Keycloak Admin events API every POLL_INTERVAL_SECONDS and writes
each event straight into ``audit_logs`` through ``auth_events.insert_event``.
The ``audit.auth.events`` Kafka topic that used to sit between the poll and
the insert is gone: the bridge and the consumer live in one process now, the
insert is already idempotent on ``source_event_id``, and a Kafka hop bought
nothing but a second principal, an ACL and a JAAS user to rotate.

State: the last-seen event timestamp (Keycloak epoch millis) is persisted to
STATE_FILE with an atomic write so a restart resumes where it left off.
Keycloak admin access goes through ``python-keycloak``, which refreshes the
admin token itself and retries once on 401.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

from common import pg
from common.settings import KeycloakSettings, PostgresSettings
from keycloak import KeycloakAdmin, KeycloakOpenIDConnection
from keycloak.exceptions import KeycloakError

from libs.shared.atomic_io import atomic_write_text
from workers.audit import auth_events

log = logging.getLogger("platform-workers.audit.bridge")

# Keycloak's events endpoint pages at 200 by default; the dateFrom filter is
# pushed a minute back so an event landing exactly on the boundary is not lost.
PAGE_SIZE = 200
DATE_FROM_SLACK_SECONDS = 60


def load_last_ts(state_file: str) -> int:
    try:
        with open(state_file, encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        # Default: 10 minutes ago (milliseconds, Keycloak epoch)
        return (int(time.time()) - 600) * 1000


def save_last_ts(state_file: str, ts: int) -> None:
    atomic_write_text(state_file, str(ts))


def _date_from(since_ms: int) -> str:
    return datetime.fromtimestamp(
        max(0, since_ms / 1000 - DATE_FROM_SLACK_SECONDS), tz=UTC
    ).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def keycloak_admin(settings: KeycloakSettings) -> KeycloakAdmin:
    """Admin client authenticated against ``master`` (``user_realm_name``)
    and pointed at the realm whose events are read (``realm_name``)."""
    connection = KeycloakOpenIDConnection(
        server_url=settings.url,
        username=settings.admin,
        password=settings.admin_password,
        realm_name=settings.realm,
        user_realm_name="master",
        client_id="admin-cli",
        verify=True,
        timeout=10,
    )
    return KeycloakAdmin(connection=connection)


def poll_events(admin: KeycloakAdmin, since_ms: int) -> list[dict[str, Any]]:
    """Events newer than ``since_ms``, oldest first."""
    events = admin.get_events(query={"first": 0, "max": PAGE_SIZE, "dateFrom": _date_from(since_ms)})
    fresh = [e for e in events if isinstance(e, dict) and e.get("time", 0) > since_ms]
    return sorted(fresh, key=lambda e: e.get("time", 0))


def write_events(conn: Any, events: list[dict[str, Any]]) -> int:
    """Insert a batch in one transaction; returns how many were new."""
    inserted = 0
    with conn.cursor() as cur:
        for event in events:
            if auth_events.insert_event(cur, event):
                inserted += 1
                log.info(
                    "auth event type=%-30s user=%s",
                    event.get("type", "?"), event.get("userId", "anonymous"),
                )
    conn.commit()
    return inserted


class Bridge:
    """One poll cycle at a time; ``run_forever`` never raises, so the thread
    only dies on something outside this loop (the supervisor restarts it)."""

    def __init__(
        self,
        *,
        keycloak: KeycloakSettings,
        postgres: PostgresSettings,
        state_file: str,
        poll_interval: int,
        stop: threading.Event | None = None,
    ) -> None:
        self.keycloak = keycloak
        self.postgres = postgres
        self.state_file = state_file
        self.poll_interval = poll_interval
        self.stop = stop or threading.Event()
        self._admin: KeycloakAdmin | None = None
        self._conn: Any = None

    def _get_admin(self) -> KeycloakAdmin:
        if self._admin is None:
            self._admin = keycloak_admin(self.keycloak)
        return self._admin

    def _get_conn(self) -> Any:
        if self._conn is None:
            self._conn = pg.connect(self.postgres)
        return self._conn

    def tick(self) -> int:
        """Poll once and persist; returns the number of events written."""
        last_ts = load_last_ts(self.state_file)
        try:
            new_events = poll_events(self._get_admin(), last_ts)
        except KeycloakError:
            # Any auth/permission failure invalidates the cached client — the
            # next tick re-authenticates from the admin credentials.
            self._admin = None
            raise
        if not new_events:
            return 0

        try:
            inserted = write_events(self._get_conn(), new_events)
        except Exception:
            conn, self._conn = self._conn, None
            if conn is not None:
                for step in (conn.rollback, conn.close):
                    with contextlib.suppress(Exception):
                        step()
            raise

        # Only advance the cursor once the batch is committed: a crash between
        # commit and this write replays the batch, which insert_event dedups.
        save_last_ts(self.state_file, max(e.get("time", 0) for e in new_events))
        log.info("Wrote %d/%d auth event(s) to audit_logs", inserted, len(new_events))
        return inserted

    def run_forever(self) -> None:
        log.info(
            "audit bridge started realm=%s poll=%ss state=%s",
            self.keycloak.realm, self.poll_interval, self.state_file,
        )
        while not self.stop.is_set():
            try:
                self.tick()
            except KeycloakError as e:
                log.error("Keycloak API error: %s", e)
            except Exception as e:  # noqa: BLE001 — the bridge must outlive any single failure
                log.error("Bridge error: %s", e)
            self.stop.wait(self.poll_interval)
