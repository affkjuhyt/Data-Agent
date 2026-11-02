"""The next questions an answer offers (the chat upgrade, "câu hỏi gợi ý"): built from the answered
metric and the cuts its owner allows, and kept only when they run.

After "Doanh thu năm 2013" the answer offers "Doanh thu năm 2013 so với kỳ trước", "Doanh thu theo
tháng năm 2013", "Doanh thu theo khu vực năm 2013", "Top 5 sản phẩm có doanh thu cao nhất năm
2013". Each one is read back exactly as a click would send it, through the readers before the rule
pass, the rule pass and the compiler, and is dropped unless it reads as the metric, cut, period
and grain it was written for and compiles. An offer that ends in "không hiểu" or a refusal would
teach the person the chat does not work.

Written in the organisation's language (libs/language.use_locale): Vietnamese, else English.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from libs import language
from services.worker_agent.semantic import compiler as c
from services.worker_agent.semantic.loader import SpaceLayer
from services.worker_agent.understand.analysis import find_analysis
from services.worker_agent.understand.comparison import find_comparison
from services.worker_agent.understand.grain import find_grain
from services.worker_agent.understand.resolver import Understanding, understand
from services.worker_agent.understand.text import FoundPeriod, find_period

MAX_FOLLOW_UPS = 4
MAX_DRILLS = 2


@dataclass(frozen=True)
class _Offer:
    text: str
    group_by: tuple[str, ...]
    grain: str | None = None
    compare: bool = False
    top_n: int | None = None


def _phrases(metric: str, period: str, dims: list[tuple[str, str]], timed: bool) -> list[_Offer]:
    english = language.current_language() != "vi"
    lowered = metric[:1].lower() + metric[1:]
    when = f" {period}" if period else ""
    out: list[_Offer] = []
    if period:
        out.append(
            _Offer(f"{metric}{when} vs previous period", (), compare=True)
            if english
            else _Offer(f"{metric}{when} so với kỳ trước", (), compare=True)
        )
    if not timed:
        out.append(
            _Offer(f"Monthly {lowered}{when}", (), grain="month")
            if english
            else _Offer(f"{metric} theo tháng{when}", (), grain="month")
        )
    for code, name in dims[:MAX_DRILLS]:
        out.append(
            _Offer(f"{metric} by {name.lower()}{when}", (code,))
            if english
            else _Offer(f"{metric} theo {name.lower()}{when}", (code,))
        )
    if dims:
        code, name = dims[0]
        out.append(
            _Offer(f"Top 5 {name.lower()} by {lowered}{when}", (code,), top_n=5)
            if english
            else _Offer(f"Top 5 {name.lower()} có {lowered} cao nhất{when}", (code,), top_n=5)
        )
    return out


def _reads_back(
    offer: _Offer,
    layer: SpaceLayer,
    metric_id: object,
    period: FoundPeriod | None,
    today: datetime.date,
) -> bool:
    asked = offer.text
    comparison = find_comparison(asked)
    if comparison is not None:
        asked = comparison.rest
    grain = find_grain(asked)
    if grain is not None:
        asked = grain.rest
    if find_analysis(asked) is not None:
        return False
    u: Understanding = understand(asked, layer.vocabulary, today=today)
    if u.decision != "proceed" or u.metric is None or u.metric.metric_id != metric_id:
        return False
    if (comparison is not None) != offer.compare or (grain.grain if grain else None) != offer.grain:
        return False
    if not set(offer.group_by) <= set(u.group_by) or u.top_n != offer.top_n:
        return False
    want = (period.start, period.end) if period else None
    read = (u.period.start, u.period.end) if u.period else None
    if read != want:
        return False
    version = layer.versions[u.metric.metric_id]
    group_by = list(u.group_by)
    if offer.grain is not None:
        time = c.time_dimension_of(version, layer.layer)
        if time is None:
            return False
        group_by.append(time.code)
    try:
        c.compile_metric(
            c.Request(
                version,
                group_by=group_by,
                filters=u.filters,
                period=c.Period(u.period.start, u.period.end) if u.period else None,
                grain=offer.grain,
                top_n=u.top_n,
                top_desc=u.top_desc,
            ),
            layer.layer,
            dialect="postgres",
        )
    except c.CompileError:
        return False
    return True


def follow_ups(
    u: Understanding,
    layer: SpaceLayer,
    *,
    today: datetime.date,
    grain: str | None = None,
    compared: bool = False,
) -> list[str]:
    """Up to MAX_FOLLOW_UPS next questions for an answer to `u`, each one read back and compiled."""
    if u.metric is None or u.metric.metric_id not in layer.versions:
        return []
    version = layer.versions[u.metric.metric_id]
    period = u.period
    # A period is offered again only when its label reads back as the same days ("tháng 9/2026",
    # "năm 2013"); a week's label does not, so its offers name no period.
    if period is not None:
        again = find_period(period.label, today, layer.vocabulary.calendar)
        if again is None or (again.start, again.end) != (period.start, period.end):
            period = None
    times = {d.code for d in layer.vocabulary.dimensions if d.is_time}
    dims = [
        (d.code, d.name)
        for d in layer.vocabulary.dimensions
        if d.dimension_id in version.allowed_dimension_ids
        and not d.is_time
        and d.code not in u.group_by
    ]
    timed = grain is not None or any(code in times for code in u.group_by)
    out: list[str] = []
    for offer in _phrases(u.metric.name, period.label if period else "", dims, timed):
        if compared and offer.compare:
            continue
        if _reads_back(offer, layer, u.metric.metric_id, period, today):
            out.append(offer.text)
        if len(out) == MAX_FOLLOW_UPS:
            break
    return out
