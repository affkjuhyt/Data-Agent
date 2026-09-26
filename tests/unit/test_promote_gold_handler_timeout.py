"""kb_promote_gold's handler: a stalled promote_to_gold call must not hold its
concurrency slot forever — same fix as kb_promote_silver's handler, see
test_promote_silver_handler_resilience.py.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from libs.ingestion import config as cfg
from libs.ingestion.runtime import WorkerRuntime
from services.kb_promote_gold.libs import handler as handler_module

pytestmark = pytest.mark.unit

DATA_ID = "11111111-1111-1111-1111-111111111111"
TENANT_ID = "22222222-2222-2222-2222-222222222222"


def _runtime() -> WorkerRuntime:
    runtime = WorkerRuntime(logger=AsyncMock(), stage_record=AsyncMock())
    runtime.mongo = MagicMock(get_client=MagicMock(return_value=MagicMock()))
    runtime.qdrant = MagicMock(get_client=MagicMock(return_value=MagicMock()))
    return runtime


@asynccontextmanager
async def _noop_lock(*_: Any, **__: Any) -> Any:
    yield


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handler_module, "set_current_tenant", lambda *_: None)
    monkeypatch.setattr(handler_module, "document_name", AsyncMock(return_value="Doc"))
    monkeypatch.setattr(handler_module, "document_lock", _noop_lock)
    monkeypatch.setattr(handler_module, "reset_events_for_new_run", AsyncMock())
    monkeypatch.setattr(handler_module.PipelineEventEmitter, "create", AsyncMock())


async def _stalls_forever(**_: Any) -> None:
    await asyncio.sleep(3600)


def test_a_stalled_gold_call_is_stamped_failed_and_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(cfg, "GOLD_STAGE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(handler_module, "promote_to_gold", _stalls_forever)
    stamp = AsyncMock()
    monkeypatch.setattr(handler_module, "stamp_pipeline_status", stamp)
    retry = AsyncMock()
    monkeypatch.setattr(handler_module, "route_ingestion_failure", retry)

    runtime = _runtime()
    handle = handler_module.build_gold_handler(runtime)
    asyncio.run(handle({"data_id": DATA_ID, "tenant_id": TENANT_ID, "approved_by": "x"}))

    stamp.assert_awaited_once()
    assert stamp.call_args.kwargs["stage"] == "gold"
    assert stamp.call_args.kwargs["status"] == "failed"
    assert "timed out" in stamp.call_args.kwargs["error"]
    retry.assert_awaited_once()
    assert retry.call_args.kwargs["stage"] == "gold"
    assert retry.call_args.kwargs["source_topic"] == cfg.KAFKA_TOPIC_GOLD


def test_a_successful_run_stamps_and_retries_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(cfg, "GOLD_STAGE_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(handler_module, "promote_to_gold", AsyncMock(return_value=None))
    stamp = AsyncMock()
    monkeypatch.setattr(handler_module, "stamp_pipeline_status", stamp)
    retry = AsyncMock()
    monkeypatch.setattr(handler_module, "route_ingestion_failure", retry)

    runtime = _runtime()
    handle = handler_module.build_gold_handler(runtime)
    asyncio.run(handle({"data_id": DATA_ID, "tenant_id": TENANT_ID, "approved_by": "x"}))

    stamp.assert_not_called()
    retry.assert_not_called()
