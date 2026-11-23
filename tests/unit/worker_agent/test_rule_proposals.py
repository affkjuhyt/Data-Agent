"""DF-11: quality rules proposed from a column's profile, deterministically."""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest

from services.worker_agent.rule_proposals import propose

pytestmark = [pytest.mark.unit, pytest.mark.req("DF-11")]

DATASET = uuid.UUID(int=1)


def _kinds(name: str, kind: str = "string", **over: Any) -> dict[str, dict[str, Any]]:
    flags = {
        "is_nullable": over.pop("is_nullable", True),
        "is_key": over.pop("is_key", False),
        "personal": over.pop("personal", False),
    }
    profile = {"sample_rows": 100, "null_ratio": 0, "distinct_est": 100, **over}
    return {p.kind: p.params for p in propose(DATASET, name, kind, profile=profile, **flags)}


def test_a_code_column_with_no_nulls_is_proposed_not_null_unique_and_its_pattern() -> None:
    got = _kinds(
        "sales_order_number",
        value_patterns=[{"pattern": r"[A-Z]{2}\d{5}", "share": 1.0}],
    )
    assert got == {
        "not_null": {},
        "unique": {},
        "match_regex": {"regex": r"^[A-Z]{2}\d{5}$", "mostly": 0.99},
    }
    assert re.match(got["match_regex"]["regex"], "SO43659")


def test_a_small_category_is_proposed_as_its_set() -> None:
    category = [{"value": v, "count": 50} for v in ("M", "F")]
    got = _kinds("gender_code", distinct_est=2, category_values=category)
    assert got["in_list"] == {"values": ["F", "M"]}
    assert "unique" not in got


def test_a_personal_column_never_lists_its_values() -> None:
    category = [{"value": v, "count": 10} for v in "ABCDEFGHIJ"]
    got = _kinds("ma_kh", distinct_est=10, category_values=category, personal=True)
    assert "in_list" not in got


def test_what_the_source_promises_or_a_handful_of_rows_proposes_nothing() -> None:
    # NOT NULL in the source, or a key: the rule would say what the schema already does.
    assert "not_null" not in _kinds("id", "integer", is_nullable=False)
    assert _kinds("id", "integer", is_key=True) == {}
    # Ten rows unique by chance, a decimal unique by chance, a column with nulls.
    assert "unique" not in _kinds("code", sample_rows=10, distinct_est=10)
    assert "unique" not in _kinds("amount", "decimal")
    assert _kinds("note", null_ratio=0.2, distinct_est=80) == {}
    assert (
        propose(
            DATASET,
            "x",
            "string",
            is_nullable=True,
            is_key=False,
            personal=False,
            profile={"sample_rows": 0},
        )
        == []
    )


def test_a_pattern_needs_99_percent_and_both_regex_engines() -> None:
    assert "match_regex" not in _kinds(
        "code", value_patterns=[{"pattern": r"\d{4}", "share": 0.95}]
    )
    # The letter class of a non-ASCII value is Python's only: PostgreSQL could not run it.
    assert "match_regex" not in _kinds(
        "ten", value_patterns=[{"pattern": r"[A-Z][^\W\d_]", "share": 1.0}]
    )
    assert (
        _kinds("ma", value_patterns=[{"pattern": r"\d{3}\-\d{2}", "share": 1.0}])["match_regex"][
            "regex"
        ]
        == r"^\d{3}\-\d{2}$"
    )
