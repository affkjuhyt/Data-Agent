"""`_active_stage` decides whether a document may be promoted again.

Silver's last log line is written with status=EMBEDDING, stage="silver" and
means "done, queued for gold" — it must not block Process to Gold with a 409.
A Gold run in progress is also status=EMBEDDING but with stage="gold".
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from services.kb_backend.router.knowledge.ingestion import _active_stage

pytestmark = pytest.mark.unit


class _Col:
    def __init__(self, record: dict[str, Any] | None) -> None:
        self._record = record

    async def find_one(self, *_: Any, **__: Any) -> dict[str, Any] | None:
        return self._record


class _Mongo:
    def __init__(self, record: dict[str, Any] | None) -> None:
        self._record = record

    def get_client(self) -> dict[str, _Col]:
        return {"kb_ingestion_logs": _Col(self._record)}


def _stage(record: dict[str, Any] | None) -> str | None:
    return asyncio.run(_active_stage(_Mongo(record), "d1"))


def test_finished_silver_does_not_block_gold() -> None:
    assert _stage({"status": "EMBEDDING", "stage": "silver"}) is None


def test_gold_in_progress_still_blocks() -> None:
    assert _stage({"status": "EMBEDDING", "stage": "gold"}) == "gold"


def test_silver_in_progress_still_blocks() -> None:
    assert _stage({"status": "CHUNKING", "stage": "silver"}) == "silver"


def test_no_record_is_idle() -> None:
    assert _stage(None) is None
