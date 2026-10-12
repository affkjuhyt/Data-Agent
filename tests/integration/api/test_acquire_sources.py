"""Thu thập (P14) crawl-source edits against PostgreSQL: PATCH keeps the guarantees create gives
(BR-149 URLs in scope) and only the owner or a data admin hands a source to someone else."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.security]


@dataclass
class Space:
    org: uuid.UUID
    code: str
    owner: uuid.UUID
    other_steward: uuid.UUID
    data_admin: uuid.UUID


def build(seed: Seed) -> Space:
    o = seed.org()

    def user(name: str) -> uuid.UUID:
        return seed.row(
            "org.app_user", org_id=o, external_id=f"kc-{name}", username=name, display_name=name
        )

    owner, other, admin = user("owner"), user("other"), user("admin")
    steward = seed.row(
        "org.role", org_id=o, code="AC03", name="Phụ trách", capabilities=["steward"]
    )
    data_admin = seed.row(
        "org.role", org_id=o, code="AC04", name="Kỹ sư dữ liệu", capabilities=["admin_data"]
    )
    space = seed.row("org.data_space", org_id=o, code="DS-THU", name="Thu thập", steward_id=owner)
    for who, role in ((owner, steward), (other, steward), (admin, data_admin)):
        seed.row(
            "org.data_space_member",
            org_id=o,
            data_space_id=space,
            principal_type="user",
            principal_id=who,
            role_id=role,
        )
    return Space(o, "DS-THU", owner, other, admin)


class _Tokens:
    def __init__(self, org_id: uuid.UUID) -> None:
        self.org_id = org_id

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.org_id)}


def client(space: Space, who: str) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=_Tokens(space.org))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api",
        headers={"Authorization": f"Bearer kc-{who}", "X-Data-Space": space.code},
    )


async def create_source(space: Space) -> str:
    async with client(space, "owner") as c:
        r = await c.post(
            "/v1/crawl-sources",
            json={
                "name": "Lãi suất huy động",
                "goal": "Bảng lãi suất 12 tháng",
                "kind": "web_page",
                "seed_urls": ["https://bank.example.vn/lai-suat"],
                "allowed_domains": ["bank.example.vn", "legal.example.vn"],
                "terms_url": "https://legal.example.vn/dieu-khoan",
            },
        )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_narrowing_domains_cannot_strand_the_terms_page(seed: Seed) -> None:
    space = build(seed)
    source = await create_source(space)
    async with client(space, "owner") as c:
        # The seeds stay in scope without legal.example.vn; the terms page would not.
        r = await c.patch(
            f"/v1/crawl-sources/{source}", json={"allowed_domains": ["bank.example.vn"]}
        )
        assert r.status_code == 422, r.text
        assert r.json()["errors"][0]["field"] == "allowed_domains"
        # Keeping both domains is fine.
        r = await c.patch(
            f"/v1/crawl-sources/{source}",
            json={"allowed_domains": ["bank.example.vn", "legal.example.vn"]},
        )
    assert r.status_code == 200, r.text


async def test_only_the_owner_or_a_data_admin_reassigns_the_source(seed: Seed) -> None:
    space = build(seed)
    source = await create_source(space)
    async with client(space, "other") as c:
        r = await c.patch(
            f"/v1/crawl-sources/{source}", json={"owner_id": str(space.other_steward)}
        )
    assert r.status_code == 403, r.text
    async with client(space, "admin") as c:
        r = await c.patch(
            f"/v1/crawl-sources/{source}", json={"owner_id": str(space.other_steward)}
        )
    assert r.status_code == 200, r.text
    assert r.json()["owner_id"] == str(space.other_steward)
    # The new owner may now hand it on; the steward who just lost it may not take it back.
    async with client(space, "owner") as c:
        r = await c.patch(f"/v1/crawl-sources/{source}", json={"owner_id": str(space.owner)})
    assert r.status_code == 403, r.text
