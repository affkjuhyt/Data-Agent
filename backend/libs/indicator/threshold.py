"""Which ceiling applied, and whether a figure cleared it.

The selection rule is one line long and is the whole point: a threshold is
chosen by the REFERENCE PERIOD of the data, never by the date of the question.
NQ 23/2021/QH15 governs 2021-2025 and NQ 26/2026/QH16 governs 2026-2030, so a
2025 figure is judged against the first one forever — including when it is
asked about in 2027. Reading "exceeded the ceiling" off the wrong resolution is
the failure NC-R3 (docs/v2/12-numeric-core.md) ranks third, above rounding
errors, because it invalidates the conclusion rather than the last digit.

Everything here is deterministic and unit-checked. The LLM is told the status;
it does not derive it.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from libs.indicator.period import Period
from libs.indicator.quantity import Quantity


class Level(Enum):
    """Severity of a threshold — NOT its direction, which `operator` carries."""

    WARNING = "warning"
    LIMIT = "limit"


class Status(Enum):
    OK = "ok"
    WARNING = "warning"
    BREACH = "breach"
    NO_THRESHOLD = "no_threshold"
    """No rule covered this period — reported, never silently treated as OK."""


_COMPARISONS: dict[str, Callable[[Quantity, Quantity], bool]] = {
    "<=": lambda value, limit: value <= limit,
    "<": lambda value, limit: value < limit,
    ">=": lambda value, limit: value >= limit,
    ">": lambda value, limit: value > limit,
}


@dataclass(frozen=True)
class Threshold:
    level: Level
    operator: str
    value: Quantity
    valid_from_year: int
    valid_to_year: int | None = None
    legal_basis_label: str | None = None
    legal_basis_doc_id: str | None = None

    def __post_init__(self) -> None:
        if self.operator not in _COMPARISONS:
            raise ValueError(f"unknown operator {self.operator!r}")
        if self.valid_to_year is not None and self.valid_to_year < self.valid_from_year:
            raise ValueError("a threshold cannot stop applying before it starts")

    def covers(self, period: Period) -> bool:
        """Whether this rule governs data ABOUT `period`.

        Inclusive of both years: a resolution written "2021-2025" governs the
        whole of 2025. An open end means no successor has been issued yet.
        """
        if period.year < self.valid_from_year:
            return False
        return self.valid_to_year is None or period.year <= self.valid_to_year

    def is_satisfied_by(self, value: Quantity) -> bool:
        """Whether `value` complies. Raises if the units are not comparable."""
        return _COMPARISONS[self.operator](value, self.value)


@dataclass(frozen=True)
class Assessment:
    status: Status
    breached: tuple[Threshold, ...] = ()
    applicable: tuple[Threshold, ...] = ()

    @property
    def legal_basis(self) -> str | None:
        """The document to cite — of the most severe rule that was broken, or,
        when nothing was broken, of the binding limit the figure cleared."""
        for threshold in (*self.breached, *self.applicable):
            if threshold.legal_basis_label:
                return threshold.legal_basis_label
        return None


def applicable_thresholds(
    thresholds: Sequence[Threshold], period: Period,
) -> tuple[Threshold, ...]:
    """The rules governing `period`, most severe first."""
    covering = [t for t in thresholds if t.covers(period)]
    covering.sort(key=lambda t: 0 if t.level is Level.LIMIT else 1)
    return tuple(covering)


def assess(
    value: Quantity, thresholds: Sequence[Threshold], period: Period,
) -> Assessment:
    """Judge `value` for `period` against the rules in force for that period.

    `period` is the reference period of the DATA. Passing today's date here
    instead is the whole bug this module exists to prevent, which is why there
    is no parameter that would accept one.
    """
    covering = applicable_thresholds(thresholds, period)
    if not covering:
        # Distinct from OK on purpose: "no ceiling was defined for 2019" and
        # "2019 was within the ceiling" are different statements, and only one
        # of them is reassuring.
        return Assessment(Status.NO_THRESHOLD)

    breached = tuple(t for t in covering if not t.is_satisfied_by(value))
    if any(t.level is Level.LIMIT for t in breached):
        status = Status.BREACH
    elif breached:
        status = Status.WARNING
    else:
        status = Status.OK
    return Assessment(status, breached, covering)
