"""data.access consumer half — Kafka -> "DataAccessLog".

Lives beside the existing auth-event consumer in this service rather than in a
new one: same broker, same database, same operational surface. What differs is
the topic and the destination table, because the two record different things at
very different volumes (see the ORM docstring).

Insert-only and idempotent. Kafka is at-least-once, so a redelivered event must
collide on `event_id` and be skipped rather than double-count in a report —
`ON CONFLICT DO NOTHING` makes that the database's job instead of the
consumer's.
"""
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from libs.basemodel.services_databaseconnector.postgres_orm.audit_access_orm import DataAccessLogORM
from services.platform_workers.common.db import set_tenant

log = logging.getLogger("platform-workers.audit.data_access")

TOPIC = "audit.data.access"

_REQUIRED = ("event_id", "tenant_id", "user_id", "action", "decision")


def _uuid(value: Any, field: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def _occurred_at(value: Any) -> datetime:
    ts = float(value or 0)
    if ts <= 0:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def insert_data_access(session: Session, event: dict[str, Any]) -> bool:
    """Write one event. Returns False when it was a duplicate or unusable.

    A malformed event is dropped with a warning rather than raising: one bad
    producer must not stall the partition and stop every other tenant's audit
    trail from being written. The trade-off is that a persistently broken
    producer loses records silently, so the warning is the thing to alert on.
    """
    missing = [f for f in _REQUIRED if not event.get(f)]
    if missing:
        log.warning("data.access | dropping event missing %s", ",".join(missing))
        return False

    stmt = (
        pg_insert(DataAccessLogORM)
        .values(
            event_id=_uuid(event["event_id"], "event_id"),
            tenant_id=_uuid(event["tenant_id"], "tenant_id"),
            user_id=event["user_id"],
            user_roles=list(event.get("user_roles") or []),
            auth_source=event.get("auth_source", "") or "",
            action=event["action"],
            decision=event["decision"],
            target=event.get("target", "") or "",
            target_objects=list(event.get("target_objects") or []),
            statement_redacted=event.get("statement_redacted", "") or "",
            statement_sha256=event.get("statement_sha256", "") or "",
            metric_ref=event.get("metric_ref", "") or "",
            row_count=event.get("row_count"),
            latency_ms=event.get("latency_ms"),
            reason=event.get("reason", "") or "",
            session_id=event.get("session_id", "") or "",
            agent_id=event.get("agent_id", "") or "",
            event_metadata=event.get("metadata") or {},
            occurred_at=_occurred_at(event.get("occurred_at")),
        )
        .on_conflict_do_nothing(index_elements=[DataAccessLogORM.event_id])
    )
    set_tenant(session, event["tenant_id"])
    result = session.execute(stmt)
    # rowcount 0 means ON CONFLICT skipped it — a redelivery, not a failure.
    return bool(result.rowcount)
