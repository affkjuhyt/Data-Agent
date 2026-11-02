"""Why a metric moved, as RD-Agent runs research: hypothesis → experiment → feedback, recorded.

Each cut the metric allows is a hypothesis ("the change is concentrated in one member of this
dimension"); the experiment is the governed comparison of the two periods by that cut (the same
query_metric call the planner could make); the feedback is how concentrated the change is there
(the largest member's share of all the members' movement, the measure the decomposition skill
ranks cuts by). The cuts are ranked on it, and the best one's members become a dataset: this
period, the period compared, the change and its share of the total movement. A contribution is
not a cause, and the observation says so.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from services.worker_agent.agentic.tools.base import Tool, ToolContext, ToolResult, cut
from services.worker_agent.agentic.tools.query import QueryMetric, QueryMetricArgs
from services.worker_agent.agentic.workspace import number
from services.worker_agent.understand import analyst

MAX_HYPOTHESES = 5
MAX_MEMBERS = 8


class ExplainChangeArgs(BaseModel):
    metric: str = Field(description="mã chỉ số")
    period: str = Field(description="kỳ ISO đang xét, ví dụ '2013-Q4'")
    compare_to: Literal["previous_period", "same_period_last_year"] = "previous_period"
    dimensions: list[str] = Field(
        default_factory=list, description="các chiều cần thử; rỗng = mọi chiều được phép (≤5)"
    )


class ExplainChange(Tool):
    name = "explain_change"
    title = "Tìm nguyên nhân biến động"
    description = (
        "câu hỏi 'vì sao tăng/giảm': thử từng chiều được phép như một giả thuyết, so hai kỳ theo "
        "chiều đó, xếp hạng chiều theo mức tập trung của thay đổi; trả dataset đóng góp của chiều "
        "giải thích tốt nhất"
    )
    Args = ExplainChangeArgs

    async def run(self, ctx: ToolContext, args: ExplainChangeArgs) -> ToolResult:  # type: ignore[override]
        described = analyst.describe_metric(ctx.layer, args.metric)
        if isinstance(described, str):
            return ToolResult(False, described)
        allowed = [d["dimension"] for d in described["dimensions"]]
        hypotheses = [d for d in args.dimensions if d in allowed] or allowed
        hypotheses = hypotheses[:MAX_HYPOTHESES]
        if not hypotheses:
            return ToolResult(False, f"chỉ số {args.metric} không cho chia theo chiều nào")
        names = {d["dimension"]: d["name"] for d in described["dimensions"]}
        trials = []
        for dim in hypotheses:
            done = await QueryMetric().run(
                ctx,
                QueryMetricArgs(
                    metric=args.metric,
                    group_by=[dim],
                    period=args.period,
                    compare_to=args.compare_to,
                    title=f"{described['name']} theo {names[dim]}: so kỳ",
                ),
            )
            if not done.ok or not done.datasets:
                trials.append(
                    {
                        "dimension": names[dim],
                        "result": "không chạy được",
                        "why": done.observation[:200],
                    }
                )
                continue
            ds = ctx.ws.datasets[done.datasets[0]]
            # Columns: the cut, the value, the base value (compare.compile_comparison).
            members = []
            for row in ds.rows:
                now, then = number(row[1]), number(row[2])
                if now is None and then is None:
                    continue
                members.append((str(row[0]), now or 0.0, then or 0.0))
            moved = sum(abs(n - t) for _, n, t in members)
            if not members or moved == 0:
                trials.append({"dimension": names[dim], "result": "không đổi"})
                continue
            top = max(members, key=lambda m: abs(m[1] - m[2]))
            trials.append(
                {
                    "dimension": names[dim],
                    "code": dim,
                    "concentration": round(abs(top[1] - top[2]) / moved, 4),
                    "largest": top[0],
                    "members": members,
                    "dataset": ds.id,
                }
            )
        ranked = sorted(
            (t for t in trials if "concentration" in t), key=lambda t: -float(t["concentration"])
        )
        if not ranked:
            return ToolResult(
                False, "không chiều nào cho được so sánh hai kỳ", detail={"trials": trials}
            )
        best = ranked[0]
        rows = sorted(best["members"], key=lambda m: -abs(m[1] - m[2]))
        moved = sum(abs(n - t) for _, n, t in rows)
        total_change = sum(n - t for _, n, t in rows)
        shown = rows[:MAX_MEMBERS]
        result = ctx.ws.add(
            title=f"Đóng góp vào thay đổi theo {best['dimension']}",
            columns=[
                str(best["dimension"]),
                "Kỳ này",
                "Kỳ so sánh",
                "Thay đổi",
                "Tỷ trọng thay đổi (%)",
            ],
            rows=[
                [name, now, then, round(now - then, 4), round((now - then) / moved * 100, 2)]
                for name, now, then in shown
            ],
            trust=ctx.ws.datasets[str(best["dataset"])].trust,
            query_run_ids=[
                r for t in ranked for r in ctx.ws.datasets[str(t["dataset"])].query_run_ids
            ],
            metric_version_ids=ctx.ws.datasets[str(best["dataset"])].metric_version_ids,
            step_id=ctx.step_id,
            tool=self.name,
            unit=ctx.ws.datasets[str(best["dataset"])].unit,
            reading=ctx.ws.datasets[str(best["dataset"])].reading,
        )
        feedback = [
            {
                "dimension": t["dimension"],
                "concentration": t["concentration"],
                "largest": t["largest"],
            }
            for t in ranked
        ] + [{"dimension": t["dimension"], "result": t["result"]} for t in trials if "result" in t]
        observation = (
            f"Giả thuyết đã thử (xếp theo mức tập trung của thay đổi): {cut(feedback, 1200)}\n"
            f"Tổng thay đổi {round(total_change, 4)}; chiều giải thích tốt nhất: {best['dimension']}.\n"
            f"{result.preview()}\nĐây là đóng góp theo thành viên, không phải nguyên nhân."
        )
        return ToolResult(
            True,
            observation,
            [result.id],
            detail={"hypotheses": feedback, "dataset": result.id},
        )
