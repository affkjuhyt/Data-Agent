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
import json
import logging
from typing import Any

log = logging.getLogger("audit-consumer.data_access")

TOPIC = "audit.data.access"

_INSERT = """
INSERT INTO "DataAccessLog"
  (event_id, tenant_id, user_id, user_roles, auth_source, action, decision,
   target, target_objects, statement_redacted, statement_sha256, metric_ref,
   row_count, latency_ms, reason, session_id, agent_id, metadata, occurred_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, to_timestamp(%s))
ON CONFLICT (event_id) DO NOTHING
"""

_REQUIRED = ("event_id", "tenant_id", "user_id", "action", "decision")


def insert_data_access(cur: Any, event: dict[str, Any]) -> bool:
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

    cur.execute(
        _INSERT,
        (
            event["event_id"],
            event["tenant_id"],
            event["user_id"],
            list(event.get("user_roles") or []),
            event.get("auth_source", "") or "",
            event["action"],
            event["decision"],
            event.get("target", "") or "",
            list(event.get("target_objects") or []),
            event.get("statement_redacted", "") or "",
            event.get("statement_sha256", "") or "",
            event.get("metric_ref", "") or "",
            event.get("row_count"),
            event.get("latency_ms"),
            event.get("reason", "") or "",
            event.get("session_id", "") or "",
            event.get("agent_id", "") or "",
            json.dumps(event.get("metadata") or {}),
            float(event.get("occurred_at") or 0) or None,
        ),
    )
    # rowcount 0 means ON CONFLICT skipped it — a redelivery, not a failure.
    return bool(cur.rowcount)
