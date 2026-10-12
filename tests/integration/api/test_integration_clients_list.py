"""GET /v1/admin/clients (UC-12-01, UC-12-04, added 12/10/2026) against PostgreSQL: every client of
the organisation with scope, last-24-hour calls, expiry and status, never a secret."""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.security]

NOW = datetime.datetime.now(datetime.UTC)


@dataclass
class Org:
    id: uuid.UUID
    admin: uuid.UUID
    space: uuid.UUID
    suffix: str


def build(seed: Seed) -> Org:
    suffix = uuid.uuid4().hex[:8]
    o = seed.org()
    admin = seed.row(
        "org.app_user",
        org_id=o,
        external_id=f"kc-admin-{suffix}",
        username=f"admin-{suffix}",
        display_name="Quản trị",
        status="active",
    )
    analyst = seed.row(
        "org.app_user",
        org_id=o,
        external_id=f"kc-analyst-{suffix}",
        username=f"analyst-{suffix}",
        display_name="Phân tích",
        status="active",
    )
    admin_role = seed.row("org.role", org_id=o, code="AC05", name="Admin", capabilities=["admin"])
    ask_role = seed.row("org.role", org_id=o, code="AC01", name="Ask", capabilities=["ask"])
    seed.row("org.user_role", org_id=o, user_id=admin, role_id=admin_role, source="manual")
    seed.row("org.user_role", org_id=o, user_id=analyst, role_id=ask_role, source="manual")
    space = seed.row("org.data_space", org_id=o, code="DS-TD", name="Tín dụng", steward_id=admin)
    return Org(o, admin, space, suffix)


def api_client(
    seed: Seed, org: Org, name: str, status: str, created: datetime.datetime, kind: str = "mcp"
) -> uuid.UUID:
    return seed.row(
        "integration.api_client",
        org_id=org.id,
        name=name,
        kind=kind,
        owner_user_id=org.admin,
        keycloak_client_id=f"da-{uuid.uuid4().hex[:12]}",
        secret_ref=f"tenants/{org.id}/clients/{name}",
        tools=["ask_question"],
        data_space_ids=f"{{{org.space}}}",
        expires_at=created + datetime.timedelta(days=90),
        status=status,
        owner_confirmed_at=created if status in ("active", "paused") else None,
        created_at=created,
        updated_at=created,
    )


class _Tokens:
    def __init__(self, org_id: uuid.UUID) -> None:
        self.org_id = org_id

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org_id)}


def client(org: Org, who: str = "admin") -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=_Tokens(org.id))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api",
        headers={"Authorization": f"Bearer kc-{who}-{org.suffix}"},
    )


async def test_admin_lists_clients_with_usage_and_no_secret(seed: Seed) -> None:
    org = build(seed)
    oldest = api_client(seed, org, "excel", "active", NOW - datetime.timedelta(days=3), "rest")
    middle = api_client(seed, org, "copilot", "pending_owner", NOW - datetime.timedelta(days=2))
    newest = api_client(seed, org, "bot", "revoked", NOW - datetime.timedelta(days=1))
    for minute, calls, errors in ((NOW - datetime.timedelta(hours=1), 7, 1), (NOW, 3, 0)):
        seed.row(
            "integration.usage_counter",
            org_id=org.id,
            api_client_id=oldest,
            tool="ask_question",
            data_space_id=org.space,
            minute=minute.replace(second=0, microsecond=0),
            calls=calls,
            errors=errors,
        )
    # Older than 24 hours: not in the summary.
    seed.row(
        "integration.usage_counter",
        org_id=org.id,
        api_client_id=oldest,
        tool="ask_question",
        minute=(NOW - datetime.timedelta(days=2)).replace(second=0, microsecond=0),
        calls=100,
    )
    foreign = build(seed)
    api_client(seed, foreign, "foreign", "active", NOW)

    async with client(org) as api:
        page = await api.get("/v1/admin/clients?page_size=2")
        assert page.status_code == 200, page.text
        assert [c["id"] for c in page.json()["items"]] == [str(newest), str(middle)]
        rest = await api.get(
            f"/v1/admin/clients?page_size=2&cursor={page.json()['next_page_token']}"
        )
        assert rest.status_code == 200, rest.text
        assert [c["id"] for c in rest.json()["items"]] == [str(oldest)]
        assert rest.json()["next_page_token"] == ""

        excel = rest.json()["items"][0]
        assert (excel["calls_24h"], excel["errors_24h"]) == (10, 1)
        assert excel["tools"] == ["ask_question"]
        assert excel["data_space_ids"] == [str(org.space)]
        assert excel["kind"] == "rest"
        # Neither the secret nor where it is kept.
        for row in page.json()["items"] + rest.json()["items"]:
            assert not {"client_secret", "secret_ref", "secret_hash"} & set(row)
        assert "tenants/" not in page.text + rest.text

        active = await api.get("/v1/admin/clients?status=active")
        assert [c["id"] for c in active.json()["items"]] == [str(oldest)]
        mcp = await api.get("/v1/admin/clients?kind=mcp&order_by=name asc")
        assert [c["id"] for c in mcp.json()["items"]] == [str(newest), str(middle)]
        bad = await api.get("/v1/admin/clients?status=gone")
        assert bad.status_code == 422, bad.text

    async with client(org, "analyst") as api:
        denied = await api.get("/v1/admin/clients")
        assert denied.status_code == 403, denied.text
