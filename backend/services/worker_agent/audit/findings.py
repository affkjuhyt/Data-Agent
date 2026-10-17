"""A dataset's audit read from its aggregates: no database here, so every rule is tested alone.

What the data steward needs before trusting a table (UC-04-02 profile, UC-06-07 quality):
- the profile of each column: share empty, distinct values, range, most common values; a column
  labelled personal data keeps its counts but never its values (AC-04-02-02);
- what is missing and why: a column empty for a whole group (gender for companies) does not apply
  there, so only the gap left where it does apply is a real one;
- how values spread, and where one group concentrates against the whole (men in one age band,
  women in another): a share at least LIFT_MIN times the overall one;
- whether the key is unique.

Each spread is also a part with its chart (the run screen's "Kết quả", like a report's question).
"""

from __future__ import annotations

import decimal
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from services.worker_agent.answer.narrate import vn_number

# A column with at most this many values is a category: spread, crossed, explained by.
CATEGORICAL_MAX = 12
# A column crossed against the others has few values (gender, a segment).
CROSS_MAX = 5
# A group concentrates when its share is this many times the overall share.
LIFT_MIN = decimal.Decimal("1.2")
MIN_SHARE = decimal.Decimal("0.10")
# A real gap above this share of the rows it applies to needs attention ("high").
HIGH_GAP = decimal.Decimal("0.20")
# The crossings told, strongest first: a table of many categories crosses many ways.
MAX_CROSSES = 6

Severity = Literal["high", "warning", "info"]
EMPTY = "(trống)"


@dataclass(frozen=True)
class Column:
    name: str
    label: str
    kind: str  # catalog.column_meta.normalized_type
    is_key: bool = False
    pii: bool = False

    @property
    def numeric(self) -> bool:
        return self.kind in ("integer", "decimal")


@dataclass
class Aggregates:
    """What the audit's queries returned (audit/run.py)."""

    rows: int
    non_null: dict[str, int]
    distinct: dict[str, int]
    minimum: dict[str, Any] = field(default_factory=dict)
    maximum: dict[str, Any] = field(default_factory=dict)
    # column → (value, rows), None for the empty value
    values: dict[str, list[tuple[Any, int]]] = field(default_factory=dict)
    # (column with gaps, group column) → (group value, rows, filled rows)
    filled_by: dict[tuple[str, str], list[tuple[Any, int, int]]] = field(default_factory=dict)
    # (a, b) → (a value, b value, rows), both filled
    crossed: dict[tuple[str, str], list[tuple[Any, Any, int]]] = field(default_factory=dict)
    duplicate_keys: int | None = None
    # The key columns the duplicates were counted on (a snapshot's date is not among them).
    key_columns: list[str] = field(default_factory=list)
    # column → (low, high, rows)
    histograms: dict[str, list[tuple[float, float, int]]] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    kind: Literal["missing", "concentration", "key"]
    severity: Severity
    column: str
    text: str


@dataclass
class Audit:
    profiles: list[dict[str, Any]]
    findings: list[Finding]
    parts: list[dict[str, Any]]
    summary: str


def _n(value: int | float) -> str:
    return vn_number(decimal.Decimal(str(value)), 0)


def _pct(part: int | decimal.Decimal, whole: int | decimal.Decimal) -> str:
    share = decimal.Decimal(part) / decimal.Decimal(whole) * 100 if whole else decimal.Decimal(0)
    return f"{vn_number(share, 1)}%"


# An amount or a date with few values in a small table is still not a category to group by.
_NOT_CATEGORIES = frozenset({"decimal", "date", "timestamp", "binary", "json"})


def is_category(c: Column, a: Aggregates) -> bool:
    return (
        not c.is_key
        and not c.pii
        and c.kind not in _NOT_CATEGORIES
        and 0 < a.distinct.get(c.name, 0) <= CATEGORICAL_MAX
    )


def _profile(c: Column, a: Aggregates) -> dict[str, Any]:
    filled = a.non_null.get(c.name, 0)
    values = a.values.get(c.name)
    return {
        "column": c.name,
        "label": c.label,
        "type": c.kind,
        "null_ratio": round(1 - filled / a.rows, 4) if a.rows else 0.0,
        "distinct": a.distinct.get(c.name, 0),
        # AC-04-02-02: a personal-data column shows neither its values nor its range.
        "min": None if c.pii else a.minimum.get(c.name),
        "max": None if c.pii else a.maximum.get(c.name),
        "top_values": "masked"
        if c.pii
        else [{"value": v, "rows": n} for v, n in values or [] if v is not None][:10],
    }


def _missing(c: Column, a: Aggregates, labels: dict[str, str]) -> Finding | None:
    filled = a.non_null.get(c.name, a.rows)
    empty = a.rows - filled
    if empty <= 0:
        return None
    plain = f"{c.label}: thiếu {_n(empty)}/{_n(a.rows)} dòng ({_pct(empty, a.rows)})."
    # The group column that explains most of the gap: groups where the column is wholly empty.
    # Such a group has more than a row or two: a lone empty row is the gap itself.
    least = max(2, a.rows // 100)
    best: tuple[int, str, list[tuple[Any, int, int]], list[tuple[Any, int, int]]] | None = None
    for (col, group), rows in a.filled_by.items():
        if col != c.name:
            continue
        absent = [r for r in rows if r[2] == 0 and r[1] >= least]
        applies = [r for r in rows if not (r[2] == 0 and r[1] >= least)]
        explained = sum(n for _, n, _ in absent)
        if absent and any(f > 0 for _, _, f in applies) and (best is None or explained > best[0]):
            best = (explained, group, absent, applies)
    if best is None:
        severity: Severity = "high" if empty / a.rows > HIGH_GAP else "warning"
        return Finding("missing", severity, c.name, plain)
    explained, group, absent, applies = best
    applicable = sum(n for _, n, _ in applies)
    gap = sum(n - f for _, n, f in applies)
    head = (
        f"{c.label}: trống {_n(empty)}/{_n(a.rows)} dòng, trong đó {_n(explained)} dòng không áp "
        f"dụng ({', '.join(str(v) for v, _, _ in absent)}) xét theo {labels.get(group, group)}"
    )
    if gap == 0:
        return Finding("missing", "info", c.name, f"{head}; không thiếu ở nhóm áp dụng.")
    where = ", ".join(str(v) for v, n, f in applies if n > f)
    severity = "high" if decimal.Decimal(gap) / applicable > HIGH_GAP else "warning"
    return Finding(
        "missing",
        severity,
        c.name,
        f"{head}; thiếu thật {_n(gap)}/{_n(applicable)} dòng ({_pct(gap, applicable)}) ở {where}.",
    )


def _spread(c: Column, a: Aggregates) -> dict[str, Any] | None:
    values = a.values.get(c.name)
    if not values:
        return None
    filled = sorted(((v, n) for v, n in values if v is not None), key=lambda x: -x[1])
    empty = sum(n for v, n in values if v is None)
    total = sum(n for _, n in filled)
    rows = [[str(v), n] for v, n in filled] + ([[EMPTY, empty]] if empty else [])
    summary = "; ".join(f"{v} {_pct(n, total)}" for v, n in filled)
    if empty:
        summary += f" (trên {_n(total)} dòng có giá trị; {_n(empty)} dòng trống)"
    return {
        "question": f"Phân bố {c.label}",
        "summary": f"{c.label}: {summary}.",
        "columns": [c.name, "value"],
        "rows": rows,
        "chart_spec": {"type": "bar", "x": c.name, "x_label": c.label, "y_label": "Số dòng"},
    }


def _histogram(c: Column, a: Aggregates) -> dict[str, Any] | None:
    bins = a.histograms.get(c.name)
    if not bins:
        return None
    rows = [[f"{_n(lo)} - {_n(hi)}", n] for lo, hi, n in bins]
    widest = max(bins, key=lambda b: b[2])
    return {
        "question": f"Phân bố {c.label}",
        "summary": (
            f"{c.label}: từ {_n(bins[0][0])} đến {_n(bins[-1][1])}; nhiều nhất trong khoảng "
            f"{_n(widest[0])} - {_n(widest[1])} ({_pct(widest[2], sum(b[2] for b in bins))})."
        ),
        "columns": ["khoang", "value"],
        "rows": rows,
        "chart_spec": {"type": "bar", "x": "khoang", "x_label": "Khoảng", "y_label": "Số dòng"},
    }


def _crossing(
    a_col: Column, b_col: Column, cells: list[tuple[Any, Any, int]]
) -> tuple[Finding | None, dict[str, Any], decimal.Decimal]:
    by_a: dict[Any, dict[Any, int]] = defaultdict(dict)
    overall: dict[Any, int] = defaultdict(int)
    for av, bv, n in cells:
        by_a[av][bv] = by_a[av].get(bv, 0) + n
        overall[bv] += n
    total = sum(overall.values())
    said = []
    strongest = decimal.Decimal(0)
    for av, counts in by_a.items():
        size = sum(counts.values())
        if not size or not total:
            continue

        def lift(bv: Any, counts: dict[Any, int] = counts, size: int = size) -> decimal.Decimal:
            share = decimal.Decimal(counts[bv]) / size
            return share / (decimal.Decimal(overall[bv]) / total)

        top = max(counts, key=lift)
        share = decimal.Decimal(counts[top]) / size
        if lift(top) >= LIFT_MIN and share >= MIN_SHARE:
            strongest = max(strongest, lift(top))
            said.append(
                f"{av} tập trung ở {top} ({_pct(counts[top], size)}, chung "
                f"{_pct(overall[top], total)})"
            )
    summary = (
        f"{a_col.label} theo {b_col.label}: " + "; ".join(said) + "."
        if said
        else f"{a_col.label} phân bố đều theo {b_col.label}."
    )
    part: dict[str, Any] = {
        "question": f"{a_col.label} theo {b_col.label}",
        "summary": summary,
        "columns": [b_col.name, a_col.name, "value"],
        "rows": [[str(bv), str(av), n] for av, bv, n in cells],
        "chart_spec": {
            "type": "bar",
            "x": b_col.name,
            "x_label": b_col.label,
            "series": a_col.name,
            "series_label": a_col.label,
            "y_label": "Số dòng",
        },
    }
    finding = Finding("concentration", "info", a_col.name, summary) if said else None
    return finding, part, strongest


def audit(dataset: str, columns: list[Column], a: Aggregates) -> Audit:
    labels = {c.name: c.label for c in columns}
    by_name = {c.name: c for c in columns}
    findings: list[Finding] = []
    for c in columns:
        gap = _missing(c, a, labels)
        if gap is not None:
            findings.append(gap)
    keys = [by_name[k] for k in a.key_columns if k in by_name] or [c for c in columns if c.is_key]
    if keys and a.duplicate_keys is not None:
        named = ", ".join(c.label for c in keys)
        findings.append(
            Finding(
                "key",
                "high",
                keys[0].name,
                f"Khoá {named} có {_n(a.duplicate_keys)} giá trị trùng.",
            )
            if a.duplicate_keys
            else Finding("key", "info", keys[0].name, f"Khoá {named} không trùng.")
        )

    parts = [p for c in columns if is_category(c, a) for p in [_spread(c, a)] if p]
    parts += [
        p
        for c in columns
        if c.numeric and not c.is_key and not is_category(c, a)
        for p in [_histogram(c, a)]
        if p
    ]
    crossings = []
    # A two-valued column (gender) crossed without concentrating anywhere: said once, plainly,
    # since "where do men and women concentrate" has an answer even when it is "nowhere".
    even: dict[str, list[str]] = defaultdict(list)
    concentrated: set[str] = set()
    for (an, bn), cells in a.crossed.items():
        if an in by_name and bn in by_name and cells:
            finding, part, strength = _crossing(by_name[an], by_name[bn], cells)
            if finding is not None:
                crossings.append((strength, finding, part))
                concentrated.add(an)
            else:
                even[an].append(bn)
    crossings.sort(key=lambda x: -x[0])
    for _, finding, part in crossings[:MAX_CROSSES]:
        findings.append(finding)
        parts.append(part)
    for an, bns in even.items():
        named = [
            str(v) for v, _ in sorted(a.values.get(an, []), key=lambda x: -x[1]) if v is not None
        ]
        if an in concentrated or len(named) != 2:
            continue
        findings.append(
            Finding(
                "concentration",
                "info",
                an,
                f"{labels[an]}: {', '.join(named)} phân bố đều theo "
                f"{', '.join(labels[b] for b in bns)} (không nhóm nào có tỷ lệ từ "
                f"{vn_number(LIFT_MIN, 1)} lần mức chung).",
            )
        )

    high = sum(f.severity == "high" for f in findings)
    warn = sum(f.severity == "warning" for f in findings)
    said = [f.text for f in findings if f.severity != "info"][:5]
    summary = (
        f"Kiểm toán {dataset} ({_n(a.rows)} dòng): {high} vấn đề cần xử lý, {warn} cảnh báo."
        + ("" if not said else " " + " ".join(said))
    )
    return Audit([_profile(c, a) for c in columns], findings, parts, summary)
