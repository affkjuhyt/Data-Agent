"""The answer's figures and text for a single-metric result, written from the figures alone.

Tab 5 G5.15: every number in an answer is a figure F1…Fn with its source (query_run row and
column) and the text carries numbers only through those figures, formatted the Vietnamese way
(1.234.567,89). The fast path writes the text from a template, not a model, so number_check is
complete by construction: every number shown is a traced figure. The LLM Narrator, with its
post-check and one regeneration, is the path for the other questions (R1 S5).

A figure whose data product is overdue is masked (masked_stale) and one with no data in time is
"CHƯA CÓ" (not_available) before the text is written (BR-178): the text never states them.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass, field
from typing import Any, Literal

MAX_LISTED = 12  # beyond this the text names the top values and the table holds the rest

DisplayState = Literal["shown", "masked_stale", "not_available"]


@dataclass
class Figure:
    key: str  # F1…
    label: str
    value: decimal.Decimal | None
    unit: str | None
    decimals: int
    row: int
    column: str
    dimensions: dict[str, str] = field(default_factory=dict)
    display_state: DisplayState = "shown"
    stale_note: str | None = None

    def as_json(self, query_run_id: str, metric_version_id: str, trust: str) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": format(self.value, "f") if self.value is not None else None,
            "unit": self.unit,
            "decimals": self.decimals,
            "dimensions": self.dimensions,
            "display_state": self.display_state,
            "source": {"query_run_id": query_run_id, "row": self.row, "col": self.column},
            "query_run_id": query_run_id,
            "metric_version_id": metric_version_id,
            "trust": trust,
        }


def vn_number(value: decimal.Decimal, decimals: int) -> str:
    """1234567.891 → "1.234.567,89" (A1.1 keeps the full value; this is only how it reads)."""
    q = value.quantize(decimal.Decimal(1).scaleb(-decimals), rounding=decimal.ROUND_HALF_UP)
    whole, _, frac = f"{abs(q):f}".partition(".")
    grouped = f"{int(whole):,}".replace(",", ".")
    sign = "-" if q < 0 else ""
    return f"{sign}{grouped},{frac}" if frac else f"{sign}{grouped}"


def figures_of(
    columns: list[str],
    rows: list[list[Any]],
    *,
    metric_name: str,
    unit: str | None,
    decimals: int,
    dimension_names: dict[str, str],
    period_label: str | None,
) -> list[Figure]:
    """One figure per row: the `value` column, labelled by its dimension members and period."""
    value_col = columns.index("value")
    dim_cols = [(i, c) for i, c in enumerate(columns) if c != "value"]
    out: list[Figure] = []
    for r, row in enumerate(rows):
        raw = row[value_col]
        value = None if raw is None else decimal.Decimal(str(raw))
        dims = {c: "" if row[i] is None else str(row[i]) for i, c in dim_cols}
        parts = [metric_name] + [f"{dimension_names.get(c, c)} {v}" for c, v in dims.items()]
        if period_label:
            parts.append(period_label)
        out.append(
            Figure(
                key=f"F{r + 1}",
                label=", ".join(parts),
                value=value,
                unit=unit,
                decimals=decimals,
                row=r,
                column="value",
                dimensions=dims,
                display_state="shown" if value is not None else "not_available",
            )
        )
    return out


def _shown(f: Figure) -> str:
    if f.display_state == "masked_stale":
        return "(đã ẩn: dữ liệu quá hạn cập nhật)"
    if f.display_state == "not_available" or f.value is None:
        return "CHƯA CÓ"
    unit = f" {f.unit}" if f.unit and f.unit != "%" else (f.unit or "")
    return f"{vn_number(f.value, f.decimals)}{unit}"


def text_of(
    figures: list[Figure],
    *,
    metric_name: str,
    group_by_names: list[str],
    period_label: str | None,
    filters_label: str | None,
    latest_note: str | None,
    truncated: bool,
) -> str:
    scope = " ".join(p for p in (filters_label, period_label) if p)
    head = f"{metric_name}{' ' + scope if scope else ''}"
    if not group_by_names and len(figures) == 1:
        text = f"{head} là {_shown(figures[0])}."
    else:
        by = ", ".join(group_by_names)
        listed = figures[:MAX_LISTED]
        lines = "; ".join(
            f"{', '.join(v for v in f.dimensions.values()) or f.key}: {_shown(f)}" for f in listed
        )
        more = (
            f" (và {len(figures) - len(listed)} dòng khác trong bảng)"
            if len(figures) > len(listed)
            else ""
        )
        text = f"{head} theo {by}: {lines}{more}."
    if latest_note:
        text += f" {latest_note}"
    if truncated:
        text += " Kết quả có hơn 10.000 dòng; bảng chỉ có 10.000 dòng đầu."
    return text
