"""Run Now queues a run row; the source list reports when each source runs next."""
from __future__ import annotations

import asyncio
import datetime
import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from services.kb_backend.operations import source_ops

pytestmark = pytest.mark.unit

UTC = datetime.timezone.utc


def _now() -> datetime.datetime:
    return datetime.datetime.now(UTC)


class _Session:
    def __init__(self, active_run: Any) -> None:
        self._active_run = active_run
        self.added: list[Any] = []
        self.commits = 0

    async def scalar(self, *_: Any, **__: Any) -> Any:
        return self._active_run

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            obj.run_id = uuid.uuid4()  # the ORM's Python-side default, applied on flush

    async def commit(self) -> None:
        self.commits += 1


class _Postgres:
    def __init__(self, session: _Session) -> None:
        self.session = session

    @asynccontextmanager
    async def get_client(self) -> Any:
        yield self.session


def _queue(active_run: Any) -> tuple[dict[str, Any] | None, _Session, HTTPException | None]:
    session = _Session(active_run)

    async def _run() -> dict[str, Any]:
        with patch.object(source_ops, "_source_row", return_value={"source_id": uuid.uuid4()}):
            return await source_ops.queue_run_now(_Postgres(session), str(uuid.uuid4()), str(uuid.uuid4()))

    try:
        return asyncio.run(_run()), session, None
    except HTTPException as exc:
        return None, session, exc


def test_run_now_records_a_queued_manual_run() -> None:
    result, session, error = _queue(active_run=None)
    assert error is None and result is not None
    assert result["status"] == "queued" and uuid.UUID(result["run_id"])
    run = session.added[0]
    assert run.status == "queued" and run.trigger == "manual"
    assert session.commits == 1


def test_run_now_is_refused_while_the_source_is_already_queued_or_running() -> None:
    result, session, error = _queue(active_run=uuid.uuid4())
    assert result is None and error is not None and error.status_code == 409
    assert session.added == [] and session.commits == 0


def test_manual_only_source_has_no_next_run() -> None:
    assert source_ops._next_run_at(None, _now()) is None
    assert source_ops._next_run_at("", _now()) is None
    assert source_ops._next_run_at("   ", None) is None


def test_scheduled_source_that_never_ran_has_not_started() -> None:
    assert source_ops._next_run_at("0 3 * * *", None) is None


def test_next_run_follows_the_last_run() -> None:
    last = _now()
    value = datetime.datetime.fromisoformat(source_ops._next_run_at("*/15 * * * *", last) or "")
    delta = (value - last).total_seconds()
    assert 0 < delta <= 15 * 60


def test_overdue_source_reports_now_not_a_past_time() -> None:
    long_ago = _now() - datetime.timedelta(days=3)
    value = datetime.datetime.fromisoformat(source_ops._next_run_at("0 3 * * *", long_ago) or "")
    assert value >= _now() - datetime.timedelta(seconds=5)


def test_invalid_cron_yields_none() -> None:
    assert source_ops._next_run_at("not a cron", _now()) is None
