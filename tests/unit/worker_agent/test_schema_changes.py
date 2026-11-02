"""A table's structure moved (worker_agent/schema_changes.py, UC-04-03): the canonical hash, and
which changes are written at once and which wait for a person."""

from __future__ import annotations

import pytest

from services.worker_agent import schema_changes as sc

pytestmark = pytest.mark.unit


def _col(name: str, data_type: str = "integer", nullable: bool = True, ordinal: int = 1) -> dict:
    norm = {"integer": "integer", "numeric": "decimal", "varchar": "string"}[data_type]
    return {
        "name": name,
        "ordinal": ordinal,
        "data_type": data_type,
        "normalized_type": norm,
        "is_nullable": nullable,
        "is_key": False,
    }


def test_the_order_the_source_lists_columns_in_is_no_change() -> None:
    a = [_col("id", ordinal=1), _col("amount", "numeric", ordinal=2)]
    b = [_col("amount", "numeric", ordinal=7), _col("id", ordinal=3)]
    assert sc.schema_hash(a) == sc.schema_hash(b)
    assert sc.diff(a, b).change is None


def test_an_added_column_or_a_looser_null_is_additive() -> None:
    before = [_col("id", nullable=False)]
    after = [_col("id", nullable=True), _col("region", "varchar")]
    d = sc.diff(before, after)
    assert d.change == "additive"
    assert d.as_json()["added"] == ["region"] and d.nullable_now == ["id"]


def test_a_removed_or_retyped_column_is_breaking() -> None:
    before = [_col("id"), _col("du_no", "numeric")]
    assert sc.diff(before, [_col("id")]).change == "breaking"
    retyped = sc.diff(before, [_col("id"), _col("du_no", "varchar")])
    assert retyped.change == "breaking"
    assert retyped.retyped == [{"name": "du_no", "before": "numeric", "after": "varchar"}]
    assert sc.diff([_col("id", nullable=True)], [_col("id", nullable=False)]).change == "breaking"
