"""An uploaded file's bytes land in Bronze in the same request (ingestion.py's
ingest_bronze) — record_upload_as_source only gives it a row in Source
Registry, for list/run-history parity with every other source type (Phase C
addendum). Re-uploading the same title must reuse that source, not pile up a
new one per upload.
"""
from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest

from services.kb_backend.operations import source_ops
from services.kb_backend.operations.source_ops import record_upload_as_source

pytestmark = pytest.mark.unit


class _FakeSession:
    def __init__(self, existing_source_id: Any = None) -> None:
        self.existing_source_id = existing_source_id
        self.added: list[Any] = []
        self.committed = False

    async def scalar(self, *_: Any, **__: Any) -> Any:
        return self.existing_source_id

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        if isinstance(obj, source_ops.KBSourceORM) and obj.source_id is None:
            obj.source_id = uuid.uuid4()

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True


class _FakePostgres:
    def __init__(self, existing_source_id: Any = None) -> None:
        self.session = _FakeSession(existing_source_id)

    @asynccontextmanager
    async def get_client(self) -> Any:
        yield self.session


def test_a_first_upload_creates_a_source_config_and_a_successful_run() -> None:
    postgres = _FakePostgres(existing_source_id=None)
    asyncio.run(record_upload_as_source(
        postgres, tenant_id=str(uuid.uuid4()), role_id=str(uuid.uuid4()),
        title="My Report.pdf", created_by="u", data_id=str(uuid.uuid4()),
    ))
    kinds = [type(o).__name__ for o in postgres.session.added]
    assert kinds == ["KBSourceORM", "KBSourceConfigORM", "KBSourceRunORM"]
    run = postgres.session.added[-1]
    assert run.status == "success" and run.trigger == "manual" and run.finished_at is not None
    assert postgres.session.committed is True


def test_a_re_upload_reuses_the_existing_source_and_only_adds_a_run() -> None:
    existing = uuid.uuid4()
    postgres = _FakePostgres(existing_source_id=existing)
    asyncio.run(record_upload_as_source(
        postgres, tenant_id=str(uuid.uuid4()), role_id=str(uuid.uuid4()),
        title="My Report.pdf", created_by="u", data_id=str(uuid.uuid4()),
    ))
    kinds = [type(o).__name__ for o in postgres.session.added]
    assert kinds == ["KBSourceRunORM"]
    assert postgres.session.added[0].source_id == existing
