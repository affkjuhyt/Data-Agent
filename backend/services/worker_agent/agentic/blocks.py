"""The answer as the agent composes it: a list of typed blocks in the order it chose.

The reporter model writes block *drafts* (`BlockDraft`, one flat shape so constrained decoding
stays simple). The server turns each into the block the client draws (`materialize`): a chart or
table gets its data from the named dataset, a KPI its value from a dataset cell. The model
chooses what to show and how; it never writes a figure into a chart, a table or a KPI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from services.worker_agent.agentic.workspace import (
    MAX_BLOCK_ROWS,
    TRUST_LABEL,
    Dataset,
    Workspace,
    number,
    plain,
)

BlockType = Literal[
    "markdown", "kpi", "chart", "table", "insights", "callout", "code", "follow_ups"
]
ChartType = Literal["bar", "line", "area", "pie", "scatter", "composed"]
# Chart types the client draws; anything else falls back to bar.
CHART_TYPES = {"bar", "line", "area", "pie", "scatter", "composed", "radar", "radialBar"}
MAX_CHART_POINTS = 120
# A KPI label that says it is a total (normalised words, both languages).
_TOTAL = re.compile(r"\b(t[oổ]ng|total|overall)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ChartRole:
    """What a chart type needs of its columns (the idea of a chart registry with
    role contracts, independently written): the kind of x, how many measures, how many rows, and whether measures must be >= 0."""

    x: Literal["time", "category", "number", "any"]
    measures: tuple[int, int]
    rows: tuple[int, int]
    non_negative: bool = False


CHART_ROLES: dict[str, ChartRole] = {
    "line": ChartRole("time", (1, 4), (2, MAX_CHART_POINTS)),
    "area": ChartRole("time", (1, 3), (2, MAX_CHART_POINTS)),
    "bar": ChartRole("any", (1, 4), (2, 50)),
    "composed": ChartRole("any", (2, 3), (2, 60)),
    # A share of a whole: few members, one measure, nothing negative.
    "pie": ChartRole("category", (1, 1), (2, 8), non_negative=True),
    "scatter": ChartRole("number", (1, 1), (3, MAX_CHART_POINTS)),
}
_TIME_WORDS = (
    "ngày",
    "tháng",
    "quý",
    "năm",
    "tuần",
    "kỳ",
    "date",
    "day",
    "month",
    "quarter",
    "year",
    "week",
    "period",
    "time",
)
_TIME_VALUE = re.compile(
    r"^\d{4}(-\d{2}(-\d{2})?|-q[1-4]|q[1-4])?$|^(q[1-4]|t?\d{1,2})[/-]\d{4}$", re.I
)


def _x_kind(column: str, values: list[Any]) -> str:
    if any(w in column.lower() for w in _TIME_WORDS) or (
        values and all(_TIME_VALUE.match(str(v).strip()) for v in values if v is not None)
    ):
        return "time"
    if values and all(number(v) is not None for v in values if v is not None):
        return "number"
    return "category"


# Years only: a ranked axis of 4-digit codes (product 1234) is not reordered.
_ISO_TIME = re.compile(r"^((?:19|20)\d{2})(?:-(\d{1,2})(?:-(\d{1,2}))?|-?q([1-4]))?$", re.I)
_LOCAL_TIME = re.compile(r"^(?:q([1-4])|t?(\d{1,2}))[/-]((?:19|20)\d{2})$", re.I)


def time_order(value: Any) -> tuple[int, int, int] | None:
    """Where a member of a time axis falls ("2013-05", "2013-Q2", "05/2013", "Q2/2013"), or None
    for one that is not a period."""
    text = str(value).strip() if value is not None else ""
    if m := _ISO_TIME.match(text):
        month = int(m.group(2) or 0) or int(m.group(4) or 0) * 3
        return int(m.group(1)), month, int(m.group(3) or 0)
    if m := _LOCAL_TIME.match(text):
        return int(m.group(3)), int(m.group(1) or 0) * 3 or int(m.group(2)), 0
    return None


def _pct(value: float) -> str:
    return f"{abs(value):,.1f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def chart_insight(
    chart_type: str, x_kind: str, x_values: list[Any], values: list[float | None]
) -> str | None:
    """One line under a chart's title (E2), computed here from the drawn rows, never by the
    model: over time the change from the first to the last point and where it peaked; a share
    chart its largest part; a bar chart its highest and lowest member. Names and percentages only,
    so no figure is written twice in two formats."""
    points = [(x, v) for x, v in zip(x_values, values, strict=False) if v is not None]
    if len(points) < 2:
        return None
    high = max(points, key=lambda p: p[1])
    low = min(points, key=lambda p: p[1])
    if x_kind == "time":
        (first_x, first), (last_x, last) = points[0], points[-1]
        line = f"Từ {first_x} đến {last_x}"
        if first > 0:
            change = (last - first) / first * 100
            line += f" {'tăng' if change >= 0 else 'giảm'} {_pct(change)}%"
        return f"{line}; cao nhất {high[0]}, thấp nhất {low[0]}."
    if chart_type == "pie":
        total = sum(v for _, v in points)
        if total > 0 and low[1] >= 0:
            return f"{high[0]} lớn nhất, chiếm {_pct(high[1] / total * 100)}% tổng."
        return None
    return f"{high[0]} cao nhất, {low[0]} thấp nhất."


def _side_name(label: str, other: str) -> str:
    """What sets a series apart from the other: "2012" of "Doanh thu 2012" beside "Doanh thu
    2013"; the whole label when they share no leading words."""
    a, b = label.split(), other.split()
    shared = 0
    while shared < min(len(a), len(b)) - 1 and a[shared] == b[shared]:
        shared += 1
    # "tháng 3/2013", not "3/2013": the word that says which kind of period stays.
    if shared and a[shared - 1].lower() in ("tháng", "quý", "năm"):
        shared -= 1
    return " ".join(a[shared:]) or label


def comparison_insight(
    x_values: list[Any],
    before: list[float | None],
    after: list[float | None],
    labels: tuple[str, str],
) -> str | None:
    """A two-series bar chart of members (2012 beside 2013 per region) read as the change from the
    first series to the second: who rose most and who fell most, or rose least. Its highest member
    of the first series alone said nothing about the comparison drawn."""
    changes = [
        (x, (a - b) / b * 100)
        for x, b, a in zip(x_values, before, after, strict=False)
        if a is not None and b is not None and b > 0
    ]
    if len(changes) < 2:
        return None
    up = max(changes, key=lambda c: c[1])
    down = min(changes, key=lambda c: c[1])

    def sign(p: float) -> str:
        # A fall is said in words ("giảm nhiều nhất (31,3%)"), so only a rise carries a sign.
        return f"{'+' if p >= 0 else ''}{_pct(p)}%"

    base = _side_name(labels[0], labels[1])
    first = f"So với {base}, {up[0]} tăng mạnh nhất ({sign(up[1])})"
    if down[1] < 0:
        return f"{first}, {down[0]} giảm nhiều nhất ({sign(down[1])})."
    return f"{first}, {down[0]} tăng ít nhất ({sign(down[1])})."


def series_insight(
    x_values: list[Any], measures: list[list[float | None]], labels: list[str]
) -> str | None:
    """Several series over time, each read from its first to its last point ("Từ 2013-01 đến
    2013-12: 2012 tăng 40,0%, 2013 tăng 118,5%."); the first series alone described only itself."""
    if len(labels) < 2 or len(labels) > 3:
        return None
    names = (
        [_side_name(labels[0], labels[1]), _side_name(labels[1], labels[0])]
        if len(labels) == 2
        else labels
    )
    parts, span = [], None
    for name, values in zip(names, measures, strict=False):
        points = [(x, v) for x, v in zip(x_values, values, strict=False) if v is not None]
        if len(points) < 2 or points[0][1] <= 0:
            continue
        change = (points[-1][1] - points[0][1]) / points[0][1] * 100
        parts.append(f"{name} {'tăng' if change >= 0 else 'giảm'} {_pct(change)}%")
        span = span or (points[0][0], points[-1][0])
    if not parts or span is None:
        return None
    return f"Từ {span[0]} đến {span[1]}: {', '.join(parts)}."


# The period a series is named by, at its end: "Doanh thu 2012", "Doanh thu 2013-05",
# "Doanh thu 2013-Q3", "Doanh thu Q3/2013", "Doanh thu tháng 3/2013", "Doanh thu quý 3/2013".
_SERIES_PERIOD = re.compile(
    r"(?:(?:Q|quý\s?)([1-4])[/ ]|(\d{1,2})/)?((?:19|20)\d{2})(?:-(?:Q([1-4])|(\d{1,2})))?\)?$",
    re.IGNORECASE,
)


def period_key(label: str) -> tuple[int, int] | None:
    """Where a series named by a period falls in time, to draw the earlier one first."""
    m = _SERIES_PERIOD.search(label.strip())
    if m is None:
        return None
    quarter = m.group(1) or m.group(4)
    month = m.group(2) or m.group(5)
    return int(m.group(3)), int(quarter) * 3 if quarter else int(month or 0)


def chart_fits(chart_type: str, x_kind: str, measures: list[list[float | None]], rows: int) -> bool:
    role = CHART_ROLES.get(chart_type)
    if role is None:
        return False
    if role.x != "any" and role.x != x_kind:
        return False
    if not role.measures[0] <= len(measures) <= role.measures[1]:
        return False
    if not role.rows[0] <= rows <= role.rows[1]:
        return False
    return not (role.non_negative and any(v is not None and v < 0 for m in measures for v in m))


def fitting_charts(x_kind: str, measures: list[list[float | None]], rows: int) -> list[str]:
    """The chart types this data fits, the most fitting first: a line along time, a bar across
    categories, a scatter of two numbers."""
    order = {
        "time": ["line", "bar", "area", "composed"],
        "category": ["bar", "pie", "composed"],
        "number": ["scatter", "bar", "line"],
    }[x_kind]
    return [t for t in order if chart_fits(t, x_kind, measures, rows)]


class KpiDraft(BaseModel):
    label: str
    dataset: str
    column: str
    # Which row: 0 for a one-row dataset; else the row whose `match_column` equals `match_value`.
    row: int = 0
    match_column: str | None = None
    match_value: str | None = None
    # A column of the same row to show as the change (previous period, delta, percent).
    compare_column: str | None = None


class InsightDraft(BaseModel):
    text: str
    tone: Literal["positive", "negative", "neutral"] = "neutral"


class BlockDraft(BaseModel):
    type: BlockType
    title: str | None = None
    # markdown, callout: the text (markdown allowed); code: shown as is.
    content: str | None = None
    tone: Literal["info", "warning", "success"] | None = None
    # chart, table, kpi, code: the dataset it shows.
    dataset: str | None = None
    chart_type: ChartType | None = None
    x: str | None = None
    y: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    kpis: list[KpiDraft] = Field(default_factory=list)
    insights: list[InsightDraft] = Field(default_factory=list)
    items: list[str] = Field(default_factory=list)


class Composition(BaseModel):
    """The reporter's answer: the blocks, a one-line headline for lists and notifications."""

    headline: str
    blocks: list[BlockDraft]


class BlockError(ValueError):
    pass


def _dataset(ws: Workspace, name: str | None) -> Dataset:
    ds = ws.get(name or "")
    if ds is None:
        raise BlockError(f"không có dataset {name}")
    return ds


def _column(ds: Dataset, name: str | None) -> str:
    if name in ds.columns:
        return name
    raise BlockError(f"dataset {ds.id} không có cột {name}; các cột: {ds.columns}")


def _row(ds: Dataset, k: KpiDraft) -> list[Any]:
    if k.match_column:
        i = ds.columns.index(_column(ds, k.match_column))
        for row in ds.rows:
            if str(plain(row[i])) == str(k.match_value):
                return row
        raise BlockError(f"không có dòng {k.match_column}={k.match_value} trong {ds.id}")
    if not 0 <= k.row < len(ds.rows):
        raise BlockError(f"dataset {ds.id} không có dòng {k.row}")
    return ds.rows[k.row]


def _explore(ds: Dataset) -> dict[str, Any] | None:
    """The reading Khám phá can open again: a certified metric's, by its id and dimension codes.
    Khám phá lists only certified metrics, so another dataset has nothing to open there."""
    r = ds.reading
    if not r or ds.trust != "certified" or not r.get("metric_id"):
        return None
    return {
        "metric_id": r["metric_id"],
        "group_by": list(r.get("group_by", [])),
        "filters": dict(r.get("filters", {})),
        "cuts": dict(r.get("cuts", {})),
        "time_cuts": list(r.get("time_cuts", [])),
    }


def _source(ds: Dataset) -> dict[str, Any]:
    explore = _explore(ds)
    return {
        "dataset": ds.id,
        "trust": ds.trust,
        "trust_label": TRUST_LABEL[ds.trust],
        "query_run_ids": ds.query_run_ids,
        **({"explore": explore} if explore else {}),
    }


def materialize(draft: BlockDraft, ws: Workspace, block_id: str) -> dict[str, Any]:
    """The block the client draws, with its data from the workspace; BlockError when the draft
    names a dataset, a column or a row that does not exist (the reporter may then fix it)."""
    t = draft.type
    # `composed` tells the client the agent chose this block and its place (ComposedBlocks.tsx).
    base: dict[str, Any] = {"type": t, "id": block_id, "composed": True}
    if draft.title:
        base["title"] = draft.title
    if t == "markdown":
        if not (draft.content or "").strip():
            raise BlockError("markdown rỗng")
        return {**base, "content": draft.content}
    if t == "callout":
        return {**base, "tone": draft.tone or "info", "content": draft.content or ""}
    if t == "insights":
        items = [i.model_dump() for i in draft.insights] or [
            {"text": s, "tone": "neutral"} for s in draft.items
        ]
        if not items:
            raise BlockError("insights rỗng")
        return {**base, "items": items}
    if t == "follow_ups":
        return {**base, "items": [s for s in draft.items if s.strip()][:5]}
    if t == "code":
        ds = ws.get(draft.dataset or "")
        code = draft.content or (ds.code if ds else None)
        if not code:
            raise BlockError("code rỗng")
        return {**base, "language": "python", "content": code}
    if t == "kpi":
        items = []
        for k in draft.kpis:
            ds = _dataset(ws, k.dataset)
            if len(ds.rows) > 1:
                # A row of a many-row result taken by its index, or called a total, was shown as
                # "Tổng doanh thu 2013" over one month's running total: every figure was traced,
                # its name was not. A total is a reading without the cut, one row.
                if not k.match_column:
                    raise BlockError(
                        f"dataset {ds.id} có {len(ds.rows)} dòng: chỉ rõ dòng của kpi"
                        f" '{k.label}' bằng match_column và match_value"
                    )
                if _TOTAL.search(k.label):
                    raise BlockError(
                        f"kpi '{k.label}' gọi một dòng của {ds.id} là tổng; tổng là truy vấn"
                        " chỉ số không chia (một dòng), hoặc bỏ kpi này"
                    )
            row = _row(ds, k)
            value = number(row[ds.columns.index(_column(ds, k.column))])
            item: dict[str, Any] = {
                "label": k.label,
                "value": value,
                "unit": ds.unit,
                **_source(ds),
            }
            if k.compare_column:
                previous = number(row[ds.columns.index(_column(ds, k.compare_column))])
                item["compare_label"] = k.compare_column
                item["compare_value"] = previous
                if previous not in (None, 0) and value is not None:
                    item["change_pct"] = round((value - previous) / abs(previous) * 100, 2)
            items.append(item)
        if not items:
            raise BlockError("kpi rỗng")
        return {**base, "items": items}
    if t == "table":
        ds = _dataset(ws, draft.dataset)
        columns = [_column(ds, c) for c in draft.columns] or list(ds.columns)
        idx = [ds.columns.index(c) for c in columns]
        rows = [{c: plain(r[i]) for c, i in zip(columns, idx, strict=True)} for r in ds.rows]
        return {
            **base,
            "title": draft.title or ds.title,
            "columns": columns,
            "rows": rows[:MAX_BLOCK_ROWS],
            "row_count": len(ds.rows),
            "unit": ds.unit,
            **_source(ds),
        }
    if t == "chart":
        ds = _dataset(ws, draft.dataset)
        x = _column(ds, draft.x or (ds.columns[0] if ds.columns else None))
        y = [_column(ds, c) for c in draft.y] or [
            c
            for c in ds.columns
            if c != x and ds.rows and number(ds.rows[0][ds.columns.index(c)]) is not None
        ][:3]
        if not y:
            raise BlockError(f"dataset {ds.id} không có cột số để vẽ")
        keys = [period_key(c) for c in y]
        timed = len(y) > 1 and all(keys) and len(set(keys)) == len(keys)
        if timed:
            # Series named by periods are drawn earlier first: "Doanh thu 2013" came before
            # "Doanh thu 2012" in the legend and the bars when the model listed them that way.
            y = [c for _, c in sorted(zip(keys, y, strict=True))]
        # A time axis is drawn in time order: a reading ranked by value ("top_desc") drew its
        # months 12, 6, 10… and its line read "Từ 2013-12 đến 2013-02 giảm 58,8%" (16/11).
        rows = ds.rows
        order = [time_order(plain(r[ds.columns.index(x)])) for r in rows]
        if rows and all(k is not None for k in order):
            rows = [r for _, r in sorted(zip(order, rows, strict=True), key=lambda p: p[0])]
        # Fitted on every row, not the first MAX_CHART_POINTS: a chart of the first 120 days of a
        # year drew a line that stopped in May with nothing saying the rest was missing.
        x_values = [plain(r[ds.columns.index(x)]) for r in rows]
        measures = [[number(r[ds.columns.index(c)]) for r in rows] for c in y]
        fits = fitting_charts(_x_kind(x, x_values), measures, len(rows))
        if not fits:
            # No chart fits these rows (one row, too many members or points): they are shown as a
            # table, which says how many rows there are.
            return materialize(
                draft.model_copy(update={"type": "table", "columns": [x, *y]}), ws, block_id
            )
        chart_type = draft.chart_type if draft.chart_type in fits else fits[0]
        data = []
        for r in rows:
            point: dict[str, Any] = {x: plain(r[ds.columns.index(x)])}
            for c in y:
                point[c] = number(r[ds.columns.index(c)])
            data.append(point)
        spec: dict[str, Any] = {
            "chart_type": chart_type,
            "title": draft.title or ds.title,
            "x_key": x,
            "y_keys": y,
            "data": data,
        }
        if chart_type == "composed":
            spec["series"] = [
                {"key": c, "type": "bar" if i == 0 else "line"} for i, c in enumerate(y)
            ]
        x_kind = _x_kind(x, x_values)
        insight: str | None = None
        if len(y) == 1:
            insight = chart_insight(chart_type, x_kind, x_values, measures[0])
        elif x_kind == "time":
            insight = series_insight(x_values, measures, y)
        elif len(y) == 2 and timed and chart_type == "bar":
            # Two periods of one quantity side by side; other pairs (revenue beside a margin)
            # have no change between them to read, and get no line rather than a wrong one.
            insight = comparison_insight(x_values, measures[0], measures[1], (y[0], y[1]))
        # The other types the same rows fit: the chart's switcher offers them (chart adjustment).
        return {
            **base,
            "spec": spec,
            "alternatives": fits,
            "unit": ds.unit,
            **({"insight": insight} if insight else {}),
            **_source(ds),
        }
    raise BlockError(f"không biết block {t}")
