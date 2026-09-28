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
    """Two different SELECTs hit this cursor for the same logical row:
    `register_http_file_bronze`'s own existing-lookup (data_id, content_hash,
    current_tier, is_deleted) and, when it falls through, `_replace_existing_by_name`'s
    (data_id, path, current_tier). `_row` is the row's fields as a dict so each
    can be projected on demand instead of one fixed tuple shape serving both.
    """

    def __init__(self, row: dict[str, Any] | None, batch_rows: list[tuple[Any, ...]] | None = None) -> None:
        self._row = row
        self._batch_rows = batch_rows or []
        self._pending_projection: tuple[Any, ...] | None = None
        self.sql: list[str] = []
        self.executions: list[tuple[str, Any]] = []

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        flat = " ".join(sql.split())
        self.sql.append(flat)
        self.executions.append((flat, params))
        if self._row is None:
            self._pending_projection = None
        elif "AND NOT is_deleted" in flat:
            self._pending_projection = (self._row["data_id"], self._row["current_tier"], self._row["content_hash"])
        elif "is_deleted" in flat:
            self._pending_projection = (
                self._row["data_id"], self._row["content_hash"], self._row["current_tier"], self._row["is_deleted"],
                self._row.get("path"),
            )
        elif 'SELECT data_id, path, current_tier' in flat:
            self._pending_projection = (self._row["data_id"], self._row.get("path"), self._row["current_tier"])

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._pending_projection

    def fetchall(self) -> list[Any]:
        return self._batch_rows  # no KBConflict batches unless a test opts in


class _Conn:
    def __init__(self, row: dict[str, Any] | None, batch_rows: list[tuple[Any, ...]] | None = None) -> None:
        self.cursor_ = _Cursor(row, batch_rows)

    def cursor(self) -> _Cursor:
        return self.cursor_

    def commit(self) -> None:
        pass


def _artifact() -> FetchedArtifact:
    return FetchedArtifact(content_bytes=CONTENT, content_type="text/csv", source_url="https://x/y.csv", extension="csv")


def test_unchanged_rerun_reports_the_kept_document_and_its_tier() -> None:
    row = {"data_id": "doc-1", "content_hash": hashlib.sha256(CONTENT).hexdigest(), "current_tier": "silver", "is_deleted": False}
    outcome: dict[str, Any] = {}
    data_id = bronze.register_http_file_bronze(
        _Conn(row), tenant_id="t", role_id="r", title="T", added_by="a", artifact=_artifact(), outcome=outcome,
    )
    assert data_id == "doc-1"
    assert outcome == {"unchanged": True, "existing_tier": "silver"}


def test_unchanged_rerun_touches_nothing_but_the_lookup() -> None:
    conn = _Conn({"data_id": "doc-1", "content_hash": hashlib.sha256(CONTENT).hexdigest(), "current_tier": "gold", "is_deleted": False})
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


def test_replacing_a_trashed_promoted_document_purges_its_qdrant_neo4j_mongo_and_child_rows(
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

    old_row = {"data_id": "doc-old", "path": "some/old/path.csv", "content_hash": "stale-hash", "current_tier": "gold", "is_deleted": True}
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


def test_replacing_a_trashed_document_with_conflict_batches_casts_the_uuid_array(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: psycopg2 adapts a plain Python list to `text[]`, which
    Postgres refuses against the `uuid` batch_id column ("operator does not
    exist: uuid = text") — this broke every scheduled re-crawl of a document
    that had recorded conflicts (RSS Kinh tế, FRED, ...) until the `::uuid[]`
    cast was added. `fetchall()` must return at least one batch row for this
    path to run at all — the pre-fix test suite always returned `[]` here,
    which is why it shipped without being caught."""
    monkeypatch.setattr(bronze, "Minio", lambda *a, **kw: _FakeMinio())
    monkeypatch.setattr(bronze, "QdrantClient", lambda **kw: _FakeQdrant([]))
    monkeypatch.setattr(
        bronze, "GraphDatabase",
        type("GD", (), {"driver": staticmethod(lambda *_a, **_kw: _FakeNeo4jSession([]))}),
    )
    monkeypatch.setattr(bronze, "MongoClient", lambda *a, **kw: _FakeMongo([]))

    batch_id = "a4111111-2222-3333-4444-555555555555"
    old_row = {"data_id": "doc-old", "path": "some/old/path.csv", "content_hash": "stale-hash", "current_tier": "gold", "is_deleted": True}
    conn = _Conn(old_row, batch_rows=[(batch_id,)])
    new_artifact = FetchedArtifact(
        content_bytes=b"changed content", content_type="text/csv", source_url="https://x/y.csv", extension="csv",
    )
    bronze.register_http_file_bronze(conn, tenant_id="t", role_id="r", title="T", added_by="a", artifact=new_artifact)

    batch_delete = next(
        (sql, params) for sql, params in conn.cursor_.executions if sql.startswith('DELETE FROM "KBConflictBatch"')
    )
    assert "ANY(%s::uuid[])" in batch_delete[0]
    assert batch_delete[1] == ([batch_id],)


def test_replacing_a_trashed_bronze_only_document_skips_the_promoted_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A document still at Bronze never reached Silver/Gold, so there is
    nothing in Qdrant/Neo4j/Mongo/child tables to purge — only the plain
    KBData/KBLifecycleHistory delete this always did."""
    purge_calls: list[Any] = []
    monkeypatch.setattr(bronze, "Minio", lambda *a, **kw: _FakeMinio())
    monkeypatch.setattr(bronze, "_purge_promoted_traces", lambda *a: purge_calls.append(a))

    old_row = {"data_id": "doc-old", "path": "some/old/path.csv", "content_hash": "stale-hash", "current_tier": "bronze", "is_deleted": True}
    conn = _Conn(old_row)
    new_artifact = FetchedArtifact(
        content_bytes=b"changed content", content_type="text/csv", source_url="https://x/y.csv", extension="csv",
    )
    bronze.register_http_file_bronze(conn, tenant_id="t", role_id="r", title="T", added_by="a", artifact=new_artifact)

    assert purge_calls == []


class _RecordingMinio(_FakeMinio):
    def __init__(self, events: list[tuple[str, str]]) -> None:
        self._events = events

    def put_object(self, _bucket: str, key: str, *_: Any, **__: Any) -> None:
        self._events.append(("put", key))

    def remove_object(self, _bucket: str, key: str) -> None:
        self._events.append(("remove", key))


_LIVE_GOLD = {
    "data_id": "doc-live", "path": "t/bronze/doc-live/T.csv", "content_hash": "stale-hash",
    "current_tier": "gold", "is_deleted": False,
}
_CHANGED = FetchedArtifact(
    content_bytes=b"changed content", content_type="text/csv", source_url="https://x/y.csv", extension="csv",
)


def test_changed_content_updates_the_live_document_in_place_and_keeps_its_data_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A re-crawl used to delete the row and insert a new data_id, stranding
    anything still following the old one (a Pipeline Activity entry stuck at
    85%) and removing the old content before the new one was usable."""
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(bronze, "Minio", lambda *a, **kw: _RecordingMinio(events))
    conn = _Conn(dict(_LIVE_GOLD))

    data_id = bronze.register_http_file_bronze(
        conn, tenant_id="t", role_id="r", title="T", added_by="a", artifact=_CHANGED,
    )

    assert data_id == "doc-live"
    sql = " ".join(conn.cursor_.sql)
    assert 'DELETE FROM "KBData"' not in sql
    assert 'INSERT INTO "KBData"' not in sql
    update = next(s for s in conn.cursor_.sql if s.startswith('UPDATE "KBData"'))
    assert "current_tier" not in update  # what Silver/Gold produced keeps serving
    assert "path = %s" in update and "content_hash = %s" in update
    assert "Content updated in place" in str(conn.cursor_.executions)


def test_the_old_object_is_removed_only_after_the_new_one_is_written(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(bronze, "Minio", lambda *a, **kw: _RecordingMinio(events))
    bronze.register_http_file_bronze(
        _Conn(dict(_LIVE_GOLD)), tenant_id="t", role_id="r", title="T", added_by="a", artifact=_CHANGED,
    )
    assert [kind for kind, _ in events] == ["put", "remove"]
    assert events[0][1].startswith("t/bronze/doc-live/") and events[0][1] != _LIVE_GOLD["path"]
    assert events[1][1] == _LIVE_GOLD["path"]


def test_a_failed_update_keeps_the_old_version_and_drops_the_new_object(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(bronze, "Minio", lambda *a, **kw: _RecordingMinio(events))

    class _FailingConn(_Conn):
        rolled_back = False

        def commit(self) -> None:
            raise RuntimeError("db went away")

        def rollback(self) -> None:
            self.rolled_back = True

    conn = _FailingConn(dict(_LIVE_GOLD))
    with pytest.raises(RuntimeError):
        bronze.register_http_file_bronze(
            conn, tenant_id="t", role_id="r", title="T", added_by="a", artifact=_CHANGED,
        )
    removed = [key for kind, key in events if kind == "remove"]
    assert conn.rolled_back and removed and _LIVE_GOLD["path"] not in removed


def test_a_web_source_reuses_its_existing_document_instead_of_replacing_it() -> None:
    conn = _Conn({"data_id": "web-1", "current_tier": "gold", "content_hash": None, "is_deleted": False})
    data_id = bronze.register_web_bronze(
        conn, tenant_id="t", role_id="r", title="T", url="https://x", added_by="a",
    )
    assert data_id == "web-1"
    sql = " ".join(conn.cursor_.sql)
    assert 'DELETE FROM "KBData"' not in sql and 'INSERT INTO "KBData"' not in sql
    update = next(s for s in conn.cursor_.sql if s.startswith('UPDATE "KBData"'))
    assert "path = %s" not in update  # nothing stored for a web source


def _web_artifact(text: bytes = b"# Page\n\nsame text") -> FetchedArtifact:
    return FetchedArtifact(content_bytes=text, content_type="text/markdown", source_url="https://x", extension="md")


def test_an_unchanged_web_page_is_not_reprocessed() -> None:
    artifact = _web_artifact()
    conn = _Conn({"data_id": "web-1", "current_tier": "gold", "content_hash": hashlib.sha256(artifact.content_bytes).hexdigest(), "is_deleted": False})
    outcome: dict[str, Any] = {}
    data_id = bronze.register_web_bronze(
        conn, tenant_id="t", role_id="r", title="T", url="https://x", added_by="a", artifact=artifact, outcome=outcome,
    )
    assert data_id == "web-1"
    assert outcome == {"unchanged": True, "existing_tier": "gold"}
    assert len(conn.cursor_.sql) == 1 and conn.cursor_.sql[0].startswith("SELECT")  # nothing written


def test_a_changed_web_page_is_updated_in_place_with_its_new_hash() -> None:
    artifact = _web_artifact(b"# Page\n\nnew text")
    conn = _Conn({"data_id": "web-1", "current_tier": "gold", "content_hash": "old-hash", "is_deleted": False})
    outcome: dict[str, Any] = {}
    bronze.register_web_bronze(
        conn, tenant_id="t", role_id="r", title="T", url="https://x", added_by="a", artifact=artifact, outcome=outcome,
    )
    assert "unchanged" not in outcome
    sql, params = next((s, p) for s, p in conn.cursor_.executions if s.startswith('UPDATE "KBData"'))
    assert "content_hash = %s" in sql and "path = %s" not in sql
    assert hashlib.sha256(artifact.content_bytes).hexdigest() in params


def test_a_web_page_stored_before_hashing_existed_is_processed_once() -> None:
    artifact = _web_artifact()
    conn = _Conn({"data_id": "web-1", "current_tier": "gold", "content_hash": None, "is_deleted": False})
    outcome: dict[str, Any] = {}
    bronze.register_web_bronze(
        conn, tenant_id="t", role_id="r", title="T", url="https://x", added_by="a", artifact=artifact, outcome=outcome,
    )
    assert "unchanged" not in outcome
    assert any(s.startswith('UPDATE "KBData"') for s, _ in conn.cursor_.executions)

