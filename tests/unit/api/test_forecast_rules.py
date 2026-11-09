"""What a formula model may cite of its certified metric expression (BR-183)."""

from __future__ import annotations

import pytest

from services.api.modules.forecast import rules

pytestmark = pytest.mark.unit


def test_a_formula_may_cite_the_columns_of_the_expression_only() -> None:
    # A pattern over the text let a formula cite CASE, WHEN or a word inside a literal ('paid').
    expression = "SUM(CASE WHEN status = 'paid' THEN amount ELSE 0 END) / NULLIF(COUNT(orders), 0)"
    assert rules.expression_names(expression) == {"status", "amount", "orders"}


def test_an_expression_that_does_not_parse_lets_a_formula_cite_nothing() -> None:
    assert rules.expression_names("amount /") == set()
