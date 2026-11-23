"""The chart validator (AL-03, ANALYTICS_SPEC §B.2): a chart is checked against its data before it
is drawn.

A chart could be drawn wrong while every figure in it was right: a share of a COUNT DISTINCT
(customers counted once per region do not add to the customers), a measure that never moves, two
series that are one, months drawn out of order, a pie of negative values. The chart roles
(agentic/blocks.CHART_ROLES) only checked the shape of the columns. Here each chart is read with
its data and what the semantic layer says of its measures, and every problem gets a code:

- `block`: the chart is not drawn; the caller shows the rows as a table with a callout saying why;
- `warn`: the chart is drawn with the note.

Deterministic, no model. An issue whose cause is a declaration the steward can fix (a measure
declared additive whose SQL is a distinct count) carries a `proposal`, which the caller files in
the Review Inbox: nothing here changes the semantic layer.

`CHART_CHECK=off` turns it off (one release, the spec's rollback).
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from libs import metrics

Code = Literal[
    "CONSTANT_MEASURE",
    "DUPLICATE_COLUMNS",
    "GRAIN_MISMATCH",
    "NON_ADDITIVE_SHARE",
    "NON_ADDITIVE_SUM",
    "HIDDEN_AXIS",
    "PIE_NEGATIVE",
    "TOO_MANY_CATEGORIES",
    "AMBIGUOUS_LABEL",
    "NO_TIME_ORDER",
]
CODES: tuple[str, ...] = Code.__args__  # type: ignore[attr-defined]
Severity = Literal["block", "warn"]

MAX_PIE = 8
CONSTANT_POINTS = 3
MAX_BAR = 40
# A label that names no quantity: the reader cannot tell what the bars are.
_GENERIC = frozenset({"", "value", "values", "giá trị", "gia tri", "y", "measure", "số liệu"})


def enabled() -> bool:
    return os.environ.get("CHART_CHECK", "on").strip().lower() != "off"


@dataclass(frozen=True)
class Series:
    """One measure drawn: its key, the label the reader sees, its values in the x order, and its
    additivity as the semantic layer knows it (`full`, `semi` over time, `none`; None unknown)."""

    key: str
    label: str
    values: Sequence[float | None]
    additivity: str | None = None
    unit: str | None = None
    # What a proposal names when the declaration is the cause (metric_id, measure_code,
    # declared): set by the caller for a measure whose declared additivity says more than its
    # SQL allows.
    measure: dict[str, Any] | None = None


@dataclass(frozen=True)
class Chart:
    chart_type: str
    x_key: str
    x_label: str
    x_kind: str  # time, category, number
    x_values: Sequence[Any]
    series: Sequence[Series]
    # "normal": the series are stacked into a total; "percent": into shares of 100.
    stack: str | None = None
    axis_labels_visible: bool = True
    # The grain the question asked for ("month") and the one the x values have.
    requested_grain: str | None = None


@dataclass(frozen=True)
class ChartIssue:
    code: Code
    severity: Severity
    detail: str
    proposal: dict[str, Any] | None = field(default=None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "detail": self.detail,
            **({"proposal": self.proposal} if self.proposal else {}),
        }


_YEAR = re.compile(r"^(19|20)\d{2}$")
_QUARTER = re.compile(r"^(19|20)\d{2}-?q[1-4]$|^q[1-4][/-](19|20)\d{2}$", re.I)
_MONTH = re.compile(r"^(19|20)\d{2}-\d{1,2}$|^t?\d{1,2}[/-](19|20)\d{2}$", re.I)
_DAY = re.compile(r"^(19|20)\d{2}-\d{1,2}-\d{1,2}")
_GRAIN_WORDS = {
    "day": "day",
    "ngày": "day",
    "week": "week",
    "tuần": "week",
    "month": "month",
    "tháng": "month",
    "quarter": "quarter",
    "quý": "quarter",
    "year": "year",
    "năm": "year",
}


def time_grain(values: Sequence[Any]) -> str | None:
    """The grain a time axis' members have, when they all agree: 2013 year, 2013-Q2 quarter,
    2013-05 month, 2013-05-01 day."""
    grains = set()
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if _DAY.match(text):
            grains.add("day")
        elif _QUARTER.match(text):
            grains.add("quarter")
        elif _MONTH.match(text):
            grains.add("month")
        elif _YEAR.match(text):
            grains.add("year")
        else:
            return None
    return grains.pop() if len(grains) == 1 else None


def _normalized(label: str) -> str:
    return re.sub(r"[\s_\-]+", " ", label.strip().lower())


def _distinct(values: Sequence[float | None]) -> int:
    return len({v for v in values if v is not None})


def check(chart: Chart, *, time_order: Any = None) -> list[ChartIssue]:
    """Every issue of the chart, blocking ones first. `time_order(value)` places a member of a
    time axis (agentic/blocks.time_order); without it the order is not checked."""
    issues: list[ChartIssue] = []
    points = len(chart.x_values)
    share = chart.chart_type == "pie" or chart.stack == "percent"

    for s in chart.series:
        # Two members equal is a comparison ("both regions at 10%"); three or more all equal is a
        # measure that did not split by the axis (a total repeated, a join that did not cut).
        if points >= CONSTANT_POINTS and _distinct(s.values) <= 1:
            issues.append(
                ChartIssue(
                    "CONSTANT_MEASURE",
                    "block",
                    f"“{s.label}” không đổi theo {chart.x_label}: biểu đồ không cho thấy gì",
                )
            )
        if share and s.additivity == "none":
            issues.append(
                ChartIssue(
                    "NON_ADDITIVE_SHARE",
                    "block",
                    f"“{s.label}” không cộng được giữa các nhóm, nên không chia thành tỷ trọng",
                    proposal=_proposal(s),
                )
            )
        if chart.stack == "normal" and s.additivity == "none":
            issues.append(
                ChartIssue(
                    "NON_ADDITIVE_SUM",
                    "block",
                    f"“{s.label}” không cộng được, nên không chồng các nhóm thành một tổng",
                    proposal=_proposal(s),
                )
            )
        if (
            chart.stack in ("normal", "percent")
            and chart.x_kind == "time"
            and s.additivity == "semi"
            and chart.chart_type == "area"
            and len(chart.series) == 1
        ):
            # A balance stacked over time reads as a running total of balances.
            issues.append(
                ChartIssue(
                    "NON_ADDITIVE_SUM",
                    "block",
                    f"“{s.label}” là số dư, không cộng dồn theo thời gian",
                )
            )

    labels = [_normalized(s.label) for s in chart.series]
    vectors = [tuple(s.values) for s in chart.series]
    if len(set(labels)) < len(labels) or (len(vectors) > 1 and len(set(vectors)) < len(vectors)):
        issues.append(
            ChartIssue("DUPLICATE_COLUMNS", "block", "hai chuỗi trên biểu đồ là cùng một số liệu")
        )

    grain = time_grain(chart.x_values) if chart.x_kind == "time" else None
    asked = _GRAIN_WORDS.get((chart.requested_grain or "").strip().lower())
    if grain and asked and grain != asked:
        issues.append(
            ChartIssue(
                "GRAIN_MISMATCH",
                "block",
                f"dữ liệu theo {_VI[grain]}, câu hỏi hỏi theo {_VI[asked]}",
            )
        )

    if chart.chart_type == "pie" and any(
        v is not None and v < 0 for s in chart.series for v in s.values
    ):
        issues.append(ChartIssue("PIE_NEGATIVE", "block", "có giá trị âm, không vẽ tỷ trọng được"))

    members = len({str(v) for v in chart.x_values})
    if chart.chart_type == "pie" and members > MAX_PIE:
        issues.append(
            ChartIssue(
                "TOO_MANY_CATEGORIES", "warn", f"{members} phần trong một biểu đồ tròn khó đọc"
            )
        )
    if chart.chart_type in ("bar", "line") and chart.x_kind == "category" and members > MAX_BAR:
        issues.append(
            ChartIssue(
                "TOO_MANY_CATEGORIES",
                "block",
                f"{members} nhóm trên một trục, quá {MAX_BAR}: xem dạng bảng",
            )
        )

    if not chart.axis_labels_visible or not chart.x_label.strip():
        issues.append(ChartIssue("HIDDEN_AXIS", "warn", "nhãn trục bị ẩn"))

    units = {s.unit for s in chart.series if s.unit}
    generic = [s for s in chart.series if _normalized(s.label) in _GENERIC]
    if generic and not chart.series[0].unit:
        issues.append(
            ChartIssue(
                "AMBIGUOUS_LABEL", "warn", "chuỗi số không nói là số gì, cũng không có đơn vị"
            )
        )
    if len(units) > 1 and chart.chart_type != "composed":
        issues.append(
            ChartIssue(
                "AMBIGUOUS_LABEL",
                "warn",
                f"các chuỗi khác đơn vị ({', '.join(sorted(units))}) trên cùng một trục",
            )
        )

    if chart.x_kind == "time" and time_order is not None and points > 1:
        keys = [time_order(v) for v in chart.x_values]
        if all(k is not None for k in keys) and keys != sorted(keys):
            issues.append(ChartIssue("NO_TIME_ORDER", "block", "chuỗi thời gian chưa được sắp xếp"))
        elif any(k is None for k in keys) and any(k is not None for k in keys):
            issues.append(
                ChartIssue(
                    "NO_TIME_ORDER",
                    "block",
                    "trục thời gian lẫn giá trị không phải kỳ, không sắp được theo thời gian",
                )
            )

    issues.sort(key=lambda i: i.severity != "block")
    return issues


def record(issues: Sequence[dict[str, Any]]) -> None:
    """Count the issues of a chart that was placed in an answer (the reporter's probes check the
    same chart more than once and are not counted)."""
    for issue in issues:
        metrics.CHART_CHECK_ISSUES.labels(issue["code"], issue["severity"]).inc()


_VI = {"day": "ngày", "week": "tuần", "month": "tháng", "quarter": "quý", "year": "năm"}


def _proposal(s: Series) -> dict[str, Any] | None:
    """A declaration to correct, when the measure is declared additive but its SQL is not."""
    m = s.measure
    if not m or m.get("declared") in (None, "none"):
        return None
    return {
        "kind": "measure_additivity",
        "metric_id": m.get("metric_id"),
        "measure_code": m.get("measure_code"),
        "from": m.get("declared"),
        "to": "none",
    }


def described(issues: Sequence[dict[str, Any]]) -> str:
    """reason() of issues kept as dicts (as_dict), as a turn's trace and a block carry them."""
    return reason(
        [ChartIssue(i["code"], i["severity"], i["detail"], i.get("proposal")) for i in issues]
    )


def blocking(issues: Sequence[ChartIssue]) -> list[ChartIssue]:
    return [i for i in issues if i.severity == "block"]


def reason(issues: Sequence[ChartIssue]) -> str:
    """One sentence for the callout that replaces or annotates the chart."""
    blocked = blocking(issues)
    if blocked:
        return "Không vẽ biểu đồ: " + "; ".join(i.detail for i in blocked) + ". Số liệu ở bảng."
    return "Lưu ý về biểu đồ: " + "; ".join(i.detail for i in issues) + "."
