"""Purging a structured-table document drops its kb_structured table.

The purge deleted the KBTable row only, so the table an approved import
created — and every row imported into it — outlived the document. The session
is a fake that answers the KBTable lookup and records what else runs.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

pytest.importorskip("qdrant_client")

from services.kb_backend.operations import trash_ops  # noqa: E402

pytestmark = pytest.mark.unit

DOCUMENT = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


class _Scalars:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def all(self) -> list[Any]:
        return self._values


class _Result:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def scalars(self) -> _Scalars:
        return _Scalars(self._values)


class _Session:
    def __init__(self, table_names: list[str]) -> None:
        self._table_names = table_names
        self.executed: list[str] = []

    async def execute(self, statement: Any) -> _Result:
        sql = str(statement)
        self.executed.append(sql)
        return _Result(self._table_names if '"KBTable".table_name' in sql else [])


def _drops(session: _Session) -> list[str]:
    return [sql for sql in session.executed if sql.startswith("DROP TABLE")]


def test_the_imported_table_is_dropped() -> None:
    session = _Session(["kb_structured.sales_2024", "kb_structured.sales_2024_2"])

    asyncio.run(trash_ops._drop_structured_tables(session, DOCUMENT))

    assert _drops(session) == [
        'DROP TABLE IF EXISTS kb_structured."sales_2024"',
        'DROP TABLE IF EXISTS kb_structured."sales_2024_2"',
    ]


@pytest.mark.parametrize(
    "table_name",
    ["public.KBData", "sales_2024", 'kb_structured.x"; DROP TABLE "KBData', "kb_structured."],
)
def test_nothing_outside_kb_structured_is_dropped(table_name: str) -> None:
    session = _Session([table_name])

    asyncio.run(trash_ops._drop_structured_tables(session, DOCUMENT))

    assert _drops(session) == []
