"""The data-quality vocabulary shared by the api (a rule's shape) and worker-agent (its check), DF-07.

Each `kind` of ingest.quality_rule is one expectation of Great Expectations Core 1.x, by name
only (GX is not installed: DATA_PIPELINE_SPEC §2 C), and its result is kept in GX's shape. Two
parameters apply to every rule that counts rows:

- `mostly` (0 < mostly ≤ 1): the rule passes while at most 1 - mostly of the rows it looks at
  break it. Absent, a single violation fails the rule, as every rule did before DF-07.
- `row_condition`: the rows a rule looks at, as columns compared with constants joined by AND
  (`segment = 'Cá nhân' AND amount > 0`). It is parsed and rebuilt by sqlglot, never pasted: no
  function, subquery or other table can enter the check.
"""

from __future__ import annotations

import decimal
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp


@dataclass(frozen=True)
class Expectation:
    name: str  # GX Core's
    column: bool  # names one column (column_name)
    counts_rows: bool  # `mostly` and `row_condition` apply


EXPECTATIONS: dict[str, Expectation] = {
    "not_null": Expectation("expect_column_values_to_not_be_null", True, True),
    "range": Expectation("expect_column_values_to_be_between", True, True),
    "unique": Expectation("expect_column_values_to_be_unique", True, True),
    "in_list": Expectation("expect_column_values_to_be_in_set", True, True),
    "match_regex": Expectation("expect_column_values_to_match_regex", True, True),
    "compound_unique": Expectation("expect_compound_columns_to_be_unique", False, True),
    # A total off its expected value: one violation or none (GX's column sum between bounds).
    "sum_matches": Expectation("expect_column_sum_to_be_between", True, False),
    # The newest value against now - max_age_hours (GX: column max between, a moving bound).
    "freshness": Expectation("expect_column_max_to_be_between", True, False),
    "custom_sql": Expectation("expect_query_to_return_no_rows", False, False),
}
KINDS = frozenset(EXPECTATIONS)
MAX_REGEX = 500
_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


class RuleInvalid(ValueError):
    """A rule's parameters do not make a check."""


def mostly(params: Mapping[str, Any]) -> float | None:
    """`mostly`, or None when every row must pass."""
    value = params.get("mostly")
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RuleInvalid("mostly is a number") from exc
    if not 0 < number <= 1:
        raise RuleInvalid("mostly is above 0 and at most 1")
    return None if number == 1 else number


def passes(violations: int, total: int | None, threshold: float | None) -> bool:
    """None as threshold: no violation at all. Else at most 1 - mostly of the rows break it."""
    if threshold is None or not total:
        return violations == 0
    return decimal.Decimal(violations) / decimal.Decimal(total) <= 1 - decimal.Decimal(
        str(threshold)
    )


def row_condition(text: str) -> exp.Expr:
    """The rows a rule looks at, checked: comparisons of a bare column with a constant (=, <>,
    <, <=, >, >=, IN a list of constants, IS [NOT] NULL), joined by AND."""
    try:
        tree = sqlglot.parse_one(text, read="postgres")
    except sqlglot.errors.ParseError as exc:
        raise RuleInvalid("row_condition is not a condition") from exc
    _check(tree)
    return tree


def _check(node: exp.Expr) -> None:
    if isinstance(node, exp.Paren):
        _check(node.this)
        return
    if isinstance(node, exp.And):
        _check(node.left)
        _check(node.right)
        return
    if isinstance(node, exp.Not) and isinstance(node.this, exp.Is):
        _check(node.this)
        return
    if isinstance(node, exp.Is):
        _column(node.this)
        if not isinstance(node.expression, exp.Null):
            raise RuleInvalid("row_condition compares with IS only to NULL")
        return
    if isinstance(node, exp.In):
        _column(node.this)
        if node.args.get("query") or not node.expressions:
            raise RuleInvalid("row_condition's IN takes a list of constants")
        for item in node.expressions:
            _constant(item)
        return
    if isinstance(node, _COMPARISONS):
        left, right = node.this, node.expression
        if isinstance(left, exp.Column):
            _column(left)
            _constant(right)
        else:
            _constant(left)
            _column(right)
        return
    raise RuleInvalid(
        "row_condition takes columns compared with constants joined by AND, "
        f"not {node.key.upper()}"
    )


def _column(node: exp.Expr) -> None:
    if not isinstance(node, exp.Column) or node.table:
        raise RuleInvalid("row_condition compares a column of the table itself")


def _constant(node: exp.Expr) -> None:
    if isinstance(node, exp.Neg):
        node = node.this
    if not isinstance(node, exp.Literal | exp.Boolean | exp.Null):
        raise RuleInvalid("row_condition compares a column with a constant")


def validate(kind: str, column: str | None, params: Mapping[str, Any]) -> None:
    """Raise RuleInvalid when the rule could not be checked as written."""
    expectation = EXPECTATIONS.get(kind)
    if expectation is None:
        raise RuleInvalid(f"unknown kind {kind}")
    # Only the kinds DF-07 added must name their column here; an older one without it is
    # accepted and its check records "rule incomplete", as before.
    if kind == "match_regex" and not column:
        raise RuleInvalid(f"a {kind} rule names a column")
    if not expectation.counts_rows and (
        params.get("mostly") is not None or params.get("row_condition")
    ):
        raise RuleInvalid(f"a {kind} rule takes neither mostly nor row_condition")
    mostly(params)
    if params.get("row_condition"):
        row_condition(str(params["row_condition"]))
    if kind == "compound_unique":
        columns = params.get("columns")
        if (
            not isinstance(columns, list)
            or len(columns) < 2
            or not all(isinstance(c, str) and c for c in columns)
        ):
            raise RuleInvalid("a compound_unique rule names two columns or more in columns")
    if kind == "match_regex":
        pattern = params.get("regex")
        if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_REGEX:
            raise RuleInvalid(f"a match_regex rule has a regex of 1 to {MAX_REGEX} characters")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise RuleInvalid(f"the regex does not compile: {exc}") from exc
    # The seven older kinds keep their behaviour: one written incomplete is accepted and its
    # check records "rule incomplete" (worker_agent/quality.py), as before DF-07.


def result(
    kind: str,
    *,
    violations: int,
    total: int | None,
    threshold: float | None,
    success: bool,
) -> dict[str, Any]:
    """The outcome in GX's result shape (ingest.quality_result.result)."""
    return {
        "expectation": EXPECTATIONS[kind].name,
        "success": success,
        "element_count": total,
        "unexpected_count": violations,
        "unexpected_percent": (
            float(decimal.Decimal(violations) * 100 / decimal.Decimal(total)) if total else None
        ),
        "mostly": threshold,
    }
