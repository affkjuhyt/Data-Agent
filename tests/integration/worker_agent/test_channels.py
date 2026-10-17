"""The channel gateway tells people outside the app (worker-agent channels/): a request in Cần
bạn reaches its assignee in the app and on the email and Teams they bound and chose; a failed
channel hands over to another; a test and a binding's code go where they were asked."""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

import pytest

from services.worker_agent.channels import gateway
from services.worker_agent.channels.cards import Message
from services.worker_agent.channels.senders import SendFailed
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once

from .conftest import Seed

pytestmark = [pytest.mark.integration]

TEAMS = "https://prod-12.westus.logic.azure.com/workflows/abc/triggers/manual/paths/invoke"
EMAIL = "linh@helios.corp"


class Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[str, Message]] = []

    async def send(self, address: str, m: Message) -> None:
        if self.fail:
            raise SendFailed("Teams answered 502")
        self.sent.append((address, m))


def person(seed: Seed, channels: list[str]) -> tuple[uuid.UUID, uuid.UUID]:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Kênh thử")
    user = seed.row("org.app_user", org_id=org, external_id="kc-c", username="c", display_name="C")
    seed.row(
        "notify.notification_pref",
        org_id=org,
        user_id=user,
        kind="hitl",
        channels="{" + ",".join(channels) + "}",
    )
    for channel, address in (("teams", TEAMS), ("email", EMAIL)):
        seed.row(
            "notify.channel_binding",
            org_id=org,
            user_id=user,
            channel=channel,
            external_address=address,
            verified_at="2026-10-17T00:00:00Z",
            status="active",
        )
    return org, user


def opened(seed: Seed, org: uuid.UUID, user: uuid.UUID, *, risk: str = "high") -> uuid.UUID:
    request = seed.row(
        "inbox.hitl_request",
        org_id=org,
        kind="checkpoint",
        risk=risk,
        title="Báo giá vàng (demo): duyệt gửi kết quả",
        context=json.dumps({"preview": "Nguyễn Văn A, CCCD 012345678901"}),
        source_service="agent",
        assignee_id=user,
    )
    seed.row(
        "inbox.outbox",
        org_id=org,
        aggregate_type="hitl_request",
        aggregate_id=request,
        event_type="hitl.opened",
        payload=json.dumps({"hitl_request_id": str(request), "kind": "checkpoint", "risk": risk}),
    )
    return request


async def drain() -> None:
    for schema, types in (("inbox", gateway.INBOX_EVENTS), ("notify", gateway.NOTIFY_EVENTS)):
        source = OutboxTableSource(schema=schema, event_types=types)
        while await run_once(source, gateway.HANDLERS, batch=50):
            pass


def deliveries(seed: Seed, user: uuid.UUID) -> list[tuple[Any, ...]]:
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT channel, status, error FROM notify.channel_delivery WHERE user_id = %s"
            " ORDER BY created_at, channel",
            (str(user),),
        )
        return list(cur.fetchall())


def senders(monkeypatch: pytest.MonkeyPatch, **by_channel: Recorder) -> None:
    monkeypatch.setattr(gateway, "senders", lambda: dict(by_channel))


async def test_a_request_reaches_its_assignee_in_the_app_and_on_the_channels_they_chose(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, user = person(seed, ["in_app", "teams"])
    teams, email = Recorder(), Recorder()
    senders(monkeypatch, teams=teams, email=email)
    request = opened(seed, org, user)
    await drain()
    assert seed.one(
        "SELECT kind, title, link, priority FROM notify.notification WHERE source_ref = %s", request
    ) == ("hitl", "Báo giá vàng (demo): duyệt gửi kết quả", "/needs-you", "high")
    assert deliveries(seed, user) == [("teams", "sent", None)]
    # Teams only, as chosen; email is bound but not asked for.
    [(address, card)] = teams.sent
    assert address == TEAMS and email.sent == []
    # BR-68: the request's context (a name, a CCCD) never leaves the app; BR-67: a link only.
    assert "012345678901" not in card.text and "Nguyễn Văn A" not in card.text
    assert card.link.endswith("/needs-you")


async def test_a_failed_channel_hands_the_card_to_another_bound_one(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, user = person(seed, ["in_app", "teams"])
    email = Recorder()
    senders(monkeypatch, teams=Recorder(fail=True), email=email)
    opened(seed, org, user)
    await drain()
    assert deliveries(seed, user) == [
        ("teams", "failed", "Teams answered 502"),
        ("email", "sent", None),
    ]
    assert [a for a, _ in email.sent] == [EMAIL]


async def test_the_same_event_twice_tells_once(seed: Seed, monkeypatch: pytest.MonkeyPatch) -> None:
    org, user = person(seed, ["in_app", "email"])
    email = Recorder()
    senders(monkeypatch, teams=Recorder(), email=email)
    request = opened(seed, org, user)
    seed.row(
        "inbox.outbox",
        org_id=org,
        aggregate_type="hitl_request",
        aggregate_id=request,
        event_type="hitl.opened",
        payload=json.dumps({"hitl_request_id": str(request)}),
    )
    await drain()
    assert len(email.sent) == 1 and len(deliveries(seed, user)) == 1


async def test_a_test_goes_on_the_channel_asked_and_a_code_to_the_address_being_bound(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, user = person(seed, ["in_app"])
    teams, email = Recorder(), Recorder()
    senders(monkeypatch, teams=teams, email=email)
    seed.row(
        "notify.outbox",
        org_id=org,
        aggregate_type="notification_test",
        aggregate_id=uuid.uuid4(),
        event_type="notification.test_requested",
        payload=json.dumps({"user_id": str(user), "channels": ["teams"]}),
    )
    monkeypatch.setenv("NOTIFY_VERIFICATION_SECRET", "s3cret")
    pending = seed.row(
        "notify.channel_binding",
        org_id=org,
        user_id=seed.row(
            "org.app_user", org_id=org, external_id="kc-d", username="d", display_name="D"
        ),
        channel="email",
        external_address="new@helios.corp",
        status="pending",
    )
    issued = datetime.datetime(2026, 10, 17, 12, 0, tzinfo=datetime.UTC)
    seed.row(
        "notify.outbox",
        org_id=org,
        aggregate_type="channel_binding",
        aggregate_id=pending,
        event_type="channel_binding.verification_requested",
        payload=json.dumps(
            {
                "binding_id": str(pending),
                "user_id": str(
                    seed.one("SELECT user_id FROM notify.channel_binding WHERE id = %s", pending)
                ),
                "channel": "email",
                "issued_at": issued.isoformat(),
            }
        ),
    )
    await drain()
    assert [a for a, _ in teams.sent] == [TEAMS]
    [(address, card)] = email.sent
    code = gateway.verification_code(b"s3cret", pending, "new@helios.corp", issued)
    assert address == "new@helios.corp" and code in card.text and len(code) == 6


async def test_a_test_requested_twice_is_sent_once(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, user = person(seed, ["in_app"])
    teams = Recorder()
    senders(monkeypatch, teams=teams, email=Recorder())
    request = uuid.uuid4()
    for _ in range(2):
        seed.row(
            "notify.outbox",
            org_id=org,
            aggregate_type="notification_test",
            aggregate_id=request,
            event_type="notification.test_requested",
            payload=json.dumps({"user_id": str(user), "channels": ["teams"]}),
        )
    await drain()
    assert len(teams.sent) == 1
