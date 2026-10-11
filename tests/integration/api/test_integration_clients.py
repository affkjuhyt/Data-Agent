"""Tích hợp (UC-12-01, UC-12-03, UC-12-04) against PostgreSQL, with the Keycloak and OpenBao
ports faked on app.state: API clients, their usage, webhooks and delivery retries."""

from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from services.api.app import create_app
from services.api.config import Settings
from services.api.modules.integration import rules
from services.api.modules.integration.ports import IssuedCredentials, IssuedKey

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.security]

NOW = datetime.datetime.now(datetime.UTC)


@dataclass
class FakeKeycloak:
    created: list[str] = field(default_factory=list)
    rotated: list[str] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)

    async def create(self, *, org_id: uuid.UUID, name: str) -> IssuedCredentials:
        client_id = f"da-{uuid.uuid4().hex[:12]}"
        self.created.append(client_id)
        return IssuedCredentials(
            client_id, f"secret-{len(self.created)}", f"tenants/{org_id}/clients/{client_id}"
        )

    async def rotate(self, *, org_id: uuid.UUID, keycloak_client_id: str, secret_ref: str) -> str:
        self.rotated.append(keycloak_client_id)
        return f"rotated-{len(self.rotated)}"

    async def revoke(self, *, org_id: uuid.UUID, keycloak_client_id: str) -> None:
        self.revoked.append(keycloak_client_id)


class FakeKeys:
    async def create(self, *, org_id: uuid.UUID, subscription_id: uuid.UUID) -> IssuedKey:
        return IssuedKey("hmac-key", f"tenants/{org_id}/webhooks/{subscription_id}")


@dataclass
class Org:
    id: uuid.UUID
    admin: uuid.UUID
    space: uuid.UUID


def build(seed: Seed) -> Org:
    o = seed.org()
    admin = seed.row(
        "org.app_user", org_id=o, external_id="kc-ac05", username="ac05", display_name="Quản trị"
    )
    seed.row(
        "org.app_user", org_id=o, external_id="kc-ac01", username="ac01", display_name="Phân tích"
    )
    role = seed.row("org.role", org_id=o, code="AC05", name="Quản trị", capabilities=["admin"])
    seed.row("org.user_role", org_id=o, user_id=admin, role_id=role, source="manual")
    space = seed.row("org.data_space", org_id=o, code="DS-TD", name="Tín dụng", steward_id=admin)
    return Org(o, admin, space)


class _Tokens:
    def __init__(self, org_id: uuid.UUID) -> None:
        self.org_id = org_id

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org_id)}


def client(org: Org, keycloak: FakeKeycloak, who: str = "kc-ac05") -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=_Tokens(org.id))
    app.state.integration_credentials = keycloak
    app.state.integration_signing_keys = FakeKeys()
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api",
        headers={"Authorization": f"Bearer {who}"},
    )


def new_client(org: Org, **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "Trợ lý văn phòng",
        "kind": "mcp",
        "owner_user_id": str(org.admin),
        "tools": ["ask_question", "get_metric_definition"],
        "data_space_ids": [str(org.space)],
        "quota_per_minute": 60,
        "quota_per_day": 5000,
        "expires_at": (NOW + datetime.timedelta(days=60)).isoformat(),
    }
    body.update(changes)
    return body


async def test_ac_12_01_01_grant_returns_the_secret_once(seed: Seed) -> None:
    org, kc = build(seed), FakeKeycloak()
    async with client(org, kc) as c:
        r = await c.post("/v1/admin/clients", json=new_client(org))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["client_secret"] == "secret-1"
    assert body["keycloak_client_id"] == kc.created[0]
    assert body["tools"] == ["ask_question", "get_metric_definition"]
    # Only the OpenBao path is stored, never the secret.
    stored = seed.conn.cursor()
    stored.execute("SELECT secret_ref FROM integration.api_client WHERE id = %s", (body["id"],))
    assert stored.fetchone()[0].startswith(f"tenants/{org.id}/")


async def test_ac_12_01_05_own_identity_gets_no_detail_tools(seed: Seed) -> None:
    org, kc = build(seed), FakeKeycloak()
    async with client(org, kc) as c:
        refused = await c.post(
            "/v1/admin/clients", json=new_client(org, identity_mode="own_identity")
        )
        granted = await c.post(
            "/v1/admin/clients",
            json=new_client(org, identity_mode="own_identity", tools=["get_metric_definition"]),
        )
    assert refused.status_code == 422 and refused.json()["code"] == "business_rule_violated"
    assert granted.status_code == 201, granted.text
    assert kc.created == [granted.json()["keycloak_client_id"]]  # nothing created for the refusal


async def test_ac_12_01_06_at_most_twelve_months(seed: Seed) -> None:
    org, kc = build(seed), FakeKeycloak()
    late = rules.twelve_months_after(NOW) + datetime.timedelta(days=30)
    async with client(org, kc) as c:
        r = await c.post("/v1/admin/clients", json=new_client(org, expires_at=late.isoformat()))
    assert r.status_code == 422 and r.json()["code"] == "business_rule_violated"


async def test_ac_12_01_02_and_03_rotate_then_revoke(seed: Seed) -> None:
    org, kc = build(seed), FakeKeycloak()
    async with client(org, kc) as c:
        created = (await c.post("/v1/admin/clients", json=new_client(org))).json()
        rotated = await c.post(f"/v1/admin/clients/{created['id']}:rotate-secret")
        assert rotated.status_code == 200, rotated.text
        assert (
            rotated.json()["client_secret"] == "rotated-1" and rotated.json()["secret_rotated_at"]
        )
        revoked = await c.post(
            f"/v1/admin/clients/{created['id']}:revoke", json={"reason": "Lộ bí mật"}
        )
        assert revoked.json()["status"] == "revoked" and revoked.json()["revoked_at"]
        assert kc.revoked == [created["keycloak_client_id"]]
        after = await c.patch(f"/v1/admin/clients/{created['id']}", json={"quota_per_day": 8000})
    assert after.status_code == 409


async def test_ac_12_04_02_raise_the_quota_and_pause(seed: Seed) -> None:
    org, kc = build(seed), FakeKeycloak()
    async with client(org, kc) as c:
        created = (await c.post("/v1/admin/clients", json=new_client(org))).json()
        r = await c.patch(
            f"/v1/admin/clients/{created['id']}", json={"quota_per_day": 8000, "status": "paused"}
        )
    assert r.status_code == 200, r.text
    assert (r.json()["quota_per_day"], r.json()["status"]) == (8000, "paused")


async def test_ac_12_04_01_usage_sums_by_hour(seed: Seed) -> None:
    org, kc = build(seed), FakeKeycloak()
    async with client(org, kc) as c:
        created = (await c.post("/v1/admin/clients", json=new_client(org))).json()
        hour = NOW.replace(minute=0, second=0, microsecond=0) - datetime.timedelta(hours=2)
        for minute, calls, blocked in ((5, 10, 1), (40, 7, 2)):
            seed.row(
                "integration.usage_counter",
                org_id=org.id,
                api_client_id=uuid.UUID(created["id"]),
                tool="ask_question",
                data_space_id=org.space,
                minute=hour + datetime.timedelta(minutes=minute),
                calls=calls,
                blocked_by_permission=blocked,
                p95_ms=800 + minute,
            )
        r = await c.get(f"/v1/admin/clients/{created['id']}/usage", params={"granularity": "hour"})
    assert r.status_code == 200, r.text
    (row,) = r.json()["items"]
    assert (row["calls"], row["blocked_by_permission"], row["p95_ms"]) == (17, 3, 840)


async def test_admin_capability_required(seed: Seed) -> None:
    org, kc = build(seed), FakeKeycloak()
    async with client(org, kc, "kc-ac01") as c:
        r = await c.post("/v1/admin/clients", json=new_client(org))
    assert r.status_code == 403 and kc.created == []


async def test_ac_12_03_webhook_targets_and_retry(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(rules.TARGETS_ENV, "hooks.example.vn")
    org, kc = build(seed), FakeKeycloak()
    async with client(org, kc) as c:
        api_client = (await c.post("/v1/admin/clients", json=new_client(org))).json()
        sub = {"api_client_id": api_client["id"], "event_types": ["report.delivered"]}
        plain = await c.post(
            "/v1/webhooks", json={**sub, "target_url": "http://hooks.example.vn/in"}
        )
        elsewhere = await c.post(
            "/v1/webhooks", json={**sub, "target_url": "https://evil.example.com/in"}
        )
        made = await c.post(
            "/v1/webhooks", json={**sub, "target_url": "https://a.hooks.example.vn/in"}
        )
        assert plain.status_code == 422
        assert elsewhere.status_code == 422 and elsewhere.json()["code"] == "business_rule_violated"
        assert made.status_code == 201, made.text
        assert made.json()["signing_key"] == "hmac-key"

        hook = uuid.UUID(made.json()["id"])
        event = uuid.uuid4()
        failed = seed.row(
            "integration.webhook_delivery",
            org_id=org.id,
            subscription_id=hook,
            event_id=event,
            event_type="report.delivered",
            payload=json.dumps({"report_id": "r1"}),
            attempt_count=6,
            status="failed",
        )
        done = seed.row(
            "integration.webhook_delivery",
            org_id=org.id,
            subscription_id=hook,
            event_id=uuid.uuid4(),
            event_type="report.delivered",
            payload=json.dumps({}),
            status="delivered",
        )
        listed = (
            await c.get(f"/v1/webhooks/{hook}/deliveries", params={"status": "failed"})
        ).json()
        assert [d["id"] for d in listed["items"]] == [str(failed)]
        assert (
            "payload" not in listed["items"][0]
        )  # AC-12-03-07: no data rows leave through the API either

        retry = await c.post(f"/v1/webhook-deliveries/{failed}:retry")
        assert retry.status_code == 202 and retry.headers["Location"].endswith(str(failed))
        again = await c.post(f"/v1/webhook-deliveries/{done}:retry")
    assert again.status_code == 409
    cur = seed.conn.cursor()
    cur.execute(
        "SELECT status, event_id FROM integration.webhook_delivery WHERE id = %s", (str(failed),)
    )
    assert cur.fetchone() == ("retrying", event)
