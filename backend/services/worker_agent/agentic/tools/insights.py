"""What a dataset of one measure along one cut says, worked out by arithmetic (AL-02).

The governed path reads a result for the analyst (answer/insights.py): along time the last change,
its percent, the change over the whole span, the average change per period and how many periods
in a row it went the same way; across members the highest and lowest, the gap and how many times
over, what the largest and the three largest hold of the total (an additive metric only), whether
the members break after a few or fall off evenly, and which stands apart by the median absolute
deviation. The agentic path had none of it: the model wrote pandas for a slope or a streak, a
number no one could recompute.

This tool runs that same code on a workspace dataset: no model, no sandbox. Every derived figure
keeps its expression, written over the input dataset's members (`(Doanh thu[2013-12] -
Doanh thu[2013-11]) / Doanh thu[2013-11] * 100`), so it can be recomputed from the input; the
figures become a dataset of their own, the sentences the observation the model writes from. Over
time a period that stands apart is found with the anomaly skill's rule (|robust z| > 3.5, five
periods at least): an observation, not a cause.
"""

from __future__ import annotations

import decimal
import re
from typing import Any

from pydantic import BaseModel, Field

from services.worker_agent.agentic.blocks import time_order
from services.worker_agent.agentic.tools.base import Tool, ToolContext, ToolResult
from services.worker_agent.agentic.workspace import Dataset, number, plain
from services.worker_agent.answer import insights as reading
from services.worker_agent.answer.narrate import Figure, shown_value
from services.worker_agent.skills import anomaly

DECIMALS = 2
_KEY = re.compile(r"\bF(\d+)\b")


class InsightsArgs(BaseModel):
    dataset: str = Field(description="mã dataset đã có (d1, d2…), một thước đo theo một chiều")
    measure: str | None = Field(default=None, description="cột thước đo; mặc định cột số cuối")
    by: str | None = Field(default=None, description="cột chiều (kỳ hoặc nhóm); mặc định cột đầu")


def _columns(ds: Dataset, measure: str | None, by: str | None) -> tuple[str, str] | str:
    """The measure and the cut, or why the dataset is not one measure along one cut."""
    numeric = [
        c
        for i, c in enumerate(ds.columns)
        if ds.rows and all(number(r[i]) is not None for r in ds.rows if r[i] is not None)
    ]
    measure = measure or (numeric[-1] if numeric else None)
    if measure not in ds.columns or measure not in numeric:
        return f"{ds.id} không có cột số {measure}; các cột số: {numeric}"
    by = by or next((c for c in ds.columns if c != measure and c not in numeric), None)
    by = by or next((c for c in ds.columns if c != measure), None)
    if by not in ds.columns or by == measure:
        return f"{ds.id} không có cột chiều {by}"
    # One cut: another column that varies and is no measure is a second cut (region by month).
    others = [c for c in ds.columns if c not in (measure, by) and c not in numeric]
    for c in others:
        i = ds.columns.index(c)
        if len({str(plain(r[i])) for r in ds.rows}) > 1:
            return f"{ds.id} chia theo hơn một chiều ({by}, {c}): lọc còn một chiều trước"
    return measure, by


def _expression(expr: str, base: dict[str, str], measure: str) -> str:
    """F3 - F2 over the input's members: Doanh thu[2013-12] - Doanh thu[2013-11]."""
    return _KEY.sub(lambda m: f"{measure}[{base.get('F' + m.group(1), m.group(0))}]", expr)


class Insights(Tool):
    name = "insights"
    title = "Đọc nhận định từ số liệu"
    description = (
        "đọc một dataset một thước đo theo một chiều bằng phép tính tất định: theo thời gian thì thay"
        " đổi kỳ cuối, cả giai đoạn, độ dốc trung bình, chuỗi tăng/giảm, kỳ bất thường; theo nhóm thì"
        " cao nhất/thấp nhất, chênh lệch, gấp mấy lần, tỷ trọng nhóm đầu (chỉ số cộng được), nhóm"
        " tách biệt, nhóm bất thường. Dùng thay vì viết pandas cho xu hướng, chuỗi, tập trung,"
        " bất thường"
    )
    Args = InsightsArgs

    async def run(self, ctx: ToolContext, args: InsightsArgs) -> ToolResult:  # type: ignore[override]
        ds = ctx.ws.get(args.dataset)
        if ds is None:
            return ToolResult(False, f"không có dataset {args.dataset}", failure="arguments")
        found = _columns(ds, args.measure, args.by)
        if isinstance(found, str):
            return ToolResult(False, found, failure="arguments")
        measure, by = found
        mi, bi = ds.columns.index(measure), ds.columns.index(by)
        rows = [r for r in ds.rows if number(r[mi]) is not None and r[bi] is not None]
        if len(rows) < 2:
            return ToolResult(False, f"{ds.id} có dưới hai giá trị để đọc", failure="logic")
        members = [str(plain(r[bi])) for r in rows]
        keys = [time_order(m) for m in members]
        over_time = all(k is not None for k in keys)
        if over_time:
            # The reading orders a time cut by its members' text: ISO periods sort right, a
            # "05/2013" does not, so the rows are put in time order and named by their place.
            ordered = sorted(zip(keys, rows, members, strict=True), key=lambda p: p[0] or (0, 0, 0))
            rows = [r for _, r, _ in ordered]
            members = [m for _, _, m in ordered]
        figures = [
            Figure(
                key=f"F{i + 1}",
                label=member,
                value=decimal.Decimal(str(number(r[mi]))),
                unit=ds.unit,
                decimals=DECIMALS,
                row=i,
                column=measure,
                dimensions={by: f"{i:06d}" if over_time else member},
            )
            for i, (r, member) in enumerate(zip(rows, members, strict=True))
        ]
        additivity = (ds.reading or {}).get("additivity")
        metric_name = str((ds.reading or {}).get("metric_name") or measure)
        read = reading.insights_of(
            figures,
            group_by=[by],
            time_dimensions={by} if over_time else set(),
            metric_name=metric_name,
            unit=ds.unit,
            decimals=DECIMALS,
            # A share of the total only when the metric is known to add up: unknown is not.
            additive=additivity in ("full", "semi"),
        )
        base = {f.key: member for f, member in zip(figures, members, strict=True)}
        # The member names the reading wrote as positions (time order) are put back.
        sentences = [
            re.sub(r"\b(\d{6})\b", lambda m: members[int(m.group(1))], s) if over_time else s
            for s in read.sentences
        ]
        derived = list(read.figures)
        text = reading.written(sentences, figures + derived)
        if over_time and len(rows) >= anomaly.MIN_MEMBERS:
            points = [
                anomaly.Point(m, f.value)
                for m, f in zip(members, figures, strict=True)
                if f.value is not None
            ]
            apart = anomaly.find_outliers(points)
            if apart:
                text += (
                    " Kỳ lệch hẳn so với các kỳ khác (MAD, |z| > 3,5): "
                    + ", ".join(
                        f"{a.member} ({shown_value(figures[members.index(a.member)])})"
                        for a in apart
                    )
                    + ". Đây là quan sát thống kê, không phải nguyên nhân."
                )
        if not derived and not text.strip():
            return ToolResult(
                True,
                f"{ds.id}: không có nhận định nào đủ điều kiện (ít giá trị hoặc có giá trị bị ẩn)",
                detail={"dataset": ds.id, "figures": 0},
            )
        out = ctx.ws.add(
            title=f"Nhận định: {ds.title}",
            columns=["Nhận định", "Giá trị", "Biểu thức"],
            rows=[
                [
                    re.sub(r"\b(\d{6})\b", lambda m: members[int(m.group(1))], f.label)
                    if over_time
                    else f.label,
                    f.value,
                    _expression(f.expr or "", base, measure),
                ]
                for f in derived
            ],
            trust="derived" if ds.trust == "certified" else ds.trust,
            query_run_ids=list(ds.query_run_ids),
            metric_version_ids=list(ds.metric_version_ids),
            step_id=ctx.step_id,
            tool=self.name,
            unit=ds.unit,
            reading={
                "insights_of": ds.id,
                "measure": measure,
                "by": by,
                "metric_name": metric_name,
                "additivity": additivity,
            },
        )
        detail: dict[str, Any] = {
            "dataset": out.id,
            "input": ds.id,
            "figures": len(derived),
            "over_time": over_time,
        }
        return ToolResult(
            True, f"{out.preview()}\nNhận định: {text.strip()}", [out.id], detail=detail
        )
