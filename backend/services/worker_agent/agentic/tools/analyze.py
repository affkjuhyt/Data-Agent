"""Computation over datasets already fetched: growth, shares, ranking, correlation, pivot,
anomaly flags, anything pandas does. The model writes the code; the sandbox runs it."""

from __future__ import annotations

from pydantic import BaseModel, Field

from services.worker_agent.agentic import sandbox
from services.worker_agent.agentic.tools.base import Tool, ToolContext, ToolResult


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
        try:
            done = await sandbox.run(args.code, frames)
        except sandbox.CodeRejected as exc:
            return ToolResult(False, f"code bị từ chối: {exc}", detail={"error": str(exc)})
        if not done.ok:
            return ToolResult(
                False,
                f"code lỗi: {done.error}" + (f"\nstdout: {done.stdout}" if done.stdout else ""),
                detail={"error": done.error},
            )
        sources = [ctx.ws.datasets[i] for i in inputs]
        units = {s.unit for s in sources if s.unit}
        ds = ctx.ws.add(
            title=args.title,
            columns=done.columns,
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
