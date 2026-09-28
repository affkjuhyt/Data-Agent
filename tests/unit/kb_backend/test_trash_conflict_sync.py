"""Trashing a document hides its conflicts; restoring brings them back.

Only a permanent delete used to clear a document's conflicts, so a document
sitting in the trash kept showing "pending" conflicts in Conflict Resolution.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from services.kb_backend.operations.trash_ops import _sync_conflicts_with_document

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return self._values


class _Session:
    def __init__(self, batch_ids: list[Any]) -> None:
        self.batch_ids = batch_ids
        self.sql: list[str] = []

    async def execute(self, stmt: Any) -> _Result:
        self.sql.append(str(stmt.compile(dialect=postgresql.dialect())))
        return _Result(self.batch_ids)


def _run(session: _Session, is_deleted: bool) -> None:
    asyncio.run(_sync_conflicts_with_document(session, uuid.uuid4(), is_deleted))


def test_conflicts_and_their_batch_are_updated_together() -> None:
    session = _Session([uuid.uuid4()])
    _run(session, True)
    assert len(session.sql) == 3
    assert session.sql[0].startswith("SELECT")
    assert 'UPDATE "KBConflict" SET is_deleted' in session.sql[1]
    assert 'UPDATE "KBConflictBatch" SET is_deleted' in session.sql[2]
    # The batch stays visible while any of its conflicts is still visible.
    assert "NOT (EXISTS" in session.sql[2]


def test_conflict_without_a_batch_skips_the_batch_update() -> None:
    session = _Session([])
    _run(session, False)
    assert len(session.sql) == 2
    assert 'UPDATE "KBConflict" SET is_deleted' in session.sql[1]
