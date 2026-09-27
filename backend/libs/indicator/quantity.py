"""Typed arithmetic for published figures: a value, what it measures, and scale.

Fiscal figures are stated in thousands of billions of dong and compared to a
tenth of a percentage point. Carrying them as floats with a free-text unit — as
the metric contract does today — loses the last digits silently and lets a debt
figure be divided by a headcount without anything objecting. Every number that
reaches an answer goes through `Quantity` instead.

Four rules the type enforces, each one a mistake it is designed to make
impossible rather than merely discouraged:

**Decimal, never float.** `Decimal("0.1") * 3 == Decimal("0.3")`; the float
spelling is not. The constructor refuses floats outright rather than converting
them, because by the time a float arrives the digits are already gone.

**The multiplier is an exponent, matching `unit_multiplier Int8` in the
observation store (SERVICE-DESIGN section 3.2).** 3.109,2 nghìn tỷ đồng is
`Decimal("3109.2")` at multiplier 12, not 3109200000000.0 — the figure stays as
published, so it can be shown back the way the source wrote it.

**Percent minus percent is percentage points.** 34,7% - 33,5% is 1,2 điểm %,
not 1,2%, and UI-DESIGN section 3 requires the two to read differently. Getting
this wrong produces a sentence that is grammatical, plausible and false.

**Rounding happens once, at the end.** `round_to` is the only rounding in the
module; intermediate steps keep full precision, so a ratio of two rounded inputs
is never rounded a second time on the way out.

Deliberately NOT here: unit conversion between currencies. An exchange rate has
a period and a source, which makes it an observation, not a constant this module
could hold (TECH-DESIGN T1). Cross-currency arithmetic raises; the formula layer
fetches the rate as a variable and converts explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


class Unit(Enum):
    """What a figure measures.

    A plain Enum, not a `str` mixin: `Unit.PERCENT == "percent"` quietly
    returning True is the kind of loose comparison this module exists to
    prevent. Use `.value` where the observation store's `unit` column needs a
    string.

    Business units, not physical ones — there is no metre or second in this
    domain, and a dimensional-analysis library (pint) would model dimensions
    this domain does not have while missing the one distinction that matters,
    percent versus percentage point (ADR-T2).
    """

    MONEY = "money"
    PERCENT = "percent"
    PERCENTAGE_POINT = "percentage_point"
    RATIO = "ratio"
    PERSONS = "persons"
    INDEX = "index"


class UnitError(ValueError):
    """An operation the unit algebra does not define.

    Raised rather than coerced: a figure whose unit cannot be derived is a bug
    in the formula, and continuing with a guessed unit is how a wrong number
    acquires a plausible label.
    """


@dataclass(frozen=True, eq=False)
class Quantity:
    """A figure, compared by what it measures rather than by how it is written.

    `eq=False` because the generated field-wise equality would call 1 nghìn tỷ
    and 1000 tỷ different numbers — they are the same figure at two scales, and
    a comparison against a threshold must not depend on which scale a source
    happened to publish in.
    """

    value: Decimal
    unit: Unit
    multiplier: int = 0
    currency: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.value, float):
            raise TypeError(
                "Quantity refuses float: Decimal('0.1') and 0.1 are different "
                "numbers, and the digits are already lost by the time the float "
                "arrives. Pass Decimal or str.",
            )
        if not isinstance(self.value, Decimal):
            object.__setattr__(self, "value", Decimal(str(self.value)))
        if self.unit is Unit.MONEY and not self.currency:
            raise UnitError("a money quantity must name its currency")
        if self.unit is not Unit.MONEY and self.currency:
            raise UnitError(f"{self.unit.value} cannot carry a currency")

    # ── comparison ───────────────────────────────────────────────────────────

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Quantity):
            return NotImplemented
        return (
            self.unit is other.unit
            and self.currency == other.currency
            and self.base_value() == other.base_value()
        )

    def __hash__(self) -> int:
        return hash((self.unit, self.currency, self.base_value()))

    def __lt__(self, other: Quantity) -> bool:
        self._require_same_kind(other, "compare")
        return self.base_value() < other.base_value()

    def __le__(self, other: Quantity) -> bool:
        self._require_same_kind(other, "compare")
        return self.base_value() <= other.base_value()

    def __gt__(self, other: Quantity) -> bool:
        self._require_same_kind(other, "compare")
        return self.base_value() > other.base_value()

    def __ge__(self, other: Quantity) -> bool:
        self._require_same_kind(other, "compare")
        return self.base_value() >= other.base_value()

    # ── scale ────────────────────────────────────────────────────────────────

    def rescale(self, multiplier: int) -> Quantity:
        """The same figure written at a different power of ten. Always exact."""
        return replace(self, value=self.value.scaleb(self.multiplier - multiplier),
                       multiplier=multiplier)

    def base_value(self) -> Decimal:
        """The figure with the multiplier applied — for storage and comparison."""
        return self.value.scaleb(self.multiplier)

    # ── addition ─────────────────────────────────────────────────────────────

    def __add__(self, other: Quantity) -> Quantity:
        self._require_same_kind(other, "add")
        left, right = _align(self, other)
        return replace(left, value=left.value + right.value)

    def __sub__(self, other: Quantity) -> Quantity:
        self._require_same_kind(other, "subtract")
        left, right = _align(self, other)
        difference = left.value - right.value
        if left.unit is Unit.PERCENT:
            # The distinction UI-DESIGN section 3 insists on: a fall from 34,7%
            # to 33,5% is 1,2 percentage points. Calling it 1,2% would name a
            # relative change of about 3,5%, a different and much smaller thing.
            return Quantity(difference, Unit.PERCENTAGE_POINT, left.multiplier)
        return replace(left, value=difference)

    def _require_same_kind(self, other: Quantity, verb: str) -> None:
        if not isinstance(other, Quantity):
            raise TypeError(f"cannot {verb} {type(other).__name__} to a Quantity")
        if self.unit is not other.unit:
            raise UnitError(f"cannot {verb} {other.unit.value} and {self.unit.value}")
        if self.currency != other.currency:
            raise UnitError(
                f"cannot {verb} {other.currency} and {self.currency} without an "
                "exchange rate; a rate has a period and a source, so the formula "
                "layer must supply it as a variable",
            )

    # ── scaling and ratios ───────────────────────────────────────────────────

    def scaled_by(self, factor: Decimal | int) -> Quantity:
        """Multiply by a plain number. The unit is unchanged by construction."""
        if isinstance(factor, float):
            raise TypeError("scale by Decimal or int, not float")
        return replace(self, value=self.value * Decimal(factor))

    def ratio_to(self, other: Quantity) -> Quantity:
        """`self / other` as a dimensionless ratio.

        Both sides must measure the same thing: debt over GDP is a ratio, debt
        over headcount is a figure this module has no name for and will not
        invent one for.
        """
        self._require_same_kind(other, "divide")
        if not other.value:
            raise ZeroDivisionError("denominator is zero")
        left, right = _align(self, other)
        return Quantity(left.value / right.value, Unit.RATIO)

    def as_percent(self) -> Quantity:
        """A ratio expressed out of a hundred. 0,347 → 34,7%."""
        if self.unit is not Unit.RATIO:
            raise UnitError(f"only a ratio converts to percent, not {self.unit.value}")
        return Quantity(self.rescale(0).value * 100, Unit.PERCENT)

    # ── presentation ─────────────────────────────────────────────────────────

    def round_to(self, places: int) -> Quantity:
        """Round to the indicator's published precision. The last step, once.

        ROUND_HALF_UP, not Decimal's default ROUND_HALF_EVEN: published figures
        are rounded the way people round, and banker's rounding disagrees with
        the source exactly on the .5 boundary.
        """
        quantum = Decimal(1).scaleb(-places)
        return replace(self, value=self.value.quantize(quantum, rounding=ROUND_HALF_UP))


def _align(left: Quantity, right: Quantity) -> tuple[Quantity, Quantity]:
    """Both figures at the finer of the two scales, so nothing is shifted away."""
    target = min(left.multiplier, right.multiplier)
    return left.rescale(target), right.rescale(target)
