"""The /progress stream must end for a document that no longer exists.

A re-crawl replaces a document under a new data_id and deletes the old row; a
browser still following the old id used to get "Waiting for worker..." forever,
leaving its Pipeline Activity entry stuck at 85% and a connection held open.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from services.kb_backend.router.knowledge import ingestion as ing

pytestmark = pytest.mark.unit


class _Logs:
    def __init__(self, doc: dict[str, Any] | None) -> None:
        self._doc = doc

    async def find_one(self, *_: Any, **__: Any) -> dict[str, Any] | None:
        return self._doc


class _Mongo:
    def __init__(self, doc: dict[str, Any] | None) -> None:
        self._logs = _Logs(doc)

    def get_client(self) -> dict[str, _Logs]:
        return {"kb_ingestion_logs": self._logs}


class _Request:
    def __init__(self, mongo: Any) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(mongo=mongo, postgres=object()))

    async def is_disconnected(self) -> bool:
        return False


async def _events(
    doc: dict[str, Any] | None, row: dict[str, Any] | None, successor: dict[str, Any] | None = None,
    name: str | None = None,
) -> list[dict[str, Any]]:
    async def fake_fetch(*_: Any, **__: Any) -> dict[str, Any] | None:
        return row

    async def fake_by_name(*_: Any, **__: Any) -> dict[str, Any] | None:
        return successor

    ctx = {"tenant_id": "t", "role_id": "r"}
    with patch.object(ing, "_fetch_doc", fake_fetch), patch.object(ing, "_fetch_doc_by_name", fake_by_name), \
            patch.object(ing, "svc", lambda req, attr: getattr(req.app.state, attr)):
        response = await ing.stream_progress("d1", _Request(_Mongo(doc)), name, ctx)
        out: list[dict[str, Any]] = []
        async for chunk in response.body_iterator:
            out.append(json.loads(str(chunk).removeprefix("data: ")))
            if len(out) > 5:
                break
        return out


def test_a_replaced_document_ends_the_stream_as_failed_instead_of_waiting_forever() -> None:
    events = asyncio.run(_events(doc=None, row=None))
    assert events == [{"status": "FAILED", "done": True, "message": "Document was replaced or deleted", "superseded": True, "replaced_by": None, "progress_pct": 0}]


def test_a_stale_embedding_log_does_not_keep_a_deleted_document_streaming() -> None:
    stale = {"status": "EMBEDDING", "stage": "silver", "logs": []}
    events = asyncio.run(_events(doc=stale, row=None))
    assert events and events[-1]["done"] is True and events[-1]["status"] == "FAILED"


def test_an_existing_document_still_streams_until_its_terminal_status() -> None:
    done = {"status": "PUBLISHED", "stage": "gold", "logs": [], "processed_chunks": 3, "total_chunks": 3}
    events = asyncio.run(_events(doc=done, row={"data_id": "d1"}))
    assert events[-1]["status"] == "PUBLISHED" and events[-1]["done"] is True


def test_a_replaced_document_points_the_client_at_its_successor_by_name() -> None:
    events = asyncio.run(_events(doc=None, row=None, successor={"data_id": "d2", "is_deleted": False}, name="Tỷ giá"))
    assert events[-1]["replaced_by"] == "d2" and events[-1]["superseded"] is True


def test_a_trashed_successor_is_not_followed() -> None:
    events = asyncio.run(_events(doc=None, row=None, successor={"data_id": "d2", "is_deleted": True}, name="X"))
    assert events[-1]["replaced_by"] is None
