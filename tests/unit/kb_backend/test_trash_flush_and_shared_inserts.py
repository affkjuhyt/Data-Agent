"""Connection behaviour of the delete path and of multi-write inserts.

flush() used to take five sequential sessions and hold one across calls to
Qdrant, Neo4j, MinIO and Mongo. insert() always owned its transaction, so a
document could exist with no lifecycle row. Both are about how long a pooled
connection is held and what a partial failure leaves behind.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest

from services.kb_backend.operations import trash_ops

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"
DOCUMENT_ID = "00000000-0000-0000-0000-0000000000aa"


class _Scalars:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def all(self) -> list[Any]:
        return self._values


class _Result:
    def __init__(self, values: list[Any] | None = None) -> None:
        self._values = values or []

    def scalars(self) -> _Scalars:
        return _Scalars(self._values)


class _Session:
    def __init__(self, timeline: list[str], scalars: list[Any]) -> None:
        self.timeline = timeline
        self._scalars = list(scalars)
        self.statements: list[str] = []
        self.commits = 0

    async def scalar(self, statement: Any) -> Any:
        return self._scalars.pop(0) if self._scalars else None

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        self.statements.append(str(statement))
        self.timeline.append("sql")
        return _Result()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass


class _Postgres:
    """Records when a session is open, so external calls made while one is held
    are visible in the timeline."""

    def __init__(self, scalars: list[Any]) -> None:
        self.timeline: list[str] = []
        self.sessions: list[_Session] = []
        self._scalars = scalars

    @asynccontextmanager
    async def get_client(self, tenant_id: str | None = None):
        session = _Session(self.timeline, self._scalars)
        self.sessions.append(session)
        self.timeline.append("session-open")
        try:
            yield session
        finally:
            self.timeline.append("session-close")


class _Recorder:
    def __init__(self, timeline: list[str], label: str) -> None:
        self.timeline = timeline
        self.label = label


class _Qdrant(_Recorder):
    def get_client(self) -> "_Qdrant":
        self.timeline.append(self.label)
        return self

    async def delete(self, *_a: Any, **_k: Any) -> None:
        self.timeline.append(self.label)


class _GraphSession:
    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline

    async def run(self, *_a: Any, **_k: Any) -> "_GraphSession":
        self.timeline.append("neo4j")
        return self

    async def consume(self) -> None:
        pass

    async def __aenter__(self) -> "_GraphSession":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        pass


class _Neo4j:
    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline

    def get_client(self) -> "_Neo4j":
        return self

    def session(self) -> _GraphSession:
        return _GraphSession(self.timeline)


def _flush_document(monkeypatch, scalars: list[Any]) -> _Postgres:
    postgres = _Postgres(scalars)
    qdrant = _Qdrant(postgres.timeline, "qdrant")
    neo4j = _Neo4j(postgres.timeline)

    async def fake_qdrant_delete(*_a: Any, **_k: Any) -> None:
        postgres.timeline.append("qdrant")

    # GraphStore's own mirror sessions are its business (they open and close
    # before its Neo4j call); here it stands for "the graph was touched".
    async def fake_graph_remove(*_a: Any, **_k: Any) -> None:
        postgres.timeline.append("neo4j")

    monkeypatch.setattr(trash_ops, "_qdrant_delete_by_data_id", fake_qdrant_delete)
    monkeypatch.setattr(trash_ops, "_qdrant_delete_block", fake_qdrant_delete)
    monkeypatch.setattr(trash_ops, "_remove_document_from_graph", fake_graph_remove)

    asyncio.run(trash_ops.flush(
        postgres, qdrant, neo4j, "document", DOCUMENT_ID, TENANT,
    ))
    return postgres


class TestFlushHoldsFewerConnections:
    def test_a_document_flush_uses_two_sessions_not_five(self, monkeypatch):
        # existence, ownership, object key
        postgres = _flush_document(monkeypatch, [DOCUMENT_ID, DOCUMENT_ID, None])
        assert len(postgres.sessions) == 2

    def test_no_session_is_held_while_qdrant_or_neo4j_is_called(self, monkeypatch):
        """The old order kept a connection checked out across network calls to
        four other stores."""
        postgres = _flush_document(monkeypatch, [DOCUMENT_ID, DOCUMENT_ID, None])

        depth = 0
        for event in postgres.timeline:
            if event == "session-open":
                depth += 1
            elif event == "session-close":
                depth -= 1
            elif event in ("qdrant", "neo4j"):
                assert depth == 0, f"{event} ran while a Postgres session was open"

    def test_the_deletes_are_one_transaction(self, monkeypatch):
        postgres = _flush_document(monkeypatch, [DOCUMENT_ID, DOCUMENT_ID, None])
        assert sum(session.commits for session in postgres.sessions) == 1

    def test_vectors_go_before_the_rows(self, monkeypatch):
        """A deleted row whose vectors survive keeps deleted content
        searchable; the reverse only makes a live document look empty."""
        postgres = _flush_document(monkeypatch, [DOCUMENT_ID, DOCUMENT_ID, None])
        timeline = postgres.timeline

        assert timeline.index("qdrant") < timeline.index("sql")

    def test_a_row_already_gone_costs_one_session_and_no_deletes(self, monkeypatch):
        postgres = _Postgres([None])
        qdrant = _Qdrant(postgres.timeline, "qdrant")

        asyncio.run(trash_ops.flush(postgres, qdrant, _Neo4j(postgres.timeline),
                                    "document", DOCUMENT_ID, TENANT))

        assert len(postgres.sessions) == 1
        assert postgres.sessions[0].statements == []


class TestChunkFlushClearsItsChildren:
    def test_versions_are_deleted_before_the_block(self, monkeypatch):
        """KBTextBlockVersion -> KBTextBlock does not cascade in the real
        schema, so deleting a chunk with versions raised a FK violation."""
        postgres = _Postgres([DOCUMENT_ID, DOCUMENT_ID])
        qdrant = _Qdrant(postgres.timeline, "qdrant")

        async def fake_delete(*_a: Any, **_k: Any) -> None:
            postgres.timeline.append("qdrant")

        monkeypatch.setattr(trash_ops, "_qdrant_delete_block", fake_delete)
        asyncio.run(trash_ops.flush(postgres, qdrant, _Neo4j(postgres.timeline),
                                    "chunk", DOCUMENT_ID, TENANT))

        statements = " | ".join(postgres.sessions[-1].statements)
        assert "KBTextBlockVersion" in statements, "chunk flush never touches its versions"


class _OwnedSession:
    """Minimal session for insert(): records commits, resolves no defaults."""

    def __init__(self) -> None:
        self.commits = 0

    def add(self, instance: Any) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def execute(self, statement: Any, params: Any = None) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        pass

    def expunge_all(self) -> None:
        # _tenant_session detaches loaded instances before its rollback.
        pass

    async def __aenter__(self) -> "_OwnedSession":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        pass


class TestInsertCanJoinACallersTransaction:
    def _prepared(self):
        from libs.basemodel.services_databaseconnector.postgres_model import KBKnowledgeBaseInsert
        return KBKnowledgeBaseInsert(tenant_id=TENANT, name="kb", description="", created_by="tester")

    def test_a_shared_session_is_not_committed_by_insert(self):
        from libs.database_connector.postgres_connector import PostgresClient

        client = PostgresClient()
        session = _Session([], [])

        async def fake_flush() -> None:
            pass

        session.flush = fake_flush          # type: ignore[assignment]
        session.add = lambda instance: None  # type: ignore[assignment]

        result = asyncio.run(client.insert(self._prepared(), session=session))

        assert result.code == 200
        assert session.commits == 0, "the caller owns the transaction"

    def test_insert_without_a_session_still_commits(self):
        """The existing contract: a lone insert owns and closes its transaction."""
        from libs.database_connector.postgres_connector import PostgresClient

        owned = _OwnedSession()
        client = PostgresClient()
        client._session_factory = lambda: owned   # type: ignore[attr-defined]
        client._bypasses_rls = False              # type: ignore[attr-defined]

        result = asyncio.run(client.insert(self._prepared()))

        assert result.code == 200
        assert owned.commits == 1
