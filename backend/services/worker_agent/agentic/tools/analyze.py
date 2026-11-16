"""Computation over datasets already fetched: growth, shares, ranking, correlation, pivot,
anomaly flags, anything pandas does. The model writes the code; the sandbox runs it."""

from __future__ import annotations

import os
import re

from pydantic import BaseModel, Field

from libs.code_sandbox import client, local
from services.worker_agent.agentic.tools.base import Tool, ToolContext, ToolResult
from services.worker_agent.agentic.workspace import Dataset


def mode() -> str:
    """off (the default), service (the python-sandbox pod at AGENT_SANDBOX_URL: the boundary
    model-written code needs) or local (a checked child of the worker, for a trusted install)."""
    chosen = os.environ.get("AGENT_PYTHON_SANDBOX", "off").strip().lower()
    # Service without its address is off, never local: a missing URL must not move the code into
    # the worker.
    if chosen == "service" and not os.environ.get("AGENT_SANDBOX_URL", "").strip():
        return "off"
    return chosen if chosen in ("service", "local") else "off"


_SUFFIXED = re.compile(r"^(.+)_(x|y)$")
# Which dataset is the left of a merge, which the right: "d1.merge(d2" or "merge(d1, d2".
_MERGE = re.compile(r"\b(d\d+)\s*\.\s*merge\(\s*(d\d+)\b|\bmerge\(\s*(d\d+)\s*,\s*(d\d+)\b")


def _side(ds: Dataset, other: Dataset) -> str | None:
    """What sets a dataset apart from the other it was merged with: its period, else its metric."""
    mine, theirs = ds.reading or {}, other.reading or {}
    for key in ("period", "metric_name"):
        if mine.get(key) and theirs.get(key) and mine[key] != theirs[key]:
            return str(mine[key])
    return None


def merged_names(
    columns: list[str], code: str, datasets: dict[str, Dataset]
) -> dict[str, str] | None:
    """New names for the "<name>_x"/"<name>_y" columns pandas gives a merge of two readings of one
    metric ("Doanh thu_x" → "Doanh thu 2012"), from what each side read; {} when there are none,
    None when the two sides cannot be told apart, for the model to name them itself."""
    pairs = {m.group(1) for c in columns if (m := _SUFFIXED.match(c))}
    pairs = {stem for stem in pairs if f"{stem}_x" in columns and f"{stem}_y" in columns}
    if not pairs:
        return {}
    found = _MERGE.search(code)
    if found is None:
        return None
    left, right = [g for g in found.groups() if g][:2]
    if left not in datasets or right not in datasets:
        return None
    names = (_side(datasets[left], datasets[right]), _side(datasets[right], datasets[left]))
    if not all(names):
        return None
    renamed: dict[str, str] = {}
    for stem in pairs:
        renamed[f"{stem}_x"] = f"{stem} {names[0]}"
        renamed[f"{stem}_y"] = f"{stem} {names[1]}"
    return renamed


class AnalyzeArgs(BaseModel):
    code: str = Field(
        description=(
            "code python/pandas; mỗi dataset là DataFrame tên theo mã (d1, d2…); "
            "gán kết quả vào biến result (DataFrame, Series, dict hoặc số)"
        )
    )
    inputs: list[str] = Field(default_factory=list, description="các mã dataset dùng tới")
    title: str = Field(default="Kết quả phân tích", description="tên ngắn của kết quả")


class Analyze(Tool):
    name = "analyze"
    title = "Phân tích bằng Python"
    description = (
        "tính thêm trên các dataset đã có bằng pandas: tăng trưởng, tỷ trọng, xếp hạng, chênh lệch, "
        "tương quan, pivot, phát hiện bất thường; không đọc nguồn dữ liệu mới"
    )
    Args = AnalyzeArgs

    async def run(self, ctx: ToolContext, args: AnalyzeArgs) -> ToolResult:  # type: ignore[override]
        inputs = [i for i in args.inputs if ctx.ws.get(i)] or list(ctx.ws.datasets)
        if not inputs:
            return ToolResult(False, "chưa có dataset nào để phân tích; truy vấn trước")
        frames = {
            i: {"columns": ctx.ws.datasets[i].columns, "rows": ctx.ws.datasets[i].rows}
            for i in inputs
        }
        chosen = mode()
        if chosen == "off":
            return ToolResult(
                False,
                "phân tích bằng Python đang tắt",
                detail={"error": "off"},
                failure="unavailable",
            )
        try:
            done = (
                await client.run(os.environ["AGENT_SANDBOX_URL"], args.code, frames)
                if chosen == "service"
                else await local.run(args.code, frames)
            )
        except local.CodeRejected as exc:
            return ToolResult(
                False,
                f"code bị từ chối: {exc}",
                detail={"error": str(exc)},
                failure="arguments",
            )
        if not done.ok:
            return ToolResult(
                False,
                f"code lỗi: {done.error}" + (f"\nstdout: {done.stdout}" if done.stdout else ""),
                detail={"error": done.error},
                failure="arguments",
            )
        renamed = merged_names(list(done.columns), args.code, ctx.ws.datasets)
        if renamed is None:
            # "Doanh thu_x"/"Doanh thu_y" reached the chart's legend (16/11): which is which is
            # the model's to say when the readings do not.
            return ToolResult(
                False,
                "kết quả có cột hậu tố _x/_y của pandas merge; đặt tên cột theo nội dung, "
                "ví dụ merge(..., suffixes=(' 2012', ' 2013')) hoặc rename",
                failure="arguments",
            )
        sources = [ctx.ws.datasets[i] for i in inputs]
        units = {s.unit for s in sources if s.unit}
        ds = ctx.ws.add(
            title=args.title,
            columns=[renamed.get(c, c) for c in done.columns],
            rows=done.rows,
            trust="derived",
            query_run_ids=[r for s in sources for r in s.query_run_ids],
            metric_version_ids=[m for s in sources for m in s.metric_version_ids],
            step_id=ctx.step_id,
            tool=self.name,
            unit=units.pop() if len(units) == 1 else None,
            truncated=done.truncated,
            code=args.code,
        )
        observation = ds.preview()
        if done.stdout.strip():
            observation += f"\nstdout: {done.stdout.strip()[:1500]}"
        return ToolResult(
            True,
            observation,
            [ds.id],
            detail={"dataset": ds.id, "inputs": inputs, "rows": len(ds.rows)},
        )
