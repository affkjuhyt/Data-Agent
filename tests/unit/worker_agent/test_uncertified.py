"""S1 (semantic/uncertified.py): a measure or draft metric is offered only on what the asker may
read: a table they read, no column masked for them (the security review of 24/11)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic import uncertified as un

pytestmark = [pytest.mark.unit]

ENTITY, DATASET = uuid.uuid4(), uuid.uuid4()


def _layer(masked: frozenset[str] = frozenset(), datasets: set[uuid.UUID] | None = None) -> object:
    return SimpleNamespace(
        layer=SimpleNamespace(
            measures={"phi": c.Measure("phi", "SUM(fee_amount)", ENTITY)},
            entity_tables={ENTITY: c.Table(DATASET, "public", "fact_fee")},
        ),
        datasets={DATASET: object()} if datasets is None else datasets,
        restricted_columns={DATASET: masked} if masked else {},
    )


def test_a_measure_on_a_readable_table_is_offered() -> None:
    assert un._readable("phi", _layer())  # type: ignore[arg-type]


def test_a_measure_reading_a_masked_column_is_withheld() -> None:
    assert not un._readable("phi", _layer(frozenset({"fee_amount"})))  # type: ignore[arg-type]


def test_a_measure_on_a_withheld_table_is_not_offered() -> None:
    assert not un._readable("phi", _layer(datasets=set()))  # type: ignore[arg-type]
    assert not un._readable("unknown_code", _layer())  # type: ignore[arg-type]
