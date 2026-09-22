"""A re-run with identical content must say so, not look like a run that lost its document.

The Bronze document is keyed by title, so an unchanged re-run keeps the existing
row (which may already be at Silver/Gold) and adds nothing new to Bronze.
"""
from __future__ import annotations

import hashlib
from typing import Any

import pytest

from libs.ingestion.connectors.base import FetchedArtifact
from services.platform_workers.workers.web_crawler import bronze

pytestmark = pytest.mark.unit

CONTENT = b"identical bytes"


class _Cursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self._row = row
        self.sql: list[str] = []

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        self.sql.append(" ".join(sql.split()))

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._row

    def fetchall(self) -> list[Any]:
        return []  # no KBConflict batches in these tests


class _Conn:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.cursor_ = _Cursor(row)

    def cursor(self) -> _Cursor:
        return self.cursor_

    def commit(self) -> None:
        pass


def _artifact() -> FetchedArtifact:
    return FetchedArtifact(content_bytes=CONTENT, content_type="text/csv", source_url="https://x/y.csv", extension="csv")


def test_unchanged_rerun_reports_the_kept_document_and_its_tier() -> None:
    row = ("doc-1", hashlib.sha256(CONTENT).hexdigest(), "silver")
    outcome: dict[str, Any] = {}
    data_id = bronze.register_http_file_bronze(
        _Conn(row), tenant_id="t", role_id="r", title="T", added_by="a", artifact=_artifact(), outcome=outcome,
    )
    assert data_id == "doc-1"
    assert outcome == {"unchanged": True, "existing_tier": "silver"}


def test_unchanged_rerun_touches_nothing_but_the_lookup() -> None:
    conn = _Conn(("doc-1", hashlib.sha256(CONTENT).hexdigest(), "gold"))
    bronze.register_http_file_bronze(conn, tenant_id="t", role_id="r", title="T", added_by="a", artifact=_artifact())
    assert len(conn.cursor_.sql) == 1 and conn.cursor_.sql[0].startswith("SELECT")


class _FakeMinio:
    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    def bucket_exists(self, _bucket: str) -> bool:
        return True

    def put_object(self, *_: Any, **__: Any) -> None:
        pass

    def remove_object(self, *_: Any, **__: Any) -> None:
        pass


class _FakeQdrant:
    def __init__(self, calls: list[tuple[str, Any]], **_: Any) -> None:
        self._calls = calls

    def delete(self, *, collection_name: str, **_: Any) -> None:
        self._calls.append(("qdrant_delete", collection_name))


class _FakeNeo4jSession:
    def __init__(self, calls: list[tuple[str, Any]]) -> None:
        self._calls = calls

    def __enter__(self) -> "_FakeNeo4jSession":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute_query(self, _query: str, **kwargs: Any) -> None:
        self._calls.append(("neo4j_delete", kwargs.get("did")))


class _FakeMongoCollection:
    def __init__(self, calls: list[tuple[str, Any]]) -> None:
        self._calls = calls

    def delete_many(self, flt: dict[str, Any]) -> None:
        self._calls.append(("mongo_delete", flt))


class _FakeMongo:
    def __init__(self, calls: list[tuple[str, Any]], **_: Any) -> None:
        self._calls = calls

    def __enter__(self) -> "_FakeMongo":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def get_default_database(self) -> Any:
        return type("DB", (), {"get_collection": lambda _self, _name: _FakeMongoCollection(self._calls)})()


def test_replacing_a_promoted_document_purges_its_qdrant_neo4j_mongo_and_child_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(bronze, "Minio", lambda *a, **kw: _FakeMinio())
    monkeypatch.setattr(bronze, "QdrantClient", lambda **kw: _FakeQdrant(calls, **kw))
    monkeypatch.setattr(
        bronze, "GraphDatabase",
        type("GD", (), {"driver": staticmethod(lambda *_a, **_kw: _FakeNeo4jSession(calls))}),
    )
    monkeypatch.setattr(bronze, "MongoClient", lambda *a, **kw: _FakeMongo(calls, **kw))

    old_row = ("doc-old", "some/old/path.csv", "gold")
    conn = _Conn(old_row)
    new_artifact = FetchedArtifact(
        content_bytes=b"changed content", content_type="text/csv", source_url="https://x/y.csv", extension="csv",
    )
    data_id = bronze.register_http_file_bronze(
        conn, tenant_id="t", role_id="r", title="T", added_by="a", artifact=new_artifact,
    )

    assert data_id != "doc-old"
    kinds = [c[0] for c in calls]
    assert kinds.count("qdrant_delete") == 2  # both the chunk and doc-summary collections
    assert "neo4j_delete" in kinds and ("neo4j_delete", "doc-old") in calls
    assert "mongo_delete" in kinds

    sql = " ".join(conn.cursor_.sql)
    assert '"KBTextTable"' in sql and '"KBTextBlockVersion"' in sql and '"KBTextBlock"' in sql
    assert '"KBConflict"' in sql
    assert 'DELETE FROM "KBData"' in sql


def test_replacing_a_bronze_only_document_skips_the_promoted_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A document still at Bronze never reached Silver/Gold, so there is
    nothing in Qdrant/Neo4j/Mongo/child tables to purge — only the plain
    KBData/KBLifecycleHistory delete this always did."""
    purge_calls: list[Any] = []
    monkeypatch.setattr(bronze, "Minio", lambda *a, **kw: _FakeMinio())
    monkeypatch.setattr(bronze, "_purge_promoted_traces", lambda *a: purge_calls.append(a))

    old_row = ("doc-old", "some/old/path.csv", "bronze")
    conn = _Conn(old_row)
    new_artifact = FetchedArtifact(
        content_bytes=b"changed content", content_type="text/csv", source_url="https://x/y.csv", extension="csv",
    )
    bronze.register_http_file_bronze(conn, tenant_id="t", role_id="r", title="T", added_by="a", artifact=new_artifact)

    assert purge_calls == []
