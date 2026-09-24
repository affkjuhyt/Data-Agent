"""A document whose Silver/Gold attempt genuinely failed (bad file format,
corrupt content) must not keep showing as "still auto-processing" forever —
AssetInventory.tsx only distinguishes that from "actually still running" via
this stamp on KBData.metadata. Live cases: "Comtrade" (.bin, Docling refuses
the extension) and an old-format .xls report both sat "processing" for a
full day after failing outright.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from libs.ingestion.pipeline_status import stamp_pipeline_status

pytestmark = pytest.mark.unit


class _FakeSession:
    def __init__(self, raise_on_execute: Exception | None = None) -> None:
        self.executed: list[Any] = []
        self.committed = False
        self.rolled_back = False
        self._raise = raise_on_execute

    async def execute(self, stmt: Any) -> None:
        if self._raise is not None:
            raise self._raise
        self.executed.append(stmt)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _FakePostgres:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    @asynccontextmanager
    async def get_client(self) -> Any:
        yield self.session


def _patch(stmt: Any) -> dict[str, Any]:
    # The value must be the raw Python dict, not a pre-`json.dumps`'d string —
    # passing a string here is exactly the bug that corrupted 7 live
    # KBData.metadata rows into `[old_object, "...", "..."]` (see
    # tests/integration/test_pipeline_status_stamp_integration.py for the
    # real-Postgres regression test that actually caught it; a mocked
    # session's `execute()` can only check what SQLAlchemy was ASKED to send,
    # not what asyncpg's JSONB codec does with it).
    params = stmt.compile(dialect=postgresql.dialect()).params
    value = params["pipeline_status_patch"]
    assert isinstance(value, dict), f"patch must be a dict, not {type(value)}: {value!r}"
    return value


def test_a_failure_is_stamped_with_stage_and_truncated_error() -> None:
    session = _FakeSession()
    postgres = _FakePostgres(session)
    long_error = "boom " * 100  # well over 300 chars

    asyncio.run(stamp_pipeline_status(
        postgres, str(uuid.uuid4()), stage="silver", status="failed", error=long_error,
    ))

    assert session.committed is True and session.rolled_back is False
    patch = _patch(session.executed[0])
    assert patch["last_pipeline_stage"] == "silver"
    assert patch["last_pipeline_status"] == "failed"
    assert patch["last_pipeline_error"] == long_error[:300]
    assert patch["last_pipeline_error"] != long_error  # confirms it was actually truncated


def test_a_success_clears_the_error_field() -> None:
    session = _FakeSession()
    postgres = _FakePostgres(session)

    asyncio.run(stamp_pipeline_status(postgres, str(uuid.uuid4()), stage="gold", status="succeeded"))

    patch = _patch(session.executed[0])
    assert patch["last_pipeline_status"] == "succeeded"
    assert patch["last_pipeline_error"] is None


def test_a_write_failure_rolls_back_instead_of_raising() -> None:
    """Best-effort: a stamp failure (row deleted mid-flight, connection
    hiccup) must never turn into a crash on top of the real pipeline error
    it was trying to record."""
    session = _FakeSession(raise_on_execute=RuntimeError("connection lost"))
    postgres = _FakePostgres(session)

    asyncio.run(stamp_pipeline_status(postgres, str(uuid.uuid4()), stage="silver", status="failed", error="x"))

    assert session.rolled_back is True and session.committed is False
