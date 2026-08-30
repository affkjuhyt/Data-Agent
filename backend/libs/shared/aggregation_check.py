"""Is this aggregate multiplied by a join?

The failure this exists for produces no error and no warning. Given

    SELECT SUM(o.amount) FROM orders o JOIN order_items i ON i.order_id = o.id

every order row is repeated once per item before the SUM runs, so the total is
multiplied by the average item count. It is not off by a rounding error, it is
off by a factor — and the number that comes back is the right order of
magnitude, formatted the same way a correct one would be, with nothing in the
answer to distinguish it. A reader has no way to tell.

Nothing in the platform looked for it: no fan-out check, no double-count check,
no unit check, no subtotal-vs-total reconciliation.

**What this can and cannot prove.** Whether a join actually fans out depends on
the cardinality of the join key, and nothing available here knows it — the
Schema Catalog stores column names and types, not primary or unique keys. So
this reports *risk*, never *wrongness*, and says so in those words. Reporting an
unverifiable figure as wrong would be its own kind of lie, and one that trains
readers to dismiss the warning.

What it does do is exact about the SHAPE: an aggregate over a column belonging
to one table, in a statement that joins that table to another, with nothing that
collapses the duplicates. That shape is wrong unless the join happens to be
one-to-one, which makes it worth stating every time.

Two shapes are provably safe and are never flagged:

  * `COUNT(DISTINCT ...)` — deduplication is the whole point of it;
  * an aggregate in a statement with no join at all, which is what the Semantic
    Layer compiles (single source_table, no joins) — so a compiled metric is
    structurally immune to this and the answer can say so.

Pure text analysis, no parser: workflow_runtime carries neither sqlparse nor
sqlglot, and `libs` stays a leaf. The cost is that this reads SQL rather than
understanding it, which is why every rule below is written to fail toward
"nothing to report" when the text is beyond it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Aggregates whose value changes when rows are duplicated. MIN and MAX are
# deliberately absent: duplicating a row cannot change either, so a fan-out is
# harmless for them and flagging it would be noise.
_MULTIPLIED_BY_DUPLICATES = ("sum", "avg", "count", "total", "stddev", "variance")

_AGG_RE = re.compile(
    r"\b(" + "|".join(_MULTIPLIED_BY_DUPLICATES) + r")\s*\(\s*(distinct\s+)?([^)]*)\)",
    re.I,
)

# `FROM x AS a`, `JOIN y b`. The alias is optional; when absent the table name
# is its own alias, which is how a reader refers to its columns.
_FROM_RE = re.compile(
    r"\bfrom\s+([A-Za-z_][\w.]*)(?:\s+as)?(?:\s+([A-Za-z_]\w*))?", re.I,
)
_JOIN_RE = re.compile(
    r"\b((?:inner|left|right|full|cross)\s+)?join\s+([A-Za-z_][\w.]*)(?:\s+as)?(?:\s+([A-Za-z_]\w*))?",
    re.I,
)

# Reserved words that can follow a table name and are not an alias.
_NOT_AN_ALIAS = frozenset({
    "on", "using", "where", "group", "order", "limit", "having", "join",
    "inner", "left", "right", "full", "cross", "union", "and", "or", "as",
})

FAN_OUT = "fan_out_risk"
UNIT_MISMATCH = "unit_mismatch"
SUBTOTAL_MISMATCH = "subtotal_mismatch"


@dataclass(frozen=True)
class Finding:
    kind: str
    detail: str
    # The specific expression that triggered it, so a reader can check the claim
    # rather than take it on faith.
    expression: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail, "expression": self.expression}


@dataclass(frozen=True)
class AggregationReport:
    findings: tuple[Finding, ...] = ()
    # True when the statement was checked and nothing about its shape can
    # multiply an aggregate — a positive statement, not merely "no findings".
    structurally_safe: bool = False
    aggregates: tuple[str, ...] = ()
    joins: int = 0

    @property
    def has_risk(self) -> bool:
        return bool(self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "structurally_safe": self.structurally_safe,
            "aggregates": list(self.aggregates),
            "joins": self.joins,
        }


def _strip_strings(sql: str) -> str:
    """Blank out string literals so a table name inside one cannot be read as
    SQL. Length is preserved so nothing else shifts."""
    out = []
    in_string = False
    for ch in sql:
        if ch == "'":
            in_string = not in_string
            out.append(" ")
        else:
            out.append(" " if in_string else ch)
    return "".join(out)


def _alias(name: str, candidate: str | None) -> str:
    if candidate and candidate.lower() not in _NOT_AN_ALIAS:
        return candidate
    # No alias: columns are qualified by the table's own (last) name segment.
    return name.rsplit(".", 1)[-1]


def _qualifier(expression: str) -> str:
    """The `o` in `o.amount`, or "" for a bare column or an expression."""
    expression = expression.strip()
    if expression in ("*", "1") or not expression:
        return ""
    match = re.match(r"^([A-Za-z_]\w*)\s*\.", expression)
    return match.group(1) if match else ""


def analyse(sql: str) -> AggregationReport:
    """Look for shapes that multiply an aggregate. Never raises."""
    if not sql or not sql.strip():
        return AggregationReport()

    try:
        text = _strip_strings(sql)

        aggregates = [
            (m.group(1).upper(), bool(m.group(2)), m.group(3).strip())
            for m in _AGG_RE.finditer(text)
        ]
        if not aggregates:
            # Nothing to multiply. Not "safe" — there is no aggregate to be
            # wrong about, which is a different statement.
            return AggregationReport(joins=len(_JOIN_RE.findall(text)))

        from_match = _FROM_RE.search(text)
        joins = [
            (_alias(m.group(2), m.group(3)), (m.group(1) or "").strip().lower())
            for m in _JOIN_RE.finditer(text)
        ]
        agg_names = tuple(f"{name}({expr})" for name, _, expr in aggregates)

        if not joins:
            # One table, so no duplication is possible from a join. This is what
            # the Semantic Layer compiles, and the reason a compiled metric
            # cannot suffer from this at all.
            return AggregationReport(
                structurally_safe=True, aggregates=agg_names, joins=0,
            )

        base_alias = _alias(from_match.group(1), from_match.group(2)) if from_match else ""
        joined_aliases = [alias for alias, _ in joins]
        findings: list[Finding] = []

        for name, is_distinct, expression in aggregates:
            if is_distinct:
                # Deduplication is exactly the fix; nothing to report.
                continue
            qualifier = _qualifier(expression)

            # An aggregate over a column from the FROM-side table, duplicated
            # once per matching row on the join side. `COUNT(*)` is included:
            # after a fan-out it counts join pairs, not the thing a reader
            # thinks it counts.
            #
            # A column from a JOINED table is exempt only when there is exactly
            # one join, because then it is the many-side and appears once. With
            # two or more, any OTHER join can still duplicate it —
            # `SUM(i.qty) FROM orders o JOIN items i JOIN shipments s` is
            # multiplied by shipments-per-order, and exempting it on the grounds
            # that `i` is joined would miss that entirely.
            from_joined_side = qualifier in joined_aliases and len(joins) == 1
            multiplied = not from_joined_side and (
                expression == "*"
                or not qualifier
                or qualifier == base_alias
                or qualifier not in joined_aliases
                or len(joins) > 1
            )
            if not multiplied:
                continue

            subject = expression if expression != "*" else "every row"
            findings.append(Finding(
                kind=FAN_OUT,
                detail=(
                    f"{name} over {subject} in a statement that joins "
                    f"{len(joins)} table(s). If one row of "
                    f"{base_alias or 'the base table'} matches several rows on the "
                    f"join, this figure is multiplied by that count. Verify the "
                    f"join is one-to-one, aggregate before joining, or use "
                    f"{name}(DISTINCT ...)."
                ),
                expression=f"{name}({expression})",
            ))

        return AggregationReport(
            findings=tuple(findings),
            structurally_safe=not findings,
            aggregates=agg_names,
            joins=len(joins),
        )
    except Exception:
        # Reading SQL with regular expressions has a floor. Below it, report
        # nothing rather than something wrong — a bad finding costs more trust
        # than a missed one.
        return AggregationReport()


def check_units(units: list[str]) -> Finding | None:
    """Two different declared units in one figure.

    Exact, unlike the fan-out check: the Semantic Layer records a unit per
    metric contract, so combining VND with USD is not a risk to weigh, it is an
    error to state. Blank units are ignored — undeclared is not a conflict.
    """
    declared = {u.strip() for u in units if u and u.strip()}
    if len(declared) <= 1:
        return None
    return Finding(
        kind=UNIT_MISMATCH,
        detail=(
            "figures with different units are being combined: "
            + ", ".join(sorted(declared))
            + ". The result has no single unit and cannot be summed or compared."
        ),
    )


def check_subtotals(
    parts: list[float], total: float, *, tolerance: float = 0.01,
) -> Finding | None:
    """Do the grouped rows add up to the stated total?

    Also exact — it is arithmetic on values already returned, needing no schema
    knowledge. A mismatch is the visible symptom of several different bugs (a
    fan-out on one branch, a filter applied to one side only, a NULL group
    dropped), which is what makes it worth checking even though it cannot say
    which.

    Relative tolerance, because float sums of many rows do not land exactly.
    """
    if not parts:
        return None
    summed = sum(parts)
    scale = max(abs(total), abs(summed), 1.0)
    if abs(summed - total) / scale <= tolerance:
        return None
    return Finding(
        kind=SUBTOTAL_MISMATCH,
        detail=(
            f"the parts sum to {summed:,.2f} but the total is stated as "
            f"{total:,.2f} — a gap of {summed - total:,.2f}. One of the two is "
            "computed over a different row set."
        ),
    )


def describe(report: AggregationReport | dict[str, Any] | None) -> str:
    """One line for the answering model, or "" when there is nothing to say.

    Takes either form: the report is produced where the query runs and read
    again in the responder, crossing agent state as plain JSON in between.
    """
    if report is None:
        return ""
    data = report.to_dict() if isinstance(report, AggregationReport) else report
    if not isinstance(data, dict):
        return ""
    findings = data.get("findings") or []
    if not findings:
        return ""
    return "aggregation-risk: " + " | ".join(
        f"{f.get('kind', '?')}: {f.get('detail', '')}" for f in findings
    )


__all__ = [
    "FAN_OUT",
    "SUBTOTAL_MISMATCH",
    "UNIT_MISMATCH",
    "AggregationReport",
    "Finding",
    "analyse",
    "check_subtotals",
    "check_units",
    "describe",
]
