"""Keycloak auth events -> ``audit_logs`` (ex audit-consumer insert_event).

Called by the bridge thread directly now that the ``audit.auth.events`` Kafka
hop is gone; the write is idempotent on ``source_event_id`` so a re-poll after
a crash cannot double-count a login.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

log = logging.getLogger("platform-workers.audit.auth-events")


def insert_event(cur: Any, event: dict[str, Any], tenant_id: str | None) -> bool:
    """Insert event into audit_logs. Returns False if the event was already
    processed (dedup) or cannot be attributed to a tenant.

    ``tenant_id`` is the platform tenant of the user the event is about (the
    bridge reads it from the user's Keycloak ``tenant_id`` attribute, the same
    attribute the token's tenant claim comes from). "AuditLogs".tenant_id is NOT
    NULL, so an event without one (an unknown or deleted user, a client-only
    event) is skipped with a warning: raising instead would fail the whole
    batch, the bridge would never advance its cursor, and every later event
    would be stuck behind it.
    """
    source_event_id = event.get("id") or ""
    event_type = event.get("type", "UNKNOWN")
    user_id = event.get("userId") or "system"
    client_id = event.get("clientId") or ""
    status = "FAILED" if "ERROR" in event_type else "SUCCESS"

    if not tenant_id:
        log.warning(
            "auth event %s type=%s user=%s has no tenant — not written",
            source_event_id or "?", event_type, user_id,
        )
        return False

    # Dedup: skip if this source event was already written.
    # Partitioned tables can't have a global UNIQUE on source_event_id, so we
    # do a point-read first; the bridge is single-threaded so there's no race.
    if source_event_id:
        cur.execute(
            'SELECT 1 FROM "AuditLogs" WHERE source_event_id = %s LIMIT 1',
            (source_event_id,),
        )
        if cur.fetchone():
            return False

    # id is generated here: the initial schema gave "AuditLogs".id no server
    # default (the ORM's uuid4 default is Python-side only), so an INSERT that
    # left it out failed every time with a NOT NULL violation.
    cur.execute(
        """
        INSERT INTO "AuditLogs"
          (id, tenant_id, actor, actor_type, action, resource_type, resource_id,
           status, metadata, ip_address, source_event_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (str(uuid.uuid4()), tenant_id, user_id, "USER", event_type, "AUTH", client_id,
         status, json.dumps(event.get("details") or {}),
         event.get("ipAddress") or "", source_event_id or None),
    )
    return True
