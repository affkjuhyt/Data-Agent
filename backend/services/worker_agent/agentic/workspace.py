"""The turn's blackboard: every dataset a tool produced, with where it came from and how far it
can be trusted. Blocks name datasets; the server reads figures from here, never from the model."""

from __future__ import annotations

import datetime
import decimal
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

Trust = Literal["certified", "exploratory", "derived", "document"]

# The weakest first: a dataset derived from several inputs is as trusted as its weakest one.
TRUST_ORDER: tuple[Trust, ...] = ("exploratory", "derived", "document", "certified")

TRUST_LABEL = {
    "certified": "Theo chỉ số chuẩn",
    "exploratory": "SQL thăm dò, chưa qua chỉ số chuẩn",
    "derived": "Tính thêm từ dữ liệu đã truy vấn",
    "document": "Theo tài liệu",
}

# What the model is shown of a dataset: enough rows to reason about its shape, not to read it all.
PREVIEW_ROWS = 12
# What a table block carries at most; the rest is in the query run.
MAX_BLOCK_ROWS = 200


def plain(value: Any) -> Any:
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def number(value: Any) -> float | None:
    """A cell as a number when it is one (amounts arrive as decimal strings, A1.1)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


@dataclass
class Dataset:
    id: str
    title: str
    columns: list[str]
    rows: list[list[Any]]
    trust: Trust
    # Where the rows came from: query runs, metric versions, the step and tool that made it.
    query_run_ids: list[str] = field(default_factory=list)
    metric_version_ids: list[str] = field(default_factory=list)
    step_id: str = ""
    tool: str = ""
    unit: str | None = None
    truncated: bool = False
    # For derived datasets: the code that made it, shown on the answer as a code block.
    code: str | None = None
    # For a certified metric's dataset: the reading it was queried with (metric code, group_by,
    # filters), so the turn can check the question names that metric (engine._names_what_was_asked).
    reading: dict[str, Any] | None = None
    # For SQL the model wrote (explore_sql): how many of its ways of writing gave this result, out
    # of how many, and whether that agreement is clear enough to use without checking (sqlgen vote).
    consensus: tuple[int, int] | None = None
    agreement_clear: bool = True

    def records(self, limit: int | None = None) -> list[dict[str, Any]]:
        rows = self.rows if limit is None else self.rows[:limit]
        return [{c: plain(v) for c, v in zip(self.columns, row, strict=False)} for row in rows]

    def preview(self) -> str:
        """The dataset as the model sees it: shape, columns, a few rows, simple stats."""
        head: dict[str, Any] = {
            "dataset": self.id,
            "title": self.title,
            "trust": self.trust,
            "unit": self.unit,
            "row_count": len(self.rows),
            "truncated": self.truncated,
            "columns": self.columns,
            "rows": self.records(PREVIEW_ROWS),
        }
        stats: dict[str, Any] = {}
        for i, col in enumerate(self.columns):
            nums = [n for n in (number(r[i]) for r in self.rows if i < len(r)) if n is not None]
            if nums and len(nums) == len(self.rows) and len(self.rows) > 1:
                stats[col] = {
                    "min": min(nums),
                    "max": max(nums),
                    "sum": round(sum(nums), 4),
                }
        if stats:
            head["stats"] = stats
        text = json.dumps(head, ensure_ascii=False, default=str)
        return text if len(text) <= 4000 else text[:4000] + "…"


@dataclass
class Workspace:
    datasets: dict[str, Dataset] = field(default_factory=dict)
    citations: list[dict[str, Any]] = field(default_factory=list)
    _seq: int = 0

    def add(self, **kwargs: Any) -> Dataset:
        self._seq += 1
        ds = Dataset(id=f"d{self._seq}", **kwargs)
        self.datasets[ds.id] = ds
        return ds

    def get(self, dataset_id: str) -> Dataset | None:
        return self.datasets.get(dataset_id)

    def summary(self) -> str:
        if not self.datasets:
            return "(chưa có dataset nào)"
        return "\n".join(ds.preview() for ds in self.datasets.values())

    def weakest(self, ids: list[str]) -> Trust:
        trusts = [self.datasets[i].trust for i in ids if i in self.datasets]
        if not trusts:
            return "derived"
        return min(trusts, key=TRUST_ORDER.index)
