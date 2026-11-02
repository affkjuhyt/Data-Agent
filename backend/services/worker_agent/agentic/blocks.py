"""The answer as the agent composes it: a list of typed blocks in the order it chose.

The reporter model writes block *drafts* (`BlockDraft`, one flat shape so constrained decoding
stays simple). The server turns each into the block the client draws (`materialize`): a chart or
table gets its data from the named dataset, a KPI its value from a dataset cell. The model
chooses what to show and how; it never writes a figure into a chart, a table or a KPI.
"""

from __future__ import annotations

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
        chart_type = draft.chart_type if draft.chart_type in CHART_TYPES else "bar"
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
        return {**base, "spec": spec, "unit": ds.unit, **_source(ds)}
    raise BlockError(f"không biết block {t}")
