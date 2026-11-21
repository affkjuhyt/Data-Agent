"""What a question asks, read by rule from its words before any plan (invariant 17, an output
contract before execution): the metric, the period, a comparison, a ranking, the cuts and the
members it names. It is read once, by the same rules query_metric reads each step's arguments
with, and used three times: the planner, executor, reviewer and reporter are shown it as fixed
facts; a step reading another metric beside the one asked does not run; and the answer is written
from the readings it asks for.

So the planner is held to one reading of the question whatever its size. On 19/11 the prompt still
told the planner to "add a comparison and the contributors" while the rules dropped what the
question did not ask: the 14B planner, which follows a prompt more closely than the 4B one, read
Giá trị tồn kho beside "Tồn kho cuối tháng 12/2013" and the answer named the extra reading instead
of the figure asked."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from services.worker_agent.agentic.tools.query import (
    asked_top,
    asks_breakdown,
    asks_change,
    asks_ranking,
    calendar_cut,
    metric_of_member,
    named_cuts,
    named_members,
    two_periods,
    two_years,
)
from services.worker_agent.understand import analyst
from services.worker_agent.understand.comparison import find_comparison
from services.worker_agent.understand.grain import GRAIN_LABELS, find_grain
from services.worker_agent.understand.packs import search
from services.worker_agent.understand.packs.base import FoundPeriod
from services.worker_agent.understand.resolver import names_metric
from services.worker_agent.understand.text import norm

_COMPARED = {
    "previous_period": "với kỳ liền trước",
    "same_period_last_year": "với cùng kỳ năm trước",
}


@dataclass(frozen=True)
class Contract:
    # The one metric the question calls by its name, when it calls exactly one the asker may see.
    metric: str | None
    # Every metric the asker may see that the question calls by its name.
    named: tuple[str, ...]
    period: FoundPeriod | None
    # A fiscal period the space models as a cut ("năm tài chính 2012"): the cut's name, the value.
    fiscal: tuple[str, str] | None
    # Two periods set side by side ("năm 2012 và 2013", "tháng 8/2013 với tháng 3/2013").
    sides: tuple[str, str] | None
    # A comparison the question names with its base ("so với cùng kỳ năm trước").
    compare_to: str | None
    # A rise or a fall asked ("giảm nhiều nhất"): the change between two periods.
    change: bool
    # How many members a ranking asks, and whether the highest; or a ranking with no count.
    top: tuple[int, bool] | None
    ranked: bool
    # The cuts the question names, as (code, name), and whether it asks a breakdown at all.
    cuts: tuple[tuple[str, str], ...]
    breakdown: bool
    grain: str | None
    # The members it names whole, as (cut code, member code, "label (cut)").
    members: tuple[tuple[str, str, str], ...]
    why: bool

    @property
    def compared(self) -> bool:
        return bool(self.compare_to or self.sides or self.change)

    def answerable(self) -> bool:
        """The question says which metric and which period: the data can answer it as asked, so
        nothing is left to ask the person."""
        return self.metric is not None and bool(self.period or self.fiscal or self.sides)

    def render(self, names: dict[str, str]) -> str:
        """The contract as the models read it, in the asker's language."""
        if self.metric is not None:
            metric = f"{self.metric} ({names.get(self.metric, self.metric)})"
        elif self.named:
            metric = ", ".join(f"{c} ({names.get(c, c)})" for c in self.named)
        else:
            metric = "câu hỏi không gọi tên chỉ số nào; chọn theo danh mục"
        if self.sides:
            period = f"{self.sides[0]} và {self.sides[1]}, đặt cạnh nhau"
        elif self.fiscal:
            period = (
                f"{self.fiscal[0]} {self.fiscal[1]} (một chiều dữ liệu, không phải năm dương lịch)"
            )
        elif self.period:
            period = (
                f'{self.period.label} = period "{self.period.start.isoformat()}/'
                f'{self.period.end.isoformat()}" của query_metric; là kỳ, không phải giá trị để'
                " search_values"
            )
        else:
            period = "câu hỏi không nêu"
        if self.compare_to:
            compared = _COMPARED.get(self.compare_to, self.compare_to)
        elif self.sides:
            compared = "hai kỳ trên"
        elif self.change:
            compared = "tăng / giảm giữa hai kỳ"
        else:
            compared = "không"
        if self.top:
            ranked = f"{self.top[0]} {'cao' if self.top[1] else 'thấp'} nhất"
        else:
            ranked = "có" if self.ranked else "không"
        # Each cut and member by the code query_metric takes, as the period is: shown "Nhóm sản
        # phẩm" alone, the 4B planner cut by tieu_nhom (Tiểu nhóm sản phẩm) (21/11).
        cut = ", ".join(f'{name} (group_by "{code}")' for code, name in self.cuts) or (
            "có, theo chiều câu hỏi nêu" if self.breakdown else "không, một con số"
        )
        if self.grain:
            cut += f"; theo {GRAIN_LABELS.get(self.grain, self.grain)}"
        lines = [
            f"- chỉ số: {metric}",
            f"- kỳ: {period}",
            f"- so sánh: {compared}",
            f"- xếp hạng: {ranked}",
            f"- chia theo: {cut}",
        ]
        if self.members:
            filters: dict[str, list[str]] = {}
            for dimension, member, _ in self.members:
                filters.setdefault(dimension, []).append(member)
            shown = ", ".join(label for _, _, label in self.members)
            lines.append(f"- lọc: {shown} = filters {json.dumps(filters, ensure_ascii=False)}")
        if self.why:
            lines.append("- hỏi vì sao: có")
        return "\n".join(lines)


def _written(question: str, code: str, vocab: Any) -> bool:
    """Whether the question writes a cut's name with its own marks, or one of its synonyms: names
    are matched folded, and "tính" of "tính theo số lượng" folds to the "tỉnh" of "Bang / tỉnh"
    (19/11). The planner is shown only the cuts the question writes."""

    def marked(text: str) -> str:
        return " ".join(
            re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFC", text).casefold()).split()
        )

    asked = f" {marked(question)} "
    dim = next(d for d in vocab.dimensions if d.code == code)
    if any(f" {name} " in asked for n in [dim.name, *dim.name.split("/")] if (name := marked(n))):
        return True
    folded = f" {norm(question)} "
    return any(
        f" {phrase} " in folded
        for phrase, target in vocab.synonyms.items()
        if target[0] == "dimension" and str(target[1]) == str(dim.dimension_id)
    )


def read(question: str, layer: Any, period: FoundPeriod | None) -> Contract:
    vocab = layer.vocabulary
    visible = analyst._visible(layer)
    every = {d.dimension_id for d in vocab.dimensions}
    members = named_members(question, vocab, every)
    member_cuts = {d.dimension_id for d in vocab.dimensions if d.code in members}

    def cuts_of(code: str) -> set[Any]:
        entry = visible.get(code)
        return set(layer.versions[entry.metric_id].allowed_dimension_ids) if entry else set()

    # Every metric of the vocabulary: one the asker may not see still makes the question name two,
    # but is never shown nor read. Its whole name first; half of it beside a member only when no
    # whole name is there ("Doanh thu Internet nhóm hàng Bikes" names Doanh thu Internet, not also
    # Doanh thu đại lý, which cuts by Nhóm sản phẩm too).
    named = [m for m in vocab.metrics if names_metric(question, m, vocab)] or [
        m
        for m in vocab.metrics
        if names_metric(question, m, vocab, member_named=bool(member_cuts & cuts_of(m.code)))
    ]
    if len(named) > 1 and member_cuts:
        # Several metrics named, and a member one alone cuts by: that one. "Doanh số Trade Sales
        # theo ngân sách" names Doanh thu and Số tài chính ngân sách, and Trade Sales is an
        # account only the second has; shown both, the 4B planner read Doanh thu first (21/11).
        cutting = [m for m in named if member_cuts & cuts_of(m.code)]
        if len(cutting) == 1:
            named = cutting
    seen = [m.code for m in named if m.code in visible]
    metric = seen[0] if len(named) == 1 and seen else None
    foreign = member_cuts - cuts_of(metric) if metric else member_cuts
    if foreign and (metric or not named):
        # A member the metric does not cut by ("Doanh số Trade Sales thực tế": an account, and
        # Doanh thu has none), or no metric named: the metric that cuts by it and that the
        # question names more of than any other, as query_metric reads it.
        found = {metric_of_member(question, layer, d) for d in foreign} - {None}
        metric = found.pop() if len(found) == 1 else None
        seen = [metric] if metric else []
    top = asked_top(question)
    text = norm(question)
    fiscal = calendar_cut(question, vocab, every)
    by_code = {d.code: d.name for d in vocab.dimensions}
    labels = {(v.dimension_code, v.value_code): v.label for v in vocab.values}
    pair = two_periods(question)
    years = two_years(question)
    compared = find_comparison(question)
    grain = find_grain(question)
    return Contract(
        metric=metric,
        named=tuple(seen),
        period=period,
        fiscal=next(((by_code[d], v[0]) for d, v in fiscal.items()), None),
        sides=(str(years[0]), str(years[1]))
        if years
        else (pair[0][1], pair[1][1])
        if pair
        else None,
        compare_to=compared.compare_to if compared else None,
        change=asks_change(text),
        top=top,
        ranked=asks_ranking(text),
        cuts=tuple(
            sorted(
                (
                    (c, by_code[c])
                    for c in named_cuts(question, vocab, every)
                    if _written(question, c, vocab)
                ),
                key=lambda cut: cut[1],
            )
        ),
        breakdown=asks_breakdown(question, vocab, top),
        grain=grain.grain if grain else None,
        members=tuple(
            (d, c, f"{labels.get((d, c), c)} ({by_code[d]})")
            for d, codes in members.items()
            for c in codes
        ),
        why=search(text, lambda pack: pack.why) is not None,
    )
