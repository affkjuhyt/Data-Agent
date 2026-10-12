"""R1-03 schema profiling and deterministic join-hint rules."""

from __future__ import annotations

import pytest

from services.worker_agent.schema_scans import (
    _cardinality_for_foreign_key,
    _profile_value,
    _quote_identifier,
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
