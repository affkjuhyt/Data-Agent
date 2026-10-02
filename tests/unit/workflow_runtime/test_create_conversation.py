"""create_conversation saves a verified caller's conversation.

It used to require an active "Members" row first. Nothing ever wrote that
table, so every real user was refused (only the "anonymous" dev bypass got
through) and their turns were never persisted. The caller's tenant is already
the verified claim require_tenant checked, so the function now only inserts.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from services.workflow_runtime.infrastructure import db

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"


class _Client:
    def __init__(self) -> None:
        self.inserted: list[Any] = []

    def is_connected(self) -> bool:
        return True

    def get_client(self, **_: Any) -> Any:
        raise AssertionError("create_conversation must not look anything up before inserting")

    async def insert(self, model: Any) -> Any:
        self.inserted.append(model)
        return SimpleNamespace(code=200, data={"id": "conv-1"}, error=None)


def test_a_verified_user_gets_a_saved_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client()
    monkeypatch.setattr(db, "client", client)

    conv_id = asyncio.run(
        db.create_conversation(user_ref="kc-user-42", channel="web", tenant_id=TENANT)
    )

    assert conv_id == "conv-1"
    [row] = client.inserted
    assert (row.tenant_id, row.user_ref, row.channel, row.status) == (
        TENANT,
        "kc-user-42",
        "web",
        "active",
    )
