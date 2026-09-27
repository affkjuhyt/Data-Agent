"""Deterministic evaluation of an indicator's formula. The LLM never does this.

An indicator is a formula over atomic variables, not an aggregate over a
warehouse column (docs/v2/12-numeric-core.md section 0). This module holds the
formula as a typed DAG, checks it BEFORE any data is fetched, and then evaluates
it with every edge carrying a `Quantity`, so unit and scale errors surface as
exceptions rather than as plausible numbers.

The split between `compile_formula` and `evaluate` is the point. Compiling
needs only the variable declarations from the catalog, so a formula that could
never produce a meaningful figure — mismatched units, a stock silently divided
by a flow, a cycle — is rejected when the indicator version is authored, not
when a user asks a question.

**The error this module exists to prevent** (NC-R2 in docs/v2/12-numeric-core.md):
end-of-period debt divided by a period's GDP is the standard debt-to-GDP ratio
when the period is a year, and nonsense when it is a quarter — off by a factor
of about four, in the direction that makes the number look reassuring. Nothing about the arithmetic
objects. So a ratio mixing a stock and a flow must name its alignment rule, and
that rule is checked against the period being evaluated.

Deliberately NOT here: revaluation effects in `decompose_change`. Separating a
GDP re-basing from real growth needs the vintage of each figure, which lives in
the observation store (NC-T2); inventing a third contribution without it would put
a number on something this module cannot see.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from libs.indicator.period import Frequency, Period
from libs.indicator.quantity import Quantity, Unit, UnitError


class StockOrFlow(Enum):
    """Whether a figure is a level at an instant or an accumulation over time."""

    STOCK = "stock"
    FLOW = "flow"
    RATIO = "ratio"


class Alignment(Enum):
    """How a stock and a flow may be put over one another.

    `formula_input.alignment_rule` in the catalog (diagram 05). Only the rule
    the golden use case needs is defined; an unlisted alignment is a modelling
    decision that belongs in the catalog spec before it belongs in code.
    """

    SAME_CALENDAR_YEAR = "same_calendar_year"


class FormulaError(ValueError):
    """A formula that cannot produce a meaningful figure."""


@dataclass(frozen=True)
class VariableSpec:
    """What the catalog declares about an atomic variable (diagram 05)."""

    variable_id: str
    unit: Unit
    stock_or_flow: StockOrFlow
    multiplier: int = 0
    currency: str | None = None


# ── nodes ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Node:
    """Base class. Subclasses are the leaf and the operations."""


@dataclass(frozen=True)
class Var(Node):
    variable_id: str


@dataclass(frozen=True)
class Ratio(Node):
    numerator: Node
    denominator: Node
    alignment: Alignment | None = None


@dataclass(frozen=True)
class Sum(Node):
    components: tuple[Node, ...]


@dataclass(frozen=True)
class Difference(Node):
    left: Node
    right: Node


@dataclass(frozen=True)
class AsPercent(Node):
    ratio: Node


@dataclass(frozen=True)
class Scale(Node):
    node: Node
    factor: Decimal


def _children(node: Node) -> tuple[Node, ...]:
    if isinstance(node, Var):
        return ()
    if isinstance(node, Ratio):
        return (node.numerator, node.denominator)
    if isinstance(node, Sum):
        return node.components
    if isinstance(node, Difference):
        return (node.left, node.right)
    if isinstance(node, AsPercent):
        return (node.ratio,)
    if isinstance(node, Scale):
        return (node.node,)
    raise FormulaError(f"unknown node type {type(node).__name__}")


def walk(node: Node) -> Iterator[Node]:
    """Every node, children before parents — the topological order.

    Nodes are frozen and acyclic by construction (a child is built before its
    parent), so the traversal cannot loop. `variables` below is what catches the
    other way a cycle arrives: a variable whose own definition refers back.
    """
    for child in _children(node):
        yield from walk(child)
    yield node


def variables(node: Node) -> frozenset[str]:
    return frozenset(n.variable_id for n in walk(node) if isinstance(n, Var))


# ── compile ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ResultType:
    unit: Unit
    stock_or_flow: StockOrFlow
    multiplier: int = 0
    currency: str | None = None
    requires_frequency: Frequency | None = None
    """Set when an alignment rule pins the formula to one frequency."""


def compile_formula(node: Node, specs: dict[str, VariableSpec]) -> ResultType:
    """Type-check the formula against the catalog. Raises, or returns its type.

    Runs without touching data, so an unusable formula is caught when an
    indicator version is authored rather than when someone asks a question.
    """
    missing = variables(node) - specs.keys()
    if missing:
        raise FormulaError(f"formula refers to undeclared variables: {sorted(missing)}")
    return _type_of(node, specs)


def _type_of(node: Node, specs: dict[str, VariableSpec]) -> ResultType:
    if isinstance(node, Var):
        spec = specs[node.variable_id]
        return ResultType(spec.unit, spec.stock_or_flow, spec.multiplier, spec.currency)

    if isinstance(node, Ratio):
        num, den = _type_of(node.numerator, specs), _type_of(node.denominator, specs)
        if num.unit is not den.unit or num.currency != den.currency:
            raise FormulaError(
                f"cannot form a ratio of {num.unit.value} over {den.unit.value}"
                f"{'' if num.currency == den.currency else f' ({num.currency} over {den.currency})'}",
            )
        return ResultType(Unit.RATIO, StockOrFlow.RATIO,
                          requires_frequency=_alignment_frequency(node, num, den))

    if isinstance(node, Sum):
        if not node.components:
            raise FormulaError("a sum needs at least one component")
        types = [_type_of(c, specs) for c in node.components]
        return _same_type(types, "sum")

    if isinstance(node, Difference):
        left, right = _type_of(node.left, specs), _type_of(node.right, specs)
        result = _same_type([left, right], "difference")
        if result.unit is Unit.PERCENT:
            # Mirrors Quantity.__sub__: a change in a percent is measured in
            # percentage points, and the type has to say so before evaluation
            # so a caller cannot declare the indicator's unit as percent.
            return ResultType(Unit.PERCENTAGE_POINT, StockOrFlow.FLOW,
                              requires_frequency=result.requires_frequency)
        return result

    if isinstance(node, AsPercent):
        inner = _type_of(node.ratio, specs)
        if inner.unit is not Unit.RATIO:
            raise FormulaError(f"only a ratio converts to percent, not {inner.unit.value}")
        return ResultType(Unit.PERCENT, StockOrFlow.RATIO,
                          requires_frequency=inner.requires_frequency)

    if isinstance(node, Scale):
        return _type_of(node.node, specs)

    raise FormulaError(f"unknown node type {type(node).__name__}")


def _alignment_frequency(
    node: Ratio, num: ResultType, den: ResultType,
) -> Frequency | None:
    mixes_stock_and_flow = {num.stock_or_flow, den.stock_or_flow} == {
        StockOrFlow.STOCK, StockOrFlow.FLOW,
    }
    if not mixes_stock_and_flow:
        if node.alignment is not None:
            raise FormulaError(
                "alignment is only meaningful where a stock meets a flow; "
                f"got {num.stock_or_flow.value} over {den.stock_or_flow.value}",
            )
        return _merge_frequency(num.requires_frequency, den.requires_frequency)
    if node.alignment is None:
        raise FormulaError(
            f"a ratio of {num.stock_or_flow.value} over {den.stock_or_flow.value} must "
            "name its alignment rule: an end-of-period level over a period's "
            "accumulation means one thing for a year and another for a quarter",
        )
    if node.alignment is Alignment.SAME_CALENDAR_YEAR:
        return Frequency.ANNUAL
    raise FormulaError(f"unsupported alignment {node.alignment}")


def _merge_frequency(left: Frequency | None, right: Frequency | None) -> Frequency | None:
    if left is not None and right is not None and left is not right:
        raise FormulaError(
            f"formula is pinned to both {left.value} and {right.value} frequency",
        )
    return left or right


def _same_type(types: list[ResultType], op: str) -> ResultType:
    first = types[0]
    for other in types[1:]:
        if other.unit is not first.unit or other.currency != first.currency:
            raise FormulaError(
                f"cannot {op} {first.unit.value} and {other.unit.value}",
            )
    frequency: Frequency | None = None
    for t in types:
        frequency = _merge_frequency(frequency, t.requires_frequency)
    return ResultType(first.unit, first.stock_or_flow, min(t.multiplier for t in types),
                      first.currency, frequency)


# ── evaluate ──────────────────────────────────────────────────────────────────

Resolver = Callable[[str, Period], Quantity]


def evaluate(
    node: Node,
    specs: dict[str, VariableSpec],
    resolve: Resolver,
    period: Period,
) -> Quantity:
    """Compute the formula for one period.

    `resolve` fetches an atomic variable's observed value — the observation
    store in production, a mapping in tests. It is a parameter rather than an
    import so this module has no opinion on where figures come from and can be
    tested without one.
    """
    result_type = compile_formula(node, specs)
    if (
        result_type.requires_frequency is not None
        and period.frequency is not result_type.requires_frequency
    ):
        raise FormulaError(
            f"formula aligns on {result_type.requires_frequency.value} periods but was "
            f"asked for {period} ({period.frequency.value})",
        )
    return _eval(node, resolve, period)


def _eval(node: Node, resolve: Resolver, period: Period) -> Quantity:
    if isinstance(node, Var):
        return resolve(node.variable_id, period)
    if isinstance(node, Ratio):
        return _eval(node.numerator, resolve, period).ratio_to(
            _eval(node.denominator, resolve, period))
    if isinstance(node, Sum):
        total = _eval(node.components[0], resolve, period)
        for component in node.components[1:]:
            total = total + _eval(component, resolve, period)
        return total
    if isinstance(node, Difference):
        return _eval(node.left, resolve, period) - _eval(node.right, resolve, period)
    if isinstance(node, AsPercent):
        return _eval(node.ratio, resolve, period).as_percent()
    if isinstance(node, Scale):
        return _eval(node.node, resolve, period).scaled_by(node.factor)
    raise FormulaError(f"unknown node type {type(node).__name__}")


# ── change decomposition ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Decomposition:
    """Why a ratio moved, split between its two sides.

    The parts sum to the total exactly, by construction:
        (N1-N0)/D0 + N1*(1/D1 - 1/D0) == N1/D1 - N0/D0
    `check()` asserts it anyway — a decomposition whose parts do not add up is
    worse than none, because it reads as an explanation.
    """

    total: Quantity
    numerator_effect: Quantity
    denominator_effect: Quantity

    def check(self, tolerance: Decimal = Decimal("0.0000001")) -> None:
        parts = (self.numerator_effect + self.denominator_effect).base_value()
        if abs(parts - self.total.base_value()) > tolerance:
            raise FormulaError(
                f"decomposition does not add up: {parts} != {self.total.base_value()}",
            )


def decompose_change(
    ratio: Ratio,
    specs: dict[str, VariableSpec],
    resolve: Resolver,
    period: Period,
    base: Period | None = None,
    as_percent: bool = True,
) -> Decomposition:
    """Split a ratio's change between periods into numerator and denominator.

    Answers "why did the ratio fall" — how much came from the numerator moving
    and how much from the denominator — which is the question the golden use
    case asks about 2017-2020, where the ratio fell mostly because GDP grew.
    """
    compile_formula(ratio, specs)
    base = base or period.previous()

    n1, d1 = _eval(ratio.numerator, resolve, period), _eval(ratio.denominator, resolve, period)
    n0, d0 = _eval(ratio.numerator, resolve, base), _eval(ratio.denominator, resolve, base)
    if not d0.value or not d1.value:
        raise ZeroDivisionError("denominator is zero in one of the periods")

    def level(q: Quantity) -> Quantity:
        return q.as_percent() if as_percent else q

    def contribution(q: Quantity) -> Quantity:
        # A share of a CHANGE is measured in percentage points, not percent:
        # it has to be subtractable from the total change, and `34,7%` minus
        # `2,3%` would name a relative change of the ratio rather than a part
        # of its movement.
        if not as_percent:
            return q
        return Quantity(q.as_percent().value, Unit.PERCENTAGE_POINT)

    try:
        total = level(n1.ratio_to(d1)) - level(n0.ratio_to(d0))
        numerator_effect = contribution((n1 - n0).ratio_to(d0))
        denominator_effect = total - numerator_effect
    except UnitError as exc:  # pragma: no cover - guarded by compile_formula
        raise FormulaError(str(exc)) from exc

    decomposition = Decomposition(total, numerator_effect, denominator_effect)
    decomposition.check()
    return decomposition
