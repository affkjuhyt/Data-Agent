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


@dataclass(frozen=True)
class ChartRole:
    """What a chart type needs of its columns (Data-Analysis-Agent's chart registry, WrenAI's chart
    rules): the kind of x, how many measures, how many rows, and whether measures must be >= 0."""

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


def _source(ds: Dataset) -> dict[str, Any]:
    return {
        "dataset": ds.id,
        "trust": ds.trust,
        "trust_label": TRUST_LABEL[ds.trust],
        "query_run_ids": ds.query_run_ids,
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
        shown_rows = ds.rows[:MAX_CHART_POINTS]
        x_values = [plain(r[ds.columns.index(x)]) for r in shown_rows]
        measures = [[number(r[ds.columns.index(c)]) for r in shown_rows] for c in y]
        fits = fitting_charts(_x_kind(x, x_values), measures, len(shown_rows))
        if not fits:
            # No chart fits these rows (one row, too many members): they are shown as a table.
            return materialize(
                draft.model_copy(update={"type": "table", "columns": [x, *y]}), ws, block_id
            )
        chart_type = draft.chart_type if draft.chart_type in fits else fits[0]
        data = []
        for r in ds.rows[:MAX_CHART_POINTS]:
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
        # The other types the same rows fit: the chart's switcher offers them (chart adjustment).
        return {**base, "spec": spec, "alternatives": fits, "unit": ds.unit, **_source(ds)}
    raise BlockError(f"không biết block {t}")
