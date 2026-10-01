"""Bronze registration behaviour for scheduled web-crawler sources.

These tests intentionally stay above SQL string details: bronze.py is an ORM
writer, so the contract is the row/object behaviour and MinIO compensation.
"""
from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import KBLifecycleHistoryORM
from libs.ingestion.connectors.base import FetchedArtifact
from services.platform_workers.workers.web_crawler import bronze

pytestmark = pytest.mark.unit

TENANT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ROLE_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
DATA_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
CONTENT = b"identical bytes"


class _FakeSession:
    def __init__(self, existing: Any | None = None, *, fail_commit: bool = False) -> None:
        self.existing = existing
        self.fail_commit = fail_commit
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0

    def scalar(self, _stmt: Any) -> Any | None:
        return self.existing

    def get(self, _model: Any, key: Any) -> Any | None:
        if self.existing is not None and key == self.existing.data_id:
            return self.existing
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def execute(self, _stmt: Any) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("db went away")

    def rollback(self) -> None:
        self.rollbacks += 1


class _RecordingMinio:
    def __init__(self, events: list[tuple[str, str]]) -> None:
        self._events = events

    def bucket_exists(self, _bucket: str) -> bool:
        return True

    def put_object(self, _bucket: str, key: str, *_: Any, **__: Any) -> None:
        self._events.append(("put", key))

    def remove_object(self, _bucket: str, key: str) -> None:
        self._events.append(("remove", key))


def _row(**overrides: Any) -> SimpleNamespace:
    fields = {
        "data_id": DATA_ID,
        "path": f"{TENANT_ID}/bronze/{DATA_ID}/T.csv",
        "content_hash": "old-hash",
        "current_tier": "gold",
        "is_deleted": False,
        "doc_metadata": {"last_pipeline_status": "succeeded", "kept": True},
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _artifact(content: bytes = CONTENT) -> FetchedArtifact:
    return FetchedArtifact(content_bytes=content, content_type="text/csv", source_url="https://x/y.csv", extension="csv")


def test_unchanged_file_reports_the_kept_document_and_does_not_write(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(bronze, "Minio", lambda *a, **kw: _RecordingMinio(events))
    existing = _row(content_hash=hashlib.sha256(CONTENT).hexdigest(), current_tier="silver")
    session = _FakeSession(existing)
    outcome: dict[str, Any] = {}

    data_id = bronze.register_http_file_bronze(
        session, tenant_id=TENANT_ID, role_id=ROLE_ID, title="T", added_by="a", artifact=_artifact(), outcome=outcome,
    )

    assert data_id == str(DATA_ID)
    assert outcome == {"unchanged": True, "existing_tier": "silver"}
    assert session.added == [] and session.commits == 0 and events == []


def test_changed_file_updates_the_live_document_in_place_and_removes_old_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(bronze, "Minio", lambda *a, **kw: _RecordingMinio(events))
    existing = _row()
    session = _FakeSession(existing)

    data_id = bronze.register_http_file_bronze(
        session, tenant_id=TENANT_ID, role_id=ROLE_ID, title="T", added_by="a", artifact=_artifact(b"changed"),
    )

    assert data_id == str(DATA_ID)
    assert existing.content_hash == hashlib.sha256(b"changed").hexdigest()
    assert existing.path != f"{TENANT_ID}/bronze/{DATA_ID}/T.csv"
    assert existing.current_tier == "gold"
    assert existing.doc_metadata["kept"] is True
    assert "last_pipeline_status" not in existing.doc_metadata
    assert any(isinstance(obj, KBLifecycleHistoryORM) and obj.from_tier == "gold" for obj in session.added)
    assert [kind for kind, _ in events] == ["put", "remove"]


def test_failed_file_update_rolls_back_and_removes_the_new_object(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(bronze, "Minio", lambda *a, **kw: _RecordingMinio(events))
    old_path = f"{TENANT_ID}/bronze/{DATA_ID}/T.csv"
    session = _FakeSession(_row(path=old_path), fail_commit=True)

    with pytest.raises(RuntimeError):
        bronze.register_http_file_bronze(
            session, tenant_id=TENANT_ID, role_id=ROLE_ID, title="T", added_by="a", artifact=_artifact(b"changed"),
        )

    removed = [key for kind, key in events if kind == "remove"]
    assert session.rollbacks == 1
    assert removed and old_path not in removed


def test_unchanged_web_page_is_not_reprocessed() -> None:
    artifact = FetchedArtifact(content_bytes=b"# Page\nsame", content_type="text/markdown", source_url="https://x", extension="md")
    session = _FakeSession(_row(content_hash=hashlib.sha256(artifact.content_bytes).hexdigest()))
    outcome: dict[str, Any] = {}

    data_id = bronze.register_web_bronze(
        session, tenant_id=TENANT_ID, role_id=ROLE_ID, title="T", url="https://x", added_by="a",
        artifact=artifact, outcome=outcome,
    )

    assert data_id == str(DATA_ID)
    assert outcome == {"unchanged": True, "existing_tier": "gold"}
    assert session.added == [] and session.commits == 0
