"""Unit tests for backend/services/ankt_connectors/promote.py.

The tiers are the part of the pipeline a person reads off a screen, so what
matters here is not that a function ran but that the record it leaves is true:
the right pair of transitions, in order, saying which store holds the output
and why nothing was chunked or embedded.
"""
from __future__ import annotations

import pytest

from services.ankt_connectors.promote import promote

pytestmark = pytest.mark.unit


class _FakePostgres:
    """Records tier updates and lifecycle inserts instead of performing them."""

    def __init__(self) -> None:
        self.inserted: list[object] = []

    async def insert(self, payload: object) -> None:
        self.inserted.append(payload)

    def get_client(self):  # noqa: ANN201 - async context manager stub
        raise AssertionError("_set_tier should be patched out in these tests")


@pytest.fixture
def recorded(monkeypatch) -> tuple[_FakePostgres, list[str]]:
    tiers: list[str] = []

    async def _fake_set_tier(_postgres, _data_id, tier) -> None:
        tiers.append(tier.value)

    monkeypatch.setattr("services.ankt_connectors.promote._set_tier", _fake_set_tier)
    return _FakePostgres(), tiers


async def _run(postgres, **overrides):
    kwargs = {
        "data_id": "11111111-1111-1111-1111-111111111111",
        "source_code": "imf-weo",
        "series_key": "VNM.GGXWDG.A",
        "variable_code": "debt_general_government",
        "observations_written": 12,
        "periods": ("2013", "2024"),
        "release_id": "22222222-2222-2222-2222-222222222222",
    }
    kwargs.update(overrides)
    await promote(postgres, **kwargs)


@pytest.mark.asyncio
async def test_the_artifact_walks_bronze_to_silver_to_gold(recorded) -> None:
    postgres, tiers = recorded
    await _run(postgres)
    assert tiers == ["silver", "gold"]


@pytest.mark.asyncio
async def test_each_step_names_the_tier_it_came_from(recorded) -> None:
    """A history row with no `from_tier` cannot be drawn as a step."""
    postgres, _ = recorded
    await _run(postgres)
    steps = [(row.from_tier, row.to_tier) for row in postgres.inserted]
    assert steps == [("bronze", "silver"), ("silver", "gold")]


@pytest.mark.asyncio
async def test_silver_says_where_the_extraction_landed(recorded) -> None:
    """The count is only meaningful next to the store that holds the rows."""
    postgres, _ = recorded
    await _run(postgres)
    silver = postgres.inserted[0]
    assert "12 observations" in silver.notes
    assert "2013..2024" in silver.notes
    assert silver.pipeline_metadata["store"] == "ankt.observation"


@pytest.mark.asyncio
async def test_zero_chunks_is_recorded_as_a_decision(recorded) -> None:
    """Silver with no chunks and no reason reads as a stage that failed.

    A structured series is deliberately not chunked, and the note has to say so
    — otherwise the next person to look at this document sees an empty silver
    stage and reruns the document pipeline over a JSON array of numbers.
    """
    postgres, _ = recorded
    await _run(postgres)
    meta = postgres.inserted[0].pipeline_metadata
    assert meta["chunks_accepted"] == 0
    assert meta["chunking_skipped"]


@pytest.mark.asyncio
async def test_gold_records_that_nothing_was_embedded(recorded) -> None:
    postgres, _ = recorded
    await _run(postgres)
    meta = postgres.inserted[1].pipeline_metadata
    assert meta["embedded"] is False
    assert meta["embedding_skipped"]
    assert meta["observations_published"] == 12


@pytest.mark.asyncio
async def test_both_steps_carry_the_release(recorded) -> None:
    """Without it the tier history cannot be tied back to a vintage, which is
    the whole basis of a point-in-time read."""
    postgres, _ = recorded
    await _run(postgres)
    assert all(
        row.pipeline_metadata["release_id"] == "22222222-2222-2222-2222-222222222222"
        for row in postgres.inserted
    )


@pytest.mark.asyncio
async def test_a_series_with_no_periods_still_promotes(recorded) -> None:
    """An empty span is not a failure: the response was stored and it was
    genuinely empty, and a crash here would leave the artifact at bronze with
    no explanation."""
    postgres, tiers = recorded
    await _run(postgres, periods=None, observations_written=0)
    assert tiers == ["silver", "gold"]
    assert "none" in postgres.inserted[0].notes
