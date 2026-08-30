"""Keycloak auth events -> ``audit_logs`` (ex audit-consumer insert_event).

Called by the bridge thread directly now that the ``audit.auth.events`` Kafka
hop is gone; the write is idempotent on ``source_event_id`` so a re-poll after
a crash cannot double-count a login.
"""
from __future__ import annotations

import json
from typing import Any


def get_tenant_id(cur: Any, realm_id: str) -> Any:
    """Look up platform tenant from Keycloak realmId via keycloak_realm_configs."""
    if not realm_id:
        return None
    cur.execute(
        "SELECT tenant_id FROM keycloak_realm_configs WHERE realm_name = %s LIMIT 1",
        (realm_id,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def insert_event(cur: Any, event: dict[str, Any]) -> bool:
    """Insert event into audit_logs. Returns False if event was already processed (dedup)."""
    source_event_id = event.get("id") or ""
    event_type = event.get("type", "UNKNOWN")
    user_id = event.get("userId") or "system"
    client_id = event.get("clientId") or ""
    status = "FAILED" if "ERROR" in event_type else "SUCCESS"
    tenant_id = get_tenant_id(cur, event.get("realmId") or "")

    # Dedup: skip if this source event was already written.
    # Partitioned tables can't have a global UNIQUE on source_event_id, so we
    # do a point-read first; the bridge is single-threaded so there's no race.
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
