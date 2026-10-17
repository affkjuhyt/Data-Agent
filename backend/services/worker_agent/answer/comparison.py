"""A compared result (semantic/compare.py) as figures, a chart and the reading of the change.

Each row has two traced figures, the value over the period asked and over the base period
({query_run_id, row, col} with col `value` or `base_value`), told apart by a pseudo-dimension
PERIOD_CODE: the table shows both side by side and the chart draws them as two series of bars.
The change of each row is a derived figure over the two (insights.py: expression, recomputed on
replay); a percentage changes in percentage points, never a percent of a percent.
"""

from __future__ import annotations

import datetime
import decimal
from typing import Any

from services.worker_agent.answer.insights import PERCENT_DECIMALS, Builder, Insights
from services.worker_agent.answer.narrate import Figure
from services.worker_agent.semantic.compiler import Period

PERIOD_CODE = "ky"
PERIOD_NAME = "Kỳ"
# Rows whose change is named in a sentence; the table holds every row's change.
NAMED_MOVERS = 1


def period_label(period: Period) -> str:
    """How a base period is named: a month, a quarter, a year, a day or a run of days."""
    start, end = period.start, period.end
    if start.day == 1 and end.day == 1:
        months = (end.year - start.year) * 12 + end.month - start.month
        if months == 1:
            return f"tháng {start.month}/{start.year}"
        if months == 3 and start.month % 3 == 1:
            return f"quý {(start.month - 1) // 3 + 1}/{start.year}"
        if months == 12 and start.month == 1:
            return f"năm {start.year}"
    last = end - datetime.timedelta(days=1)
    if last == start:
        return start.strftime("%d/%m/%Y")
    return f"{start:%d/%m/%Y} - {last:%d/%m/%Y}"


def comparison_figures(
    columns: list[str],
    rows: list[list[Any]],
    *,
    metric_name: str,
    unit: str | None,
    decimals: int,
    dimension_names: dict[str, str],
    period_label: str,
    base_label: str,
) -> list[Figure]:
    """Two figures per row, the period's then the base period's, F1 F2 for row 0 and so on."""
    sides = (("value", period_label), ("base_value", base_label))
    dim_cols = [(i, c) for i, c in enumerate(columns) if c not in ("value", "base_value")]
    out: list[Figure] = []
    for r, row in enumerate(rows):
        dims = {c: "" if row[i] is None else str(row[i]) for i, c in dim_cols}
        for column, label in sides:
            raw = row[columns.index(column)]
            value = None if raw is None else decimal.Decimal(str(raw))
            parts = [metric_name] + [f"{dimension_names.get(c, c)} {v}" for c, v in dims.items()]
            out.append(
                Figure(
                    key=f"F{len(out) + 1}",
                    label=", ".join([*parts, label]),
                    value=value,
                    unit=unit,
                    decimals=decimals,
                    row=r,
                    column=column,
                    dimensions={**dims, PERIOD_CODE: label},
                    display_state="shown" if value is not None else "not_available",
                )
            )
    return out


def comparison_insights(
    figures: list[Figure],
    *,
    group_by: list[str],
    metric_name: str,
    unit: str | None,
    decimals: int,
    period_label: str,
    base_label: str,
) -> Insights:
    """The change of every row whose two figures are shown, and the sentences that read it."""
    b = Builder(figures, unit, decimals)
    change_unit = "điểm %" if unit == "%" else unit
    moved: list[tuple[decimal.Decimal, str, str, str | None]] = []
    for now, before in zip(figures[::2], figures[1::2], strict=True):
        if now.value is None or before.value is None:
            continue
        if now.display_state != "shown" or before.display_state != "shown":
            continue
        member = ", ".join(now.dimensions[c] for c in group_by)
        about = f"{metric_name}{': ' + member if member else ''}"
        delta = b.add(
            f"{about}, thay đổi {period_label} so với {base_label}",
            f"{now.key} - {before.key}",
            unit=change_unit,
            decimals=decimals,
            signed=True,
        )
        if delta is None:
            continue
        pct = None
        if unit != "%":
            pct = b.add(
                f"{about}, tỷ lệ thay đổi {period_label} so với {base_label}",
                f"({now.key} - {before.key}) / {before.key} * 100",
                unit="%",
                decimals=PERCENT_DECIMALS,
                signed=True,
            )
        moved.append((now.value - before.value, member, delta, pct))
    if not moved:
        return b.out

    def said(change: decimal.Decimal, delta: str, pct: str | None) -> str:
        verb = "tăng" if change > 0 else "giảm" if change < 0 else "đi ngang"
        return (
            f"{verb} {delta} ({pct})" if pct and change else f"{verb} {delta}" if change else verb
        )

    if not group_by:
        change, _, delta, pct = moved[0]
        b.out.sentences.append(
            f"{period_label.capitalize()} {said(change, delta, pct)} so với {base_label}."
        )
        return b.out
    ups = sorted((m for m in moved if m[0] > 0), key=lambda m: m[0], reverse=True)
    downs = sorted((m for m in moved if m[0] < 0), key=lambda m: m[0])
    b.out.sentences.append(
        f"So với {base_label}: {len(ups)} nhóm tăng, {len(downs)} nhóm giảm"
        f"{f', {len(moved) - len(ups) - len(downs)} nhóm đi ngang' if len(moved) > len(ups) + len(downs) else ''}."
    )
    for change, member, delta, pct in ups[:NAMED_MOVERS] + downs[:NAMED_MOVERS]:
        kind = "Tăng mạnh nhất" if change > 0 else "Giảm mạnh nhất"
        b.out.sentences.append(f"{kind} là {member}: {delta}{f' ({pct})' if pct else ''}.")
    return b.out
