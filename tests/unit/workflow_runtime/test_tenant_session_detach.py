"""_tenant_session must not leave ORM instances expired after the block.

rollback() expires every attached instance; reading an attribute afterwards
raised DetachedInstanceError (chunks endpoint 500, Gold chunk failures). The
session must expunge before it rolls back.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from libs.database_connector.postgres_connector import _tenant_session

pytestmark = pytest.mark.unit


class _Session:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *_: Any) -> None:
        self.calls.append("close")

    async def execute(self, *_: Any, **__: Any) -> None:
        self.calls.append("set_config")

    def expunge_all(self) -> None:
        self.calls.append("expunge_all")

    async def rollback(self) -> None:
        self.calls.append("rollback")


def test_expunges_before_rollback() -> None:
    calls: list[str] = []

    async def run() -> None:
        async with _tenant_session(lambda: _Session(calls), "t1"):
            calls.append("body")

    asyncio.run(run())
    assert calls == ["set_config", "body", "expunge_all", "rollback", "close"]


def test_expunges_even_when_the_body_raises() -> None:
    calls: list[str] = []

    async def run() -> None:
        async with _tenant_session(lambda: _Session(calls), "t1"):
            raise ValueError("boom")

    with pytest.raises(ValueError):
        asyncio.run(run())
    assert calls.index("expunge_all") < calls.index("rollback")
