"""Keycloak auth events -> ``audit_logs`` (ex audit-consumer insert_event).

Called by the bridge thread directly now that the ``audit.auth.events`` Kafka
hop is gone; the write is idempotent on ``source_event_id`` so a re-poll after
a crash cannot double-count a login.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from libs.basemodel.services_databaseconnector.postgres_orm.auth_release_orm import AuditLogsORM
from services.platform_workers.common.db import set_tenant

log = logging.getLogger("platform-workers.audit.auth-events")


def insert_event(session: Session, event: dict[str, Any], tenant_id: str | None) -> bool:
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
    try:
        set_tenant(session, tenant_id)
    except ValueError:
        # A malformed attribute is no tenant at all; raising would stall the
        # bridge's cursor behind this event (see above).
        log.warning(
            "auth event %s type=%s user=%s has tenant %r, not a UUID — not written",
            source_event_id or "?", event_type, user_id, tenant_id,
        )
        return False

    # Dedup: skip if this source event was already written.
    # Partitioned tables can't have a global UNIQUE on source_event_id, so we
    # do a point-read first; the bridge is single-threaded so there's no race.
    if source_event_id:
        existing = session.scalar(
            select(AuditLogsORM.id).where(AuditLogsORM.source_event_id == source_event_id).limit(1)
        )
        if existing is not None:
            return False

    session.add(
        AuditLogsORM(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            actor=user_id,
            actor_type="USER",
            action=event_type,
            resource_type="AUTH",
            resource_id=client_id,
            status=status,
            event_metadata=event.get("details") or {},
            ip_address=event.get("ipAddress") or "",
            source_event_id=source_event_id or None,
        )
    )
    return True
