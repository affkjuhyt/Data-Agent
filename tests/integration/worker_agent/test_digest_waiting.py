"""The digest's "N việc đang chờ bạn" counts what Cần bạn lists: requests assigned to the owner,
to a group of theirs, or to a capability they hold where the request lives."""

from __future__ import annotations

import uuid

import pytest

from services.worker_agent import digests

from .conftest import Seed

pytestmark = [pytest.mark.integration]


async def test_what_waits_follows_the_inbox_rule(seed: Seed) -> None:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Ngân hàng thử")
    owner = seed.row("org.app_user", org_id=org, external_id="kc-o", username="o", display_name="O")
    other = seed.row("org.app_user", org_id=org, external_id="kc-x", username="x", display_name="X")
    space = seed.row("org.data_space", org_id=org, code="DS-W", name="W", steward_id=owner)
    elsewhere = seed.row("org.data_space", org_id=org, code="DS-E", name="E", steward_id=other)
    approver = seed.row("org.role", org_id=org, code="AP", name="AP", capabilities=["approve"])
    seed.row(
        "org.data_space_member",
        org_id=org,
        data_space_id=space,
        principal_type="user",
        principal_id=owner,
        role_id=approver,
    )
    group = seed.row("org.user_group", org_id=org, external_id="cn=risk", name="Rủi ro")
    seed.row("org.user_group_member", org_id=org, group_id=group, user_id=owner)

    def request(**values: object) -> None:
        seed.row(
            "inbox.hitl_request",
            org_id=org,
            kind="approve_proposal",
            title="Duyệt",
            source_service="context",
            **values,
        )

    # Counted: by name, reassigned to them, to their group, to a capability they hold there.
    request(assignee_id=owner, data_space_id=space)
    request(assignee_id=owner, data_space_id=space, status="reassigned")
    request(assignee_group="Rủi ro", data_space_id=space)
    request(assignee_capability="approve", data_space_id=space)
    # Not counted: someone else's, a capability they hold in no role there, an org-wide one
    # needing an org role they lack, a decided one.
    request(assignee_id=other, data_space_id=space)
    request(assignee_capability="approve", data_space_id=elsewhere)
    request(assignee_capability="compliance", data_space_id=space)
    request(assignee_capability="approve")
    request(
        assignee_id=owner,
        data_space_id=space,
        status="approved",
        resolved_at="2026-10-01T00:00:00Z",
    )

    assert await digests.waiting(org, owner) == 4
