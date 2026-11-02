"""Certifying a knowledge page version (UC-07-10, BR-164) against PostgreSQL.

The certifier is a third person, neither the author nor the reviewer, unless the organisation
allows self-approval (org.organization.allow_self_approval).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from services.api.app import create_app
from services.api.config import Settings

from .conftest import Seed

pytestmark = [pytest.mark.integration, pytest.mark.contract]


@dataclass
class Wiki:
    org: uuid.UUID
    space: uuid.UUID
    users: dict[str, uuid.UUID]
    seed: Seed

    def headers(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer kc-{who}", "X-Data-Space": str(self.space)}

    def reviewed_version(self, author: str, reviewer: str) -> uuid.UUID:
        """A reviewed version of a new topic page, written by `author`, reviewed by `reviewer`."""
        page = self.seed.row(
            "context.knowledge_page",
            org_id=self.org,
            data_space_id=self.space,
            kind="topic",
            subject_kind="topic",
            slug=f"trang-{uuid.uuid4().hex[:8]}",
            title="Trang thử",
            owner_id=self.users[author],
        )
        version = self.seed.row(
            "context.knowledge_page_version",
            org_id=self.org,
            page_id=page,
            origin="manual",
            body="Giá xăng điều hành theo kỳ.",
            created_by=self.users[author],
        )
        # Sending a version for review needs a claim with a source (BR-161).
        source = self.seed.row(
            "context.knowledge_source_link",
            org_id=self.org,
            page_version_id=version,
            source_kind="glossary_term",
            source_ref=uuid.uuid4(),
            source_label="Nguồn",
            content_sha256="0" * 64,
            data_space_id=self.space,
        )
        self.seed.row(
            "context.knowledge_claim",
            org_id=self.org,
            page_version_id=version,
            anchor="c1",
            statement="Giá xăng điều hành theo kỳ.",
            source_link_id=source,
            valid_from="2026-10-01",
        )
        # The guard trigger takes a version only in order: draft, pending_review, reviewed.
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "UPDATE context.knowledge_page_version SET status = 'pending_review' WHERE id = %s",
                (str(version),),
            )
            cur.execute(
                "UPDATE context.knowledge_page_version SET status = 'reviewed', reviewed_by = %s,"
                " reviewed_at = now() WHERE id = %s",
                (str(self.users[reviewer]), str(version)),
            )
        return version

    def status(self, version: uuid.UUID) -> tuple[str, str]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT status, certified_by FROM context.knowledge_page_version WHERE id = %s",
                (str(version),),
            )
            status, certified_by = cur.fetchone()
            return status, str(certified_by)


@pytest.fixture
def wiki(seed: Seed) -> Wiki:
    org = seed.org()
    role = seed.row(
        "org.role", org_id=org, code="STEWARD", name="Steward", capabilities=["ask", "steward"]
    )
    keeper = seed.row(
        "org.app_user",
        org_id=org,
        external_id=f"kc-keeper-{org.hex[:6]}",
        username="keeper",
        display_name="Keeper",
    )
    space = seed.row("org.data_space", org_id=org, code="DS-KB", name="KB", steward_id=keeper)
    users: dict[str, uuid.UUID] = {}
    for name in ("author", "reviewer", "certifier"):
        user = seed.row(
            "org.app_user",
            org_id=org,
            external_id=f"kc-{name}",
            username=name,
            display_name=name,
            status="active",
        )
        seed.row("org.user_role", org_id=org, user_id=user, role_id=role, source="manual")
        seed.row(
            "org.data_space_member",
            org_id=org,
            data_space_id=space,
            principal_type="user",
            principal_id=user,
            role_id=role,
        )
        users[name] = user
    return Wiki(org, space, users, seed)


class Tokens:
    def __init__(self, wiki: Wiki) -> None:
        self.wiki = wiki

    def claims(self, token: str) -> dict[str, Any]:
        return {"sub": token, "org_id": str(self.wiki.org)}


def client(wiki: Wiki) -> httpx.AsyncClient:
    app = create_app(Settings(), token_verifier=Tokens(wiki))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


async def test_the_author_and_the_reviewer_cannot_certify(wiki: Wiki) -> None:
    version = wiki.reviewed_version("author", "reviewer")
    async with client(wiki) as api:
        for who in ("author", "reviewer"):
            r = await api.post(
                f"/v1/knowledge-page-versions/{version}:certify", headers=wiki.headers(who)
            )
            assert r.status_code == 403, r.text
            assert r.json()["code"] == "policy_denied"
        third = await api.post(
            f"/v1/knowledge-page-versions/{version}:certify", headers=wiki.headers("certifier")
        )
    assert third.status_code == 200, third.text


async def test_knowledge_page_certify_self_approved_when_the_org_allows_it(
    wiki: Wiki, self_approvals: Any
) -> None:
    wiki.seed.allow_self_approval(wiki.org)
    # The same person wrote, reviewed and now certifies.
    version = wiki.reviewed_version("author", "author")
    async with client(wiki) as api:
        r = await api.post(
            f"/v1/knowledge-page-versions/{version}:certify", headers=wiki.headers("author")
        )
    assert r.status_code == 200, r.text
    assert wiki.status(version) == ("certified", str(wiki.users["author"]))
    assert self_approvals(wiki.org) == ["knowledge_page.certify"]
