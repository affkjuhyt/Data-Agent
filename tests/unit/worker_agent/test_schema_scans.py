"""R1-03 schema profiling and deterministic join-hint rules."""

from __future__ import annotations

import pytest

from services.worker_agent.schema_scans import (
    _cardinality_for_foreign_key,
    _profile_value,
    _quote_identifier,
    pattern_hint,
    value_pattern,
    value_patterns,
)

pytestmark = [pytest.mark.unit]


def test_profile_values_are_redacted_for_sensitive_column_names() -> None:
    assert _profile_value("email", "alice@example.com") == "***"
    assert _profile_value("branch_code", "HN01") == "HN01"


def test_profile_values_keep_non_sensitive_numbers_as_text() -> None:
    assert _profile_value("balance", 1200.50) == "1200.5"
    assert _profile_value("active", True) == "True"


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


@pytest.mark.req("DF-10")
def test_a_value_pattern_names_runs_of_digits_and_letters() -> None:
    assert value_pattern("SO43659") == r"[A-Z]{2}\d{5}"
    assert value_pattern("2024-03") == r"\d{4}\-\d{2}"
    assert value_pattern("a1") == r"[a-z]\d"
    # A letter outside ASCII is a letter class: the pattern does not spell the name.
    assert value_pattern("Nguyễn Văn") == r"[A-Z][a-z]{3}[^\W\d_][a-z]\ [A-Z][^\W\d_][a-z]"
    assert "ễ" not in str(value_pattern("ễễ"))
    assert value_pattern("") is None and value_pattern("x" * 65) is None


@pytest.mark.req("DF-10")
def test_patterns_are_weighted_by_count_and_a_hint_needs_most_of_the_column() -> None:
    values = [
        {"value": "AW00011000", "count": 9},
        {"value": "AW1", "count": 1},
    ]
    patterns = value_patterns(values)
    assert patterns == [
        {"pattern": r"[A-Z]{2}\d{8}", "share": 0.9},
        {"pattern": r"[A-Z]{2}\d", "share": 0.1},
    ]
    assert pattern_hint(patterns) == r"[A-Z]{2}\d{8}"
    assert pattern_hint([{"pattern": "x", "share": 0.5}]) is None
    assert value_patterns([]) == []
