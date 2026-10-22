"""The query each quality rule counts its violations with (UC-06-07)."""

from __future__ import annotations

import pytest

from libs.dataagent_db.models import ingest
from services.worker_agent.quality import violation_sql


def _rule(kind: str, column: str | None, params: dict[str, object]) -> ingest.QualityRule:
    return ingest.QualityRule(kind=kind, column_name=column, params=params)


def test_not_null_counts_every_row_by_default() -> None:
    sql = violation_sql(_rule("not_null", "gioi_tinh", {}), '"ws"."kh"')
    assert sql == 'SELECT count(*) AS n FROM "ws"."kh" WHERE "gioi_tinh" IS NULL'


def test_not_null_with_where_counts_only_those_groups() -> None:
    # A company has no gender: the column is required of individuals only.
    sql = violation_sql(
        _rule("not_null", "gioi_tinh", {"where": {"customer_segment": ["Cá nhân", "Hộ KD"]}}),
        '"ws"."kh"',
    )
    assert sql == (
        'SELECT count(*) AS n FROM "ws"."kh" WHERE "gioi_tinh" IS NULL'
        " AND CAST(\"customer_segment\" AS text) IN ('Cá nhân', 'Hộ KD')"
    )


def test_a_where_without_values_is_refused() -> None:
    with pytest.raises(ValueError):
        violation_sql(_rule("not_null", "gioi_tinh", {"where": {"segment": []}}), "t")


def test_a_quote_in_a_value_stays_a_literal() -> None:
    sql = violation_sql(_rule("in_list", "x", {"values": ["a'; DROP TABLE t; --"]}), "t")
    assert "'a''; DROP TABLE t; --'" in sql
