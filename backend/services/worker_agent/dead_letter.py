"""An outbox event that died is told to the organisation's admins (PL-02).

The relay (libs/dataagent_db/outbox_relay.py) leaves an event dead after five failed tries and
writes `outbox.dead_letter` on integration.outbox: {schema, event_id, event_type, aggregate_type,
aggregate_id, attempts, last_error}. Nothing retries it after that; a person decides, from the
notice and integration.v_outbox_dead, whether to fix the cause and replay it. The notice names the
event, never its payload, and the error only as the kind of failure (notify.notification.body holds
no data), so the full error stays on the outbox row where an admin reads it.
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import func, literal, or_, select

from libs import metrics
from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import notify, org
from services.worker_agent.events import Event

logger = logging.getLogger(__name__)

EVENT_TYPE = "outbox.dead_letter"


def _kind_of(error: str) -> str:
    """The exception's name the relay put first ("TimeoutError: …"), or a generic word."""
    head = error.split(":", 1)[0].strip()
    return head if head.isidentifier() else "error"


async def handle(event: Event) -> None:
    dead = event.payload
    metrics.OUTBOX_DEAD.labels(str(dead.get("schema")), str(dead.get("event_type"))).inc()
    async with scoped_session("svc_notify", event.org_id, None) as session:
        now = datetime.datetime.now(datetime.UTC)
        admins = (
            await session.scalars(
                select(org.UserRole.user_id)
                .join(org.Role, org.Role.id == org.UserRole.role_id)
                .join(org.AppUser, org.AppUser.id == org.UserRole.user_id)
                .where(
                    org.UserRole.org_id == event.org_id,
                    org.AppUser.status == "active",
                    literal("admin") == func.any(org.Role.capabilities),
                    or_(org.UserRole.valid_until.is_(None), org.UserRole.valid_until > now),
                )
                .distinct()
            )
        ).all()
        if not admins:
            logger.error(
                "dead letter | %s %s died and org %s has no admin to tell",
                dead.get("event_type"),
                dead.get("event_id"),
                event.org_id,
            )
            return
        for admin in admins:
            # One notice per admin and dead event, however often this event is redelivered.
            exists = await session.scalar(
                select(notify.Notification.id).where(
                    notify.Notification.source_ref == event.aggregate_id,
                    notify.Notification.user_id == admin,
                    notify.Notification.kind == "system",
                )
            )
            if exists is not None:
                continue
            session.add(
                notify.Notification(
                    id=uuid7(),
                    org_id=event.org_id,
                    user_id=admin,
                    kind="system",
                    title="Một sự kiện hệ thống đã ngừng xử lý",
                    body=(
                        f"{dead.get('event_type')} ({dead.get('schema')}) lỗi "
                        f"{dead.get('attempts')} lần ({_kind_of(str(dead.get('last_error', '')))}) "
                        "và không được thử lại nữa. Xem integration.v_outbox_dead."
                    ),
                    source_ref=event.aggregate_id,
                    priority="high",
                )
            )


HANDLERS = {EVENT_TYPE: handle}
