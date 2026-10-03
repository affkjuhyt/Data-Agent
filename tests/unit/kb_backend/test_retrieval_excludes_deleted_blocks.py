"""Retrieval skips chunks whose block is deleted.

A Gold re-run (libs/ingestion/gold/persistence.py) and a block-level trash
mark set only "KBTextBlock".is_deleted; the block's version stays active and
undeleted. lexical_search and _fetch_chunks_postgres filtered on the version's
flags alone, so a superseded or trashed chunk kept surfacing in answers after
its Qdrant point was gone. The statements are captured and compiled; what the
rows look like is not the point here.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("qdrant_client")
pytest.importorskip("neo4j")

from sqlalchemy.dialects import postgresql  # noqa: E402

from services.kb_backend.pipeline.light_rag.pipeline import (  # noqa: E402
    _fetch_chunks_postgres,
    lexical_search,
)

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"
BLOCK = "00000000-0000-0000-0000-0000000000b1"


class _Result:
    def all(self) -> list[Any]:
        return []


class _Session:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        return _Result()


class _Postgres:
    def __init__(self) -> None:
        self.session = _Session()

    @asynccontextmanager
    async def get_client(self, **_: Any):
        yield self.session


def _sql(postgres: _Postgres) -> str:
    [statement] = postgres.session.statements
    return str(statement.compile(dialect=postgresql.dialect()))


def test_lexical_search_skips_deleted_blocks() -> None:
    postgres = _Postgres()

    asyncio.run(lexical_search(postgres, "giá dầu", TENANT, limit=5))  # type: ignore[arg-type]

    assert '"KBTextBlock".is_deleted IS false' in _sql(postgres)


def test_chunk_fetch_skips_deleted_blocks() -> None:
    postgres = _Postgres()

    asyncio.run(_fetch_chunks_postgres(postgres, [BLOCK], TENANT))  # type: ignore[arg-type]

    assert '"KBTextBlock".is_deleted IS false' in _sql(postgres)
