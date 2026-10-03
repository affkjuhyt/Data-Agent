"""The Postgres mirror prune after a graph removal.

_prune_postgres_orphans called an unimported `sa_or_`, so every remove path
raised NameError after Neo4j had already committed and orphan KBNeo4jNode rows
were never pruned.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from libs.ingestion.graph_store import GraphStore

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"


class _Session:
    def __init__(self) -> None:
        self.statements: list[Any] = []
        self.committed = False

    async def execute(self, statement: Any) -> None:
        self.statements.append(statement)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        raise AssertionError("prune must not roll back")


class _Postgres:
    def __init__(self) -> None:
        self.session = _Session()

    @asynccontextmanager
    async def get_client(self, **_: Any):
        yield self.session


def test_prune_deletes_nodes_no_edge_references_and_commits():
    postgres = _Postgres()
    store = GraphStore(neo4j=None, postgres=postgres)  # type: ignore[arg-type]

    asyncio.run(store._prune_postgres_orphans(TENANT, "kb-1"))

    assert postgres.session.committed
    [statement] = postgres.session.statements
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert sql.startswith('DELETE FROM "KBNeo4jNode"')
    assert "NOT (EXISTS" in sql
    assert '"KBNeo4jRelationship".from_node = "KBNeo4jNode".node_id OR' in sql
