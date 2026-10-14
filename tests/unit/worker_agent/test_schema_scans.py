"""UC-04-02 profile queries, without a source: what one aggregate reads, and what a column keeps."""

from __future__ import annotations

from typing import Any

import pytest

from services.worker_agent.schema_scans import (
    _cardinality_for_foreign_key,
    _from_clause,
    _profile_sql,
    _profile_table,
    _quote_identifier,
)

pytestmark = [pytest.mark.unit]


def _table(row_estimate: int = 100, kind: str = "table") -> dict[str, Any]:
    return {
        "schema_name": "credit",
        "table_name": "khach_hang",
        "object_kind": kind,
        "row_estimate": row_estimate,
        "columns": [
            {"name": "id", "normalized_type": "integer"},
            {"name": "so_cccd", "normalized_type": "string"},
            {"name": "lien_he", "normalized_type": "string"},
            {"name": "active", "normalized_type": "boolean"},
            {"name": "extra", "normalized_type": "json"},
        ],
    }


def test_identifier_quoting_does_not_allow_sql_fragments() -> None:
    assert _quote_identifier("credit", "loan") == '"credit"."loan"'
    assert _quote_identifier('credit"; DROP TABLE x', "loan") == '"credit""; DROP TABLE x"."loan"'


@pytest.mark.parametrize(
    ("child_unique", "expected"),
    [(False, "many_to_one"), (True, "one_to_one")],
)
def test_foreign_key_hint_is_proposed_with_deterministic_cardinality(
    child_unique: bool, expected: str
) -> None:
    assert _cardinality_for_foreign_key(child_unique) == expected


@pytest.mark.req("AC-04-02-06")
def test_a_profile_reads_a_sample_above_ten_million_rows() -> None:
    assert _from_clause(_table()) == '"credit"."khach_hang" AS s'
    assert _from_clause(_table(25_000_000)) == (
        '"credit"."khach_hang" AS s TABLESAMPLE SYSTEM (4.0) REPEATABLE (7)'
    )
    # A view's size is not known: it is read up to the sample size, never whole.
    assert _from_clause(_table(kind="view")) == (
        '(SELECT * FROM "credit"."khach_hang" LIMIT 1000000) AS s'
    )


def test_one_aggregate_per_table_without_values_of_personal_columns() -> None:
    sql, params = _profile_sql(_table(), masked={"so_cccd"})
    assert sql.count("FROM") == 1
    # The personal column's min and max are not even read; a boolean or json has none.
    assert 'min(s."so_cccd")' not in sql and 'min(s."active")' not in sql
    assert 'min(s."id")' in sql and 'min(s."lien_he")' in sql
    # Patterns are matched in the source on text columns, as parameters.
    assert sql.count("FILTER (WHERE") == 2 * 3 and len(params) == 6
    assert 'count(DISTINCT s."extra")' not in sql


class _Row(dict[str, Any]):
    pass


class _Conn:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = _Row(row)
        self.fetched: list[str] = []

    async def fetchrow(self, sql: str, *params: Any) -> _Row:
        return self.row

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self.fetched.append(sql)
        return [{"value": "1", "hits": 3}]


@pytest.mark.req("AC-04-02-02")
async def test_a_column_of_phone_numbers_is_labelled_and_keeps_no_value() -> None:
    row = {
        "n": 10,
        "nn0": 10, "d0": 10, "lo0": "1", "hi0": "10",
        "nn1": 10, "d1": 10, "p1_cccd": 10, "p1_phone": 0, "p1_email": 0,
        "nn2": 9, "d2": 9, "lo2": "0901", "hi2": "0988", "p2_cccd": 0, "p2_phone": 9, "p2_email": 0,
        "nn3": 10, "d3": 2,
        "nn4": 0,
    }  # fmt: skip
    conn = _Conn(row)
    out = await _profile_table(conn, _table(), masked={"so_cccd"})
    # By its values: lien_he holds phone numbers.
    assert out["lien_he"]["pattern"].label == "phone"
    assert out["lien_he"]["pattern"].hint == "số điện thoại di động"
    for personal in ("so_cccd", "lien_he"):
        assert out[personal]["top_values"] == []
        assert out[personal]["min_value"] is None and out[personal]["max_value"] is None
    assert out["id"]["min_value"] == "1" and out["id"]["top_values"] == [{"value": "1", "count": 3}]
    assert str(out["lien_he"]["null_ratio"]) == "0.1"
    # Most common values were asked for the id and the boolean only, never a personal column.
    assert len(conn.fetched) == 2 and all("so_cccd" not in q for q in conn.fetched)
    assert out["extra"]["distinct_est"] is None and out["extra"]["top_values"] == []
