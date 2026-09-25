"""Only Production data may reach Gold; Training and Evaluation stop at Silver.

The guard lives in `promote_to_gold` (the one function every path — the
manual button, the auto-chain from Silver, a replayed Kafka event — ends up
calling), so a stale caller cannot put such a document into the knowledge base.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from libs.ingestion import purpose as purpose_module
from libs.ingestion.gold import orchestrator
from libs.ingestion.purpose import reaches_gold

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("purpose", "expected"),
    [("production", True), ("training", False), ("evaluation", False), (None, True), ("", True)],
)
def test_only_production_reaches_gold(purpose: str | None, expected: bool) -> None:
    assert reaches_gold(purpose) is expected


def test_every_known_purpose_has_a_ceiling() -> None:
    assert set(purpose_module.MAX_TIER_BY_PURPOSE) == {"production", "training", "evaluation"}


def _promote(purpose: str, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], MagicMock]:
    monkeypatch.setattr(orchestrator, "_get_data_row", AsyncMock(return_value=("Doc", "KB", purpose)))
    stamp = AsyncMock()
    monkeypatch.setattr(orchestrator, "stamp_pipeline_status", stamp)
    mongo = MagicMock()
    result = asyncio.run(orchestrator.promote_to_gold(
        postgres=MagicMock(), qdrant=MagicMock(), graph=MagicMock(), mongo=mongo, model=MagicMock(),
        data_id="11111111-1111-1111-1111-111111111111", tenant_id="22222222-2222-2222-2222-222222222222",
        approved_by="tester",
    ))
    return result, mongo


@pytest.mark.parametrize("purpose", ["training", "evaluation"])
def test_gold_is_skipped_before_anything_is_written(purpose: str, monkeypatch: pytest.MonkeyPatch) -> None:
    result, mongo = _promote(purpose, monkeypatch)

    assert result["layer"] == "silver"
    assert result["skipped"] == f"purpose_{purpose}"
    mongo.get_client.assert_not_called()  # no staging read, no Mongo log, no Qdrant/graph work
    orchestrator.stamp_pipeline_status.assert_not_called()
