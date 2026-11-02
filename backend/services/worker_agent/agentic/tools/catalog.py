"""Tools over the Data Space's semantic layer: what metrics, cuts and values exist. They read
metadata only, and only what the asker may see (invariant 13): the same functions the analyst
pass uses (understand/analyst.py)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from services.worker_agent.agentic.tools.base import Tool, ToolContext, ToolResult, cut
from services.worker_agent.understand import analyst
from services.worker_agent.understand.text import norm


class SearchCatalogArgs(BaseModel):
    text: str = Field(description="cụm từ chỉ đại lượng hoặc chiều cần tìm")


class SearchCatalog(Tool):
    name = "search_catalog"
    title = "Tìm trong danh mục"
    description = (
        "tìm chỉ số (metric) và chiều (dimension) của Data Space theo tên, mã, từ đồng nghĩa; "
        "dùng trước khi truy vấn để biết mã"
    )
    Args = SearchCatalogArgs

    async def run(self, ctx: ToolContext, args: SearchCatalogArgs) -> ToolResult:  # type: ignore[override]
        metrics = analyst.search_metrics(ctx.layer, args.text)
        words = set(norm(args.text).split())
        dims = [
            {"dimension": d.code, "name": d.name, "time": d.is_time}
            for d in ctx.layer.vocabulary.dimensions
            if words & (set(norm(d.name).split()) | set(norm(d.code.replace("_", " ")).split()))
        ][:10]
        if not metrics:
            # Nothing shares a word: show the whole visible catalogue, it is small enough to read.
            metrics = [
                {"metric": code, "name": m.name, "certified": m.certified}
                for code, m in list(analyst._visible(ctx.layer).items())[:30]
            ]
        found = {"metrics": metrics, "dimensions": dims}
        return ToolResult(
            True,
            cut(found),
            detail={
                "metrics": [m["name"] for m in metrics][:8],
                "dimensions": [d["name"] for d in dims],
            },
        )


class DescribeMetricArgs(BaseModel):
    metric: str = Field(description="mã chỉ số")


class DescribeMetric(Tool):
    name = "describe_metric"
    title = "Xem định nghĩa chỉ số"
    description = (
        "đơn vị, cách tính theo kỳ, chiều thời gian và các chiều được phép chia của một chỉ số"
    )
    Args = DescribeMetricArgs

    async def run(self, ctx: ToolContext, args: DescribeMetricArgs) -> ToolResult:  # type: ignore[override]
        found = analyst.describe_metric(ctx.layer, args.metric)
        if isinstance(found, str):
            return ToolResult(False, found)
        return ToolResult(True, cut(found), detail={"metric": found.get("name")})


class SearchValuesArgs(BaseModel):
    text: str = Field(description="giá trị cần tìm, ví dụ tên vùng, sản phẩm")
    dimension: str | None = Field(default=None, description="mã chiều, hoặc null")


class SearchValues(Tool):
    name = "search_values"
    title = "Tìm giá trị"
    description = "tìm mã giá trị thật của một chiều (ví dụ 'Mỹ' → United States) để lọc cho đúng"
    Args = SearchValuesArgs

    async def run(self, ctx: ToolContext, args: SearchValuesArgs) -> ToolResult:  # type: ignore[override]
        found = analyst.search_values(ctx.layer, args.text, args.dimension)
        return ToolResult(
            bool(found),
            cut(found) if found else f"không thấy giá trị nào giống '{args.text}'",
            detail={"found": len(found)},
        )
