"""Trung tâm thông báo, cài đặt nhận, liên kết kênh and the channel callback (UC-08-06, UC-08-07,
BR-67, BR-127) against PostgreSQL, on the organisation of test_monitor.py.

The callback has no user token: the channel gateway signs it (A1.2), and the card names its
delivery. A second organisation holds a delivery of its own, to show a card is only acted on in
the organisation it was sent from.
"""

from __future__ import annotations

import datetime
import json
import time
import uuid
from typing import Any

import httpx
import pytest

from services.api.modules.notify.channels import channel_secrets, sign, verification_code

from .conftest import Seed
from .test_monitor import Org, build, client, user

pytestmark = [pytest.mark.integration, pytest.mark.contract]

CALLBACK = {"teams": b"teams-secret", "zalo": b"zalo-secret", "email": b"email-secret"}
VERIFY = b"verify-secret"


class FakeSecrets:
    def callback_secret(self, kind: str) -> bytes | None:
        return CALLBACK.get(kind)

    def verification_secret(self) -> bytes | None:
        return VERIFY


class NoSecrets:
    def callback_secret(self, kind: str) -> bytes | None:
        return None

    def verification_secret(self) -> bytes | None:
        return None


def api_for(ntf: Org, secrets: Any = None) -> httpx.AsyncClient:
    return client(ntf, {channel_secrets: lambda: secrets or FakeSecrets()})


@pytest.fixture
def ntf(seed: Seed) -> Org:
    return build(seed)


def me(who: str) -> dict[str, str]:
    return {"Authorization": f"Bearer kc-{who}"}


# ---------------------------------------------------------------- notifications (UC-08-07)


async def test_my_notifications_are_mine_newest_first(ntf: Org) -> None:
    def note(who: str, at: str, kind: str = "alert", read: bool = False) -> uuid.UUID:
        return ntf.seed.row(
            "notify.notification",
            org_id=ntf.org,
            user_id=ntf.users[who],
            data_space_id=ntf.space,
            kind=kind,
            title=f"{kind} {at}",
            created_at=at,
            read_at=at if read else None,
        )

    old = note("owner", "2026-10-01T08:00:00Z", read=True)
    mid = note("owner", "2026-10-02T08:00:00Z", kind="hitl")
    new = note("owner", "2026-10-03T08:00:00Z")
    note("colleague", "2026-10-04T08:00:00Z")
    async with api_for(ntf) as api:
        mine = await api.get("/v1/me/notifications", headers=me("owner"))
        assert mine.status_code == 200, mine.text
        assert [n["id"] for n in mine.json()["items"]] == [str(new), str(mid), str(old)]
        unread = await api.get(
            "/v1/me/notifications", params={"unread": "true"}, headers=me("owner")
        )
        assert [n["id"] for n in unread.json()["items"]] == [str(new), str(mid)]
        read = await api.get(
            "/v1/me/notifications", params={"unread": "false"}, headers=me("owner")
        )
        assert [n["id"] for n in read.json()["items"]] == [str(old)]
        hitl = await api.get("/v1/me/notifications", params={"kind": "hitl"}, headers=me("owner"))
        assert [n["id"] for n in hitl.json()["items"]] == [str(mid)]
        first = await api.get("/v1/me/notifications", params={"page_size": 2}, headers=me("owner"))
        rest = await api.get(
            "/v1/me/notifications",
            params={"page_size": 2, "page_token": first.json()["next_page_token"]},
            headers=me("owner"),
        )
        assert [n["id"] for n in rest.json()["items"]] == [str(old)]
        assert rest.json()["next_page_token"] == ""
        nocap = await api.get("/v1/me/notifications", headers=ntf.headers("nocap"))
        assert nocap.status_code == 403
        anonymous = await api.get("/v1/me/notifications")
        assert anonymous.status_code == 401


# AC-08-07-01
async def test_prefs_start_from_the_defaults_and_put_replaces_them(ntf: Org) -> None:
    async with api_for(ntf) as api:
        defaults = await api.get("/v1/me/notification-prefs", headers=me("owner"))
        assert defaults.status_code == 200, defaults.text
        items = defaults.json()["items"]
        assert len(items) == 8 and all(p["id"] is None for p in items)
        assert {p["channels"][0] for p in items} == {"in_app"}

        put = await api.put(
            "/v1/me/notification-prefs",
            json={
                "prefs": [
                    {
                        "kind": "alert",
                        "channels": ["in_app", "teams"],
                        "quiet_hours": {"from": "22:00", "to": "06:00"},
                        "batch_mode": "15m",
                    },
                    {"kind": "hitl", "channels": ["in_app", "zalo"]},
                ]
            },
            headers=me("owner"),
        )
        assert put.status_code == 200, put.text
        prefs = {p["kind"]: p for p in put.json()["items"]}
        assert prefs["alert"]["channels"] == ["in_app", "teams"]
        assert prefs["alert"]["quiet_hours"] == {"from": "22:00", "to": "06:00"}
        assert prefs["alert"]["batch_mode"] == "15m" and prefs["alert"]["id"]
        assert prefs["digest"]["id"] is None
        alert_id = prefs["alert"]["id"]
        updated = ntf.events("notify", alert_id)
        # UC-08-07 step 3: the gateway sends a test on the channel just turned on.
        assert [e for e, _ in updated] == ["notification_pref.updated"]
        assert updated[0][1]["added_channels"] == ["teams"]

        # The same set again changes nothing; leaving hitl out resets it.
        same = await api.put(
            "/v1/me/notification-prefs",
            json={
                "prefs": [
                    {
                        "kind": "alert",
                        "channels": ["in_app", "teams"],
                        "quiet_hours": {"from": "22:00", "to": "06:00"},
                        "batch_mode": "15m",
                    }
                ]
            },
            headers=me("owner"),
        )
        assert same.status_code == 200
        assert {p["kind"]: p for p in same.json()["items"]}["hitl"]["id"] is None
        assert len(ntf.events("notify", alert_id)) == 1
        hitl_id = prefs["hitl"]["id"]
        assert [e for e, _ in ntf.events("notify", hitl_id)] == [
            "notification_pref.updated",
            "notification_pref.reset",
        ]
        theirs = await api.get("/v1/me/notification-prefs", headers=me("colleague"))
        assert all(p["id"] is None for p in theirs.json()["items"])
    assert (
        ntf.one(
            "SELECT count(*) FROM notify.notification_pref WHERE user_id = %s", ntf.users["owner"]
        )
        == 1
    )


async def test_notification_test_and_channel_rules_are_canonical(ntf: Org) -> None:
    async with api_for(ntf) as api:
        test = await api.post("/v1/me/notification-preferences:test", headers=me("owner"))
        assert test.status_code == 200, test.text
        assert test.json()["channels"] == ["in_app"]

        rules = await api.get("/v1/channel-rules", headers=me("owner"))
        assert rules.status_code == 200, rules.text
        assert {channel["key"] for channel in rules.json()["channels"]} >= {
            "in_app",
            "email",
            "teams",
        }
        assert {row["kind"] for row in rules.json()["rows"]} >= {"hitl", "alert"}


# AC-08-07-03
async def test_hitl_always_reaches_the_app(ntf: Org) -> None:
    async with api_for(ntf) as api:
        off = await api.put(
            "/v1/me/notification-prefs",
            json={"prefs": [{"kind": "hitl", "channels": ["teams"]}]},
            headers=me("owner"),
        )
        assert off.status_code == 422
        assert off.json()["code"] == "business_rule_violated"
        none = await api.put(
            "/v1/me/notification-prefs",
            json={"prefs": [{"kind": "hitl", "channels": []}]},
            headers=me("owner"),
        )
        assert none.status_code == 422
        for prefs in (
            [{"kind": "alert", "channels": ["in_app"]}, {"kind": "alert", "channels": ["email"]}],
            [{"kind": "alert", "channels": ["fax"]}],
            [
                {
                    "kind": "alert",
                    "channels": ["in_app"],
                    "quiet_hours": {"from": "25:00", "to": "06:00"},
                }
            ],
            [{"kind": "alert", "channels": ["in_app"], "batch_mode": "hourly"}],
        ):
            bad = await api.put(
                "/v1/me/notification-prefs", json={"prefs": prefs}, headers=me("owner")
            )
            assert bad.status_code == 422, prefs
        nocap = await api.put(
            "/v1/me/notification-prefs", json={"prefs": []}, headers=ntf.headers("nocap")
        )
        assert nocap.status_code == 403
    assert ntf.one("SELECT count(*) FROM notify.notification_pref WHERE org_id = %s", ntf.org) == 0


# ---------------------------------------------------------------- channel bindings (UC-08-06)


async def test_a_channel_is_bound_with_the_code_sent_to_its_address(ntf: Org) -> None:
    url = "/v1/me/channels/zalo"
    async with api_for(ntf) as api:
        empty = await api.put(url, json={}, headers=me("owner"))
        assert empty.status_code == 422
        early = await api.put(url, json={"verification_code": "123456"}, headers=me("owner"))
        assert early.status_code == 422
        sent = await api.put(url, json={"external_address": "0912000111"}, headers=me("owner"))
        assert sent.status_code == 200, sent.text
        binding = sent.json()
        assert binding["status"] == "pending" and binding["verified_at"] is None
        requested = ntf.events("notify", binding["id"])
        assert [e for e, _ in requested] == ["channel_binding.verification_requested"]
        # Neither the address nor the code travels in the event.
        assert "0912000111" not in json.dumps(requested[0][1])
        issued = datetime.datetime.fromisoformat(requested[0][1]["issued_at"])
        code = verification_code(VERIFY, uuid.UUID(binding["id"]), "0912000111", issued)

        wrong = await api.put(
            url,
            json={"verification_code": f"{(int(code) + 1) % 1_000_000:06d}"},
            headers=me("owner"),
        )
        assert wrong.status_code == 422
        other_address = await api.put(
            url,
            json={"external_address": "0912999999", "verification_code": code},
            headers=me("owner"),
        )
        assert other_address.status_code == 422
        # Another person cannot use the owner's code: it is not their binding.
        stranger = await api.put(url, json={"verification_code": code}, headers=me("colleague"))
        assert stranger.status_code == 422
        active = await api.put(url, json={"verification_code": code}, headers=me("owner"))
        assert active.status_code == 200, active.text
        assert active.json()["status"] == "active" and active.json()["verified_at"]
        assert active.json()["external_address"] == "0912000111"
        bad_channel = await api.put(
            "/v1/me/channels/fax", json={"external_address": "x"}, headers=me("owner")
        )
        assert bad_channel.status_code == 422
        nocap = await api.put(url, json={"external_address": "x"}, headers=ntf.headers("nocap"))
        assert nocap.status_code == 403
    assert [e for e, _ in ntf.events("notify", binding["id"])] == [
        "channel_binding.verification_requested",
        "channel_binding.activated",
    ]


async def test_an_expired_code_does_not_bind(ntf: Org) -> None:
    async with api_for(ntf) as api:
        sent = await api.put(
            "/v1/me/channels/teams", json={"external_address": "owner@bank.vn"}, headers=me("owner")
        )
        binding = sent.json()["id"]
        issued = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=20)
        ntf.run("UPDATE notify.channel_binding SET updated_at = %s WHERE id = %s", issued, binding)
        code = verification_code(VERIFY, uuid.UUID(binding), "owner@bank.vn", issued)
        late = await api.put(
            "/v1/me/channels/teams", json={"verification_code": code}, headers=me("owner")
        )
        assert late.status_code == 422
        ntf.run("UPDATE notify.channel_binding SET status = 'revoked' WHERE id = %s", binding)
        revoked = await api.put(
            "/v1/me/channels/teams", json={"verification_code": code}, headers=me("owner")
        )
        assert revoked.status_code == 409
    async with api_for(ntf, NoSecrets()) as api:
        unconfigured = await api.put(
            "/v1/me/channels/teams", json={"external_address": "owner@bank.vn"}, headers=me("owner")
        )
        assert unconfigured.status_code == 503


# ---------------------------------------------------------------- channel callback (UC-08-06)


def hitl(ntf: Org, org: uuid.UUID, space: uuid.UUID, who: uuid.UUID, **values: Any) -> uuid.UUID:
    values.setdefault("kind", "clarification")
    values.setdefault("risk", "low")
    return ntf.seed.row(
        "inbox.hitl_request",
        org_id=org,
        data_space_id=space,
        title="Chọn cách tính nợ xấu",
        source_service="worker-agent",
        assignee_id=who,
        **values,
    )


def delivery(
    ntf: Org, org: uuid.UUID, request: uuid.UUID, who: uuid.UUID, channel: str = "teams"
) -> uuid.UUID:
    return ntf.seed.row(
        "notify.channel_delivery",
        org_id=org,
        hitl_request_id=request,
        user_id=who,
        channel=channel,
        status="delivered",
    )


def bind(ntf: Org, org: uuid.UUID, who: uuid.UUID, channel: str, address: str) -> None:
    ntf.seed.row(
        "notify.channel_binding",
        org_id=org,
        user_id=who,
        channel=channel,
        external_address=address,
        status="active",
        verified_at="2026-10-01T00:00:00Z",
    )


def card(org: object, delivery_id: object, sender: str, decision: str = "answer") -> bytes:
    body = {
        "payload": {
            "org_id": str(org),
            "delivery_id": str(delivery_id),
            "sender": sender,
            "action": {"decision": decision, "option": "Theo dư nợ cuối kỳ"},
        }
    }
    return json.dumps(body).encode()


def signed(
    kind: str, body: bytes, secret: bytes | None = None, stamp: int | None = None
) -> dict[str, str]:
    ts = str(stamp if stamp is not None else int(time.time()))
    return {
        "Content-Type": "application/json",
        "X-Timestamp": ts,
        "X-Signature": sign(secret or CALLBACK[kind], ts, body),
    }


@pytest.fixture
def cards(ntf: Org) -> dict[str, uuid.UUID]:
    owner = ntf.users["owner"]
    bind(ntf, ntf.org, owner, "teams", "29:owner-teams")
    bind(ntf, ntf.org, owner, "email", "owner@bank.vn")
    low = hitl(ntf, ntf.org, ntf.space, owner)
    return {
        "low": delivery(ntf, ntf.org, low, owner),
        "low_hitl": low,
        "email": delivery(ntf, ntf.org, low, owner, "email"),
        "high": delivery(ntf, ntf.org, hitl(ntf, ntf.org, ntf.space, owner, risk="high"), owner),
        "conflict": delivery(
            ntf, ntf.org, hitl(ntf, ntf.org, ntf.space, owner, kind="conflict"), owner
        ),
        "medium": delivery(
            ntf,
            ntf.org,
            hitl(ntf, ntf.org, ntf.space, owner, kind="approve_proposal", risk="medium"),
            owner,
        ),
        "low_approval": delivery(
            ntf, ntf.org, hitl(ntf, ntf.org, ntf.space, owner, kind="approve_proposal"), owner
        ),
        "resolved": delivery(
            ntf,
            ntf.org,
            hitl(
                ntf,
                ntf.org,
                ntf.space,
                owner,
                status="answered",
                resolved_at="2026-10-01T00:00:00Z",
            ),
            owner,
        ),
    }


async def test_a_bad_or_missing_signature_is_refused(ntf: Org, cards: dict[str, uuid.UUID]) -> None:
    body = card(ntf.org, cards["low"], "29:owner-teams")
    url = "/v1/channels/teams/callback"
    async with api_for(ntf) as api:
        cases = [
            {"Content-Type": "application/json"},
            {**signed("teams", body), "X-Signature": "sha256=" + "0" * 64},
            signed("teams", body, secret=b"zalo-secret"),
            {**signed("teams", body), "X-Timestamp": "yesterday"},
            signed("teams", body, stamp=int(time.time()) - 600),
            # A user token is no substitute for the channel's signature.
            {"Content-Type": "application/json", "Authorization": "Bearer kc-owner"},
        ]
        for headers in cases:
            refused = await api.post(url, content=body, headers=headers)
            assert refused.status_code == 401, (headers, refused.text)
            assert refused.json()["code"] == "unauthenticated"
        # The body is what is signed: changing the decision breaks it.
        tampered = await api.post(
            url,
            content=card(ntf.org, cards["low"], "29:owner-teams", "reject"),
            headers=signed("teams", body),
        )
        assert tampered.status_code == 401
        unconfigured = await api.post(
            "/v1/channels/mobile_push/callback", content=body, headers=signed("teams", body)
        )
        assert unconfigured.status_code == 401
        unknown = await api.post(
            "/v1/channels/fax/callback", content=body, headers=signed("teams", body)
        )
        assert unknown.status_code == 404
    assert (
        ntf.one("SELECT status FROM notify.channel_delivery WHERE id = %s", cards["low"])
        == "delivered"
    )
    assert ntf.events("notify", cards["low"]) == []


# AC-08-06-01
async def test_a_signed_card_records_the_decision_for_the_linked_user(
    ntf: Org, cards: dict[str, uuid.UUID]
) -> None:
    body = card(ntf.org, cards["low"], "29:owner-teams")
    async with api_for(ntf) as api:
        done = await api.post(
            "/v1/channels/teams/callback", content=body, headers=signed("teams", body)
        )
        assert done.status_code == 204, done.text
        again = await api.post(
            "/v1/channels/teams/callback", content=body, headers=signed("teams", body)
        )
        assert again.status_code == 409 and again.json()["code"] == "invalid_state"
    assert (
        ntf.one("SELECT status FROM notify.channel_delivery WHERE id = %s", cards["low"]) == "acted"
    )
    assert ntf.one(
        "SELECT action_taken FROM notify.channel_delivery WHERE id = %s", cards["low"]
    ) == {
        "decision": "answer",
        "option": "Theo dư nợ cuối kỳ",
    }
    events = ntf.events("notify", cards["low"])
    assert [e for e, _ in events] == ["channel_delivery.acted"]
    payload = events[0][1]
    assert payload["hitl_request_id"] == str(cards["low_hitl"]) and payload["channel"] == "teams"
    assert payload["user_id"] == str(ntf.users["owner"]) and payload["decision"] == "answer"
    # The decision is inbox's to apply (the event), not this route's.
    assert (
        ntf.one("SELECT status FROM inbox.hitl_request WHERE id = %s", cards["low_hitl"]) == "open"
    )


async def test_the_card_is_acted_on_only_in_its_own_organisation(
    ntf: Org, cards: dict[str, uuid.UUID]
) -> None:
    other_org = ntf.seed.org()
    role = ntf.seed.row("org.role", org_id=other_org, code="ASK", name="Ask", capabilities=["ask"])
    other_user = user(ntf.seed, other_org, "other-org-user", role)
    other_space = ntf.seed.row(
        "org.data_space", org_id=other_org, code="DS-X", name="X", steward_id=other_user
    )
    bind(ntf, other_org, other_user, "teams", "29:owner-teams")
    theirs = delivery(ntf, other_org, hitl(ntf, other_org, other_space, other_user), other_user)
    async with api_for(ntf) as api:
        for org, delivery_id in (
            (ntf.org, theirs),
            (other_org, cards["low"]),
            (uuid.uuid4(), cards["low"]),
        ):
            body = card(org, delivery_id, "29:owner-teams")
            missing = await api.post(
                "/v1/channels/teams/callback", content=body, headers=signed("teams", body)
            )
            assert missing.status_code == 404, (org, missing.text)
        # Named with its own organisation, the other organisation's card works there.
        body = card(other_org, theirs, "29:owner-teams")
        ok = await api.post(
            "/v1/channels/teams/callback", content=body, headers=signed("teams", body)
        )
        assert ok.status_code == 204, ok.text
    assert (
        ntf.one("SELECT status FROM notify.channel_delivery WHERE id = %s", cards["low"])
        == "delivered"
    )
    assert ntf.events("notify", cards["low"]) == []
    assert ntf.one("SELECT org_id FROM notify.outbox WHERE aggregate_id = %s", theirs) == str(
        other_org
    )


async def test_only_the_linked_account_acts_on_the_card(
    ntf: Org, cards: dict[str, uuid.UUID]
) -> None:
    bind(ntf, ntf.org, ntf.users["colleague"], "teams", "29:colleague-teams")
    async with api_for(ntf) as api:
        for sender in ("29:colleague-teams", "29:nobody"):
            body = card(ntf.org, cards["low"], sender)
            refused = await api.post(
                "/v1/channels/teams/callback", content=body, headers=signed("teams", body)
            )
            assert refused.status_code == 403, sender
        # A card sent on Teams is not answered from Zalo.
        body = card(ntf.org, cards["low"], "29:owner-teams")
        zalo = await api.post(
            "/v1/channels/zalo/callback", content=body, headers=signed("zalo", body)
        )
        assert zalo.status_code == 404
    assert (
        ntf.one("SELECT status FROM notify.channel_delivery WHERE id = %s", cards["low"])
        == "delivered"
    )


# AC-08-06-02, AC-08-06-04
async def test_what_may_be_decided_outside_the_app(ntf: Org, cards: dict[str, uuid.UUID]) -> None:
    async with api_for(ntf) as api:
        email = card(ntf.org, cards["email"], "owner@bank.vn")
        by_mail = await api.post(
            "/v1/channels/email/callback", content=email, headers=signed("email", email)
        )
        assert by_mail.status_code == 403
        for name, decision in (
            ("high", "answer"),
            ("conflict", "answer"),
            ("medium", "approve"),
        ):
            body = card(ntf.org, cards[name], "29:owner-teams", decision)
            refused = await api.post(
                "/v1/channels/teams/callback", content=body, headers=signed("teams", body)
            )
            assert refused.status_code == 403, (name, refused.text)
        resolved = card(ntf.org, cards["resolved"], "29:owner-teams")
        late = await api.post(
            "/v1/channels/teams/callback", content=resolved, headers=signed("teams", resolved)
        )
        assert late.status_code == 409
        body = card(ntf.org, cards["low_approval"], "29:owner-teams", "approve")
        approved = await api.post(
            "/v1/channels/teams/callback", content=body, headers=signed("teams", body)
        )
        assert approved.status_code == 204, approved.text
    for name in ("email", "high", "conflict", "medium", "resolved"):
        assert ntf.events("notify", cards[name]) == [], name
    assert [e for e, _ in ntf.events("notify", cards["low_approval"])] == ["channel_delivery.acted"]


@pytest.mark.parametrize(
    ("channel", "address"),
    [
        # The worker would call a Teams URL: only Microsoft's https webhook hosts, never inside.
        ("teams", "https://openbao.dataagent-database.svc.cluster.local:8120/v1/sys/seal"),
        ("teams", "http://contoso.webhook.office.com/webhookb2/abc"),
        ("teams", "https://10.0.0.5/hook"),
        ("email", "not-an-email"),
    ],
)
async def test_an_address_the_gateway_must_not_send_to_is_refused(
    ntf: Org, channel: str, address: str
) -> None:
    async with api_for(ntf) as api:
        r = await api.put(
            f"/v1/me/channels/{channel}", json={"external_address": address}, headers=me("owner")
        )
    assert r.status_code == 422, r.text


@pytest.mark.parametrize(
    "address",
    [
        "https://prod-12.westus.logic.azure.com/workflows/abc/triggers/manual/paths/invoke",
        "owner@bank.vn",
    ],
)
async def test_a_teams_webhook_or_a_teams_account_is_bound(ntf: Org, address: str) -> None:
    async with api_for(ntf) as api:
        r = await api.put(
            "/v1/me/channels/teams", json={"external_address": address}, headers=me("owner")
        )
    assert r.status_code == 200, r.text
