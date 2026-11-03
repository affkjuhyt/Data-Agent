"""data_coverage: where a metric has data, before a query for a period it may never reach (AGC-10).

Without it the agent learned that a quarter is empty only by querying it, then had nothing to try
but the same quarter again, and handed the question over. The probe is semantic/coverage.py."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from services.worker_agent.agentic.tools.base import Tool, ToolContext, ToolResult
from services.worker_agent.agentic.tools.query import QUERYABLE, _secrets, _source_for
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic import coverage
from services.worker_agent.understand import analyst


class DataCoverageArgs(BaseModel):
    metric: str = Field(description="mã chỉ số")
    grain: Literal["month", "quarter", "year"] = Field(
        default="month", description="độ chi tiết của các kỳ cần xem"
    )
    filters: dict[str, list[str]] = Field(
        default_factory=dict, description="{mã chiều: [mã giá trị]}, như query_metric, có thể rỗng"
    )


class DataCoverage(Tool):
    name = "data_coverage"
    title = "Xem kỳ có dữ liệu"
    description = (
        "chỉ số có dữ liệu từ kỳ nào đến kỳ nào, kỳ cuối có bị cắt dở không, kỳ được hỏi có nằm "
        "trong đó không; dùng trước query_metric khi kỳ được hỏi có thể chưa có dữ liệu (tương lai, "
        "rất cũ) hoặc khi cần biết kỳ đầy đủ gần nhất"
    )
    Args = DataCoverageArgs

    async def run(self, ctx: ToolContext, args: DataCoverageArgs) -> ToolResult:  # type: ignore[override]
        entry = analyst._visible(ctx.layer).get(args.metric)
        if entry is None:
            return ToolResult(
                False,
                f"không có chỉ số {args.metric} (hoặc bạn không được xem); dùng search_catalog",
            )
        version = ctx.layer.versions[entry.metric_id]
        found = await _coverage(ctx, version, args.grain, args.filters)
        if found is None:
            # No time dimension, a source not queryable in place, or one that did not answer.
            return ToolResult(
                False,
                f"không dò được các kỳ có dữ liệu của {entry.name}",
                failure="unavailable",
            )
        asked = c.Period(ctx.period.start, ctx.period.end) if ctx.period is not None else None
        return ToolResult(
            True,
            coverage.describe(found, entry.name, asked),
            detail={
                "metric": entry.name,
                "first": coverage.label(found.first, found.grain) if found.first else None,
                "last": coverage.label(found.last, found.grain) if found.last else None,
                "last_complete": coverage.label(found.last_complete, found.grain)
                if found.last_complete
                else None,
                "partial_last": found.partial_last,
                "query_run_id": str(found.query_run_id) if found.query_run_id else None,
            },
        )


async def _coverage(
    ctx: ToolContext,
    version: c.MetricVersion,
    grain: str,
    filters: dict[str, list[str]] | None = None,
) -> coverage.Coverage | None:
    try:
        first = c.compile_metric(c.Request(version), ctx.layer.layer, dialect="postgres")
    except c.CompileError:
        return None
    source = await _source_for(ctx, first.dataset_ids[0])
    if source is None or source.mode != "federated" or source.status not in QUERYABLE:
        return None
    return await coverage.probe(
        ctx.layer,
        version,
        source,
        org_id=ctx.org_id,
        space_id=ctx.space_id,
        user_id=ctx.user_id,
        caller_id=ctx.question_id,
        secrets=_secrets(),
        as_of=ctx.as_of,
        grain=grain,
        filters=filters,
    )


async def for_planning(ctx: ToolContext, codes: list[str]) -> str:
    """The coverage of the metrics the question most likely reads, for the planner's situation,
    when the question names a period: one line each, nothing when a probe fails. A metric whose
    data never reaches that period gets its move in ctx.period_moves, which query_metric holds."""
    if ctx.period is None:
        return ""
    asked = c.Period(ctx.period.start, ctx.period.end)
    # By month whatever the question's grain: a quarter holding two whole months and a cut-off
    # third does not look cut off, the third month does.
    grain = "month"
    visible = analyst._visible(ctx.layer)
    lines = []
    for code in codes:
        entry = visible.get(code)
        if entry is None:
            continue
        found = await _coverage(ctx, ctx.layer.versions[entry.metric_id], grain)
        if found is None:
            continue
        lines.append(f"{code}: {coverage.describe(found, entry.name, asked)}")
        if not found.covers(asked.start, asked.end):
            suggested = coverage.suggest(found, asked)
            if suggested is not None:
                ctx.period_moves[code] = (asked, suggested[0])
    return "\n".join(lines)
