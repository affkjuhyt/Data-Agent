"""Pending human reviews are scoped to the caller's tenant.

The reviews router was authenticated but not tenant-scoped: any user could list
every tenant's pending reviews and resume one. A review now records its tenant
and another tenant's reads as missing.
"""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from services.workflow_runtime.api.routers import reviews

pytestmark = pytest.mark.unit

TENANT_A = "a0000000-0000-0000-0000-00000000000a"
TENANT_B = "b0000000-0000-0000-0000-00000000000b"


def _caller(tenant: str) -> dict[str, object]:
    return {"user_id": "u", "tenant_id": tenant, "roles": ["business-operator"]}


@pytest.fixture
def pending(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = {
        f"conv-{name}": {
            "tenant_id": tenant,
            "agent_id": "agent",
            "query": "q",
            "payload": None,
            "suspended_at": time.monotonic(),
        }
        for name, tenant in (("a", TENANT_A), ("b", TENANT_B))
    }
    monkeypatch.setattr(reviews, "_pending_reviews", entries)


async def test_a_tenant_lists_only_its_own_reviews(pending: None) -> None:
    listed = await reviews.list_pending(_caller(TENANT_A))

    assert [r["conv_id"] for r in listed] == ["conv-a"]


async def test_another_tenants_review_cannot_be_read_or_resumed(pending: None) -> None:
    with pytest.raises(HTTPException) as read:
        await reviews.get_pending("conv-b", _caller(TENANT_A))
    with pytest.raises(HTTPException) as resume:
        await reviews.resume_review(
            "conv-b", reviews.ResumeRequest(approved=True), _caller(TENANT_A)
        )

    assert read.value.status_code == resume.value.status_code == 404
    assert "conv-b" in reviews._pending_reviews
