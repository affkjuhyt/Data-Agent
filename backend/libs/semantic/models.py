"""Metric contracts — the vocabulary the Semantic Layer is written in.

Business meaning lived only in prompts before this: an agent asked for "doanh
thu" got whatever SQL the model invented that turn, so gross and net revenue
were the same question, `COUNT(*)` and `COUNT(DISTINCT customer_id)` were
interchangeable, and "tháng 6" silently meant `created_at` when the business
means the transaction date. Two people asking the same thing in different words
got different numbers with no way to tell which was right.

A MetricDefinition fixes all of that in one versioned record: one aggregation,
over one expression, on one table, with its own default filters, its own
business time column, and a declared unit. Ambiguity is resolved once, by a
named owner, instead of per turn by a language model.

Pure data + validation, no I/O and no database driver — `libs/` stays a leaf
layer (enforced by .importlinter), so these types are equally usable from a
service, a test, or a migration script.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class SemanticError(Exception):
    """Base for every failure in this package. Callers treat any instance as
    'this question cannot be answered from the registry' and fall back."""


class MetricNotFound(SemanticError):
    pass


class InvalidMetricDefinition(SemanticError):
    """A stored definition is unusable. Raised at load time, not compile time,
    so a malformed contract is caught before it can emit SQL."""


class UnsupportedRequest(SemanticError):
    """The definition is valid but cannot serve this particular request — an
    unknown dimension, an unknown filter column, an unparseable time range."""


class Aggregation(str, Enum):
    SUM = "sum"
    # COUNT and COUNT_DISTINCT are deliberately separate members rather than a
    # flag: "số đơn hàng" and "số khách hàng" differ ONLY by this choice, and
    # collapsing them is one of the exact confusions this layer exists to end.
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


_AGG_SQL = {
    Aggregation.SUM: "SUM({expr})",
    Aggregation.COUNT: "COUNT({expr})",
    Aggregation.COUNT_DISTINCT: "COUNT(DISTINCT {expr})",
    Aggregation.AVG: "AVG({expr})",
    Aggregation.MIN: "MIN({expr})",
    Aggregation.MAX: "MAX({expr})",
}


def aggregation_template(agg: Aggregation) -> str:
    return _AGG_SQL[agg]


class TimeGrain(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    # No grain: one number for the whole period, no time bucketing.
    NONE = "none"


class MetricStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    DEPRECATED = "deprecated"


class FilterOp(str, Enum):
    EQ = "eq"
    NEQ = "neq"
    IN = "in"
    NOT_IN = "not_in"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


# Identifiers are interpolated into SQL, so they are validated against this and
# nothing else reaches the string. Optionally schema-qualified; no quoting, no
# expressions, no whitespace. The compiled SQL still passes sql_guard and runs
# as a read-only role, but that is defence in depth, not the primary control —
# a metric definition is operator-authored config and must be constrained here.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def validate_identifier(value: str, what: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.match(value):
        raise InvalidMetricDefinition(f"{what} is not a valid SQL identifier: {value!r}")
    return value


@dataclass(frozen=True)
class MetricFilter:
    """A predicate baked into the metric, or supplied by the caller.

    Default filters are how gross and net revenue stop being the same query:
    `revenue_net` carries `status NOT IN ('refunded','cancelled')` in its own
    contract, so every caller gets it whether or not they thought to ask.
    """

    column: str
    op: FilterOp
    value: object = None

    def __post_init__(self) -> None:
        validate_identifier(self.column, "filter column")
        if self.op in (FilterOp.IS_NULL, FilterOp.IS_NOT_NULL):
            return
        if self.op in (FilterOp.IN, FilterOp.NOT_IN):
            if not isinstance(self.value, (list, tuple)) or not self.value:
                raise InvalidMetricDefinition(f"{self.op.value} filter on {self.column} needs a non-empty list")
        elif self.value is None:
            raise InvalidMetricDefinition(f"{self.op.value} filter on {self.column} needs a value")

    @staticmethod
    def from_dict(raw: dict[str, object]) -> MetricFilter:
        try:
            op = FilterOp(str(raw.get("op", "eq")))
        except ValueError as e:
            raise InvalidMetricDefinition(f"unknown filter op: {raw.get('op')!r}") from e
        return MetricFilter(column=str(raw.get("column", "")), op=op, value=raw.get("value"))


@dataclass(frozen=True)
class MetricDimension:
    """A column the metric may be broken down by, under a business-facing key.

    Keyed separately from the column so "miền" can group by `region_name`
    without the caller knowing the physical name — and so renaming the column
    is a version bump, not a break.
    """

    key: str
    column: str
    label: str = ""

    def __post_init__(self) -> None:
        validate_identifier(self.column, f"dimension {self.key!r} column")

    @staticmethod
    def from_dict(raw: dict[str, object]) -> MetricDimension:
        return MetricDimension(
            key=str(raw.get("key", "")).strip().lower(),
            column=str(raw.get("column", "")),
            label=str(raw.get("label", "")),
        )


@dataclass(frozen=True)
class MetricDefinition:
    """One approved way to compute one number.

    `time_column` is the business event date, deliberately named as its own
    field rather than assumed: defaulting to a row's `created_at` is the classic
    silent error, where a June order booked in July lands in the wrong month and
    the total is quietly wrong rather than visibly missing.
    """

    key: str
    version: int
    name: str
    aggregation: Aggregation
    source_table: str
    # The expression aggregated. "*" is legal only for COUNT.
    expression: str
    time_column: str = ""
    default_grain: TimeGrain = TimeGrain.NONE
    unit: str = ""
    dimensions: tuple[MetricDimension, ...] = ()
    default_filters: tuple[MetricFilter, ...] = ()
    status: MetricStatus = MetricStatus.APPROVED
    owner: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        validate_identifier(self.source_table, "source_table")
        if self.expression == "*":
            if self.aggregation is not Aggregation.COUNT:
                raise InvalidMetricDefinition(
                    f"metric {self.key!r}: expression '*' is only valid for COUNT, not {self.aggregation.value}"
                )
        else:
            validate_identifier(self.expression, f"metric {self.key!r} expression")
        if self.time_column:
            validate_identifier(self.time_column, f"metric {self.key!r} time_column")
        if self.default_grain is not TimeGrain.NONE and not self.time_column:
            raise InvalidMetricDefinition(
                f"metric {self.key!r} declares grain {self.default_grain.value} but no time_column"
            )
        seen: set[str] = set()
        for d in self.dimensions:
            if d.key in seen:
                raise InvalidMetricDefinition(f"metric {self.key!r} has duplicate dimension key {d.key!r}")
            seen.add(d.key)

    @property
    def ref(self) -> str:
        """Stable, quotable identity for provenance: `revenue_net@3`."""
        return f"{self.key}@{self.version}"

    def dimension(self, key: str) -> MetricDimension:
        wanted = (key or "").strip().lower()
        for d in self.dimensions:
            if d.key == wanted:
                return d
        raise UnsupportedRequest(
            f"metric {self.ref} has no dimension {key!r} "
            f"(available: {', '.join(d.key for d in self.dimensions) or 'none'})"
        )


@dataclass
class MetricCatalog:
    """Every metric a tenant may ask for, plus the phrasings that reach them."""

    definitions: dict[str, MetricDefinition] = field(default_factory=dict)
    # normalized synonym -> metric key
    synonyms: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> MetricDefinition:
        try:
            return self.definitions[key]
        except KeyError as e:
            raise MetricNotFound(f"no metric named {key!r}") from e
