"""Module transactions are bound to the caller (ADR-31). The scoping itself is
proven against Postgres in tests/integration/api."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from services.api.kernel import db
from services.api.kernel.auth import Principal

pytestmark = [pytest.mark.unit, pytest.mark.security]


async def test_transaction_scopes_to_the_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    @asynccontextmanager
    async def fake_scoped_session(
        role: str, org_id: Any, user_id: Any, **settings: str
    ) -> AsyncIterator[str]:
        seen.update(role=role, org_id=org_id, user_id=user_id, settings=settings)
        yield "session"

    monkeypatch.setattr(db, "scoped_session", fake_scoped_session)
    principal = Principal(user_id=uuid.uuid4(), org_id=uuid.uuid4(), external_id="kc")
    async with db.transaction("svc_qa", principal, knowledge_reader="on") as session:
        assert session == "session"
    assert seen == {
        "role": "svc_qa",
        "org_id": principal.org_id,
        "user_id": principal.user_id,
        "settings": {"knowledge_reader": "on"},
    }
