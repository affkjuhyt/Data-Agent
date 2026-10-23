"""The channel gateway (UC-08-06, UC-08-07): telling people outside the app, by email and Teams.

- `hitl.opened` (inbox.outbox): the request's assignee gets it in the app (notify.notification,
  kind hitl) and on every external channel their preference for hitl names and they have bound
  and verified (notify.notification_pref, notify.channel_binding). Each send is a
  notify.channel_delivery row, sent or failed; a channel that fails hands over to another bound
  one (AC-08-06-03), and the app copy is always there.
- `notification.test_requested` (notify.outbox): one test message on each named, bound channel.
- `channel_binding.verification_requested` (notify.outbox): the six-digit code to the address
  being bound, computed as the api checks it (NOTIFY_VERIFICATION_SECRET).

What a card says is cards.py: a title and a link, never a decision (BR-67) nor personal data
(BR-68). Not yet: quiet hours and batching (BR-127), Zalo and mobile push, a request assigned to a
group or a capability (it is in Cần bạn for them), and copies marked handled when one is acted on.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import logging
import os
import uuid
from typing import Any, Protocol

from sqlalchemy import select

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import inbox, notify
from services.worker_agent.channels.cards import (
    RISK_TEXT,
    Message,
    hitl_card,
    probe_card,
    verification_card,
)
from services.worker_agent.channels.senders import SendFailed, Smtp, Teams
from services.worker_agent.events import Event, Handler

logger = logging.getLogger(__name__)

ROLE = "svc_notify"
HITL_OPENED = "hitl.opened"
TEST = "notification.test_requested"
VERIFY = "channel_binding.verification_requested"
# The channels this gateway sends on, and the order a failed one hands over to the next.
EXTERNAL = ("teams", "email")


class Sender(Protocol):
    async def send(self, address: str, m: Message) -> None: ...


def app_url() -> str:
    return os.environ.get("APP_BASE_URL", "http://localhost:30280")


def senders() -> dict[str, Sender]:
    out: dict[str, Sender] = {"teams": Teams()}
    smtp = Smtp.from_env()
    if smtp is not None:
        out["email"] = smtp
    return out


def verification_code(
    secret: bytes, binding_id: uuid.UUID, address: str, issued_at: datetime.datetime
) -> str:
    """The api's code for a binding (api notify/channels.verification_code): both must agree."""
    message = f"{binding_id}|{address}|{issued_at.astimezone(datetime.UTC).isoformat()}"
    digest = hmac.new(secret, message.encode(), hashlib.sha256).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


async def bindings(org_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, str]:
    async with scoped_session(ROLE, org_id, user_id) as session:
        rows = (
            await session.scalars(
                select(notify.ChannelBinding).where(
                    notify.ChannelBinding.user_id == user_id,
                    notify.ChannelBinding.status == "active",
                )
            )
        ).all()
    return {b.channel: b.external_address for b in rows}


async def deliver(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    wanted: list[str],
    bound: dict[str, str],
    m: Message,
    *,
    notification_id: uuid.UUID | None,
    hitl_request_id: uuid.UUID | None,
) -> list[tuple[str, str]]:
    """Send on each wanted channel; a failed one hands over to the next bound channel not tried
    yet. Returns (channel, status) per attempt; each is a channel_delivery row."""
    out: list[tuple[str, str]] = []
    available = senders()
    tried: set[str] = set()
    queue = [c for c in EXTERNAL if c in wanted and c in bound]
    while queue:
        channel = queue.pop(0)
        if channel in tried:
            continue
        tried.add(channel)
        error: str | None = None
        sender = available.get(channel)
        try:
            if sender is None:
                raise SendFailed(f"{channel} is not configured on this worker")
            await sender.send(bound[channel], m)
            status = "sent"
        except SendFailed as exc:
            status, error = "failed", str(exc)
        async with scoped_session(ROLE, org_id, user_id) as session:
            session.add(
                notify.ChannelDelivery(
                    id=uuid7(),
                    org_id=org_id,
                    notification_id=notification_id,
                    hitl_request_id=hitl_request_id,
                    user_id=user_id,
                    channel=channel,
                    status=status,
                    error=error,
                )
            )
        out.append((channel, status))
        if status == "failed":
            # AC-08-06-03: the card goes on another bound channel, if there is one not tried.
            queue += [c for c in EXTERNAL if c in bound and c not in tried and c not in queue]
        else:
            break
    return out


async def on_hitl_opened(event: Event) -> None:
    request_id = uuid.UUID(str(event.payload["hitl_request_id"]))
    async with scoped_session(ROLE, event.org_id) as session:
        request = await session.get(inbox.HitlRequest, request_id)
        if request is None or request.assignee_id is None:
            return
        user = request.assignee_id
        # Redelivered: the request was already told to its assignee.
        if await session.scalar(
            select(notify.Notification.id).where(
                notify.Notification.source_ref == request_id,
                notify.Notification.user_id == user,
                notify.Notification.kind == "hitl",
            )
        ):
            return
        pref = await session.scalar(
            select(notify.NotificationPref).where(
                notify.NotificationPref.user_id == user, notify.NotificationPref.kind == "hitl"
            )
        )
        wanted = list(pref.channels) if pref else ["in_app"]
        notification_id = uuid7()
        risk = request.risk or "medium"
        session.add(
            notify.Notification(
                id=notification_id,
                org_id=event.org_id,
                user_id=user,
                data_space_id=request.data_space_id,
                kind="hitl",
                title=request.title,
                body=f"Mức rủi ro: {RISK_TEXT.get(risk, risk)}.",
                link="/needs-you",
                source_ref=request_id,
                priority="high" if risk == "high" else "normal",
            )
        )
        title, kind = request.title, request.kind
    card = hitl_card(title=title, kind=kind, risk=risk, app_url=app_url())
    sent = await deliver(
        event.org_id,
        user,
        wanted,
        await bindings(event.org_id, user),
        card,
        notification_id=notification_id,
        hitl_request_id=request_id,
    )
    logger.info("hitl %s told to its assignee: %s", request_id, sent or "in app only")


async def on_test(event: Event) -> None:
    p: dict[str, Any] = event.payload
    user = uuid.UUID(str(p["user_id"]))
    wanted = [str(c) for c in p.get("channels") or []]
    notification_id = uuid7()
    async with scoped_session(ROLE, event.org_id, user) as session:
        # Redelivered: this test was already sent.
        if await session.scalar(
            select(notify.Notification.id).where(
                notify.Notification.source_ref == event.aggregate_id,
                notify.Notification.user_id == user,
                notify.Notification.kind == "system",
            )
        ):
            return
        session.add(
            notify.Notification(
                id=notification_id,
                org_id=event.org_id,
                user_id=user,
                kind="system",
                title="Thông báo thử",
                body="Thông báo thử đã được gửi tới các kênh bạn chọn.",
                source_ref=event.aggregate_id,
            )
        )
    bound = await bindings(event.org_id, user)
    for channel in wanted:
        if channel in bound and channel in EXTERNAL:
            # A test goes on the channel asked, with no hand-over: it is that channel being tried.
            await deliver(
                event.org_id,
                user,
                [channel],
                {channel: bound[channel]},
                probe_card(app_url=app_url()),
                notification_id=notification_id,
                hitl_request_id=None,
            )


async def on_verify(event: Event) -> None:
    p = event.payload
    secret = os.environ.get("NOTIFY_VERIFICATION_SECRET", "").encode()
    if not secret:
        logger.warning("channel verification: NOTIFY_VERIFICATION_SECRET is not set")
        return
    binding_id = uuid.UUID(str(p["binding_id"]))
    user = uuid.UUID(str(p["user_id"]))
    async with scoped_session(ROLE, event.org_id, user) as session:
        binding = await session.get(notify.ChannelBinding, binding_id)
        if binding is None or binding.status != "pending":
            return
        channel, address = binding.channel, binding.external_address
    issued = datetime.datetime.fromisoformat(str(p["issued_at"]))
    code = verification_code(secret, binding_id, address, issued)
    sender = senders().get(channel)
    if sender is None:
        logger.warning("channel verification: %s is not configured on this worker", channel)
        return
    try:
        await sender.send(address, verification_card(code, app_url=app_url()))
    except SendFailed as exc:
        logger.warning("channel verification %s could not be sent: %s", binding_id, exc)


HANDLERS: dict[str, Handler] = {HITL_OPENED: on_hitl_opened, TEST: on_test, VERIFY: on_verify}
# Which outbox each event is claimed from (main.sources).
INBOX_EVENTS = frozenset({HITL_OPENED})
NOTIFY_EVENTS = frozenset({TEST, VERIFY})
