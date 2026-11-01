"""Phễu chuyển đổi (skill category `custom`): rates between ordered stages.

Given stage counts in order (awareness → … → conversion), each step's conversion is
count[i] / count[i-1]. Refuses non-positive or non-monotonic increases that would make a
conversion > 100% unless `allow_increase` is set (rare event funnels). Pure arithmetic.
"""

from __future__ import annotations

import decimal
import itertools
from dataclasses import dataclass
from typing import Any

from services.worker_agent.answer.narrate import vn_number

D = decimal.Decimal


@dataclass(frozen=True)
class Stage:
    name: str
    count: decimal.Decimal


@dataclass(frozen=True)
class Step:
    from_stage: str
    to_stage: str
    rate: decimal.Decimal  # 0-1
    from_count: decimal.Decimal
    to_count: decimal.Decimal


class BadFunnel(ValueError):
    """Counts that cannot form a conversion funnel."""


def conversions(stages: list[Stage], *, allow_increase: bool = False) -> list[Step]:
    if len(stages) < 2:
        raise BadFunnel("Cần ít nhất hai giai đoạn.")
    for s in stages:
        if s.count < 0:
            raise BadFunnel(f"Số lượng âm ở “{s.name}”.")
    steps: list[Step] = []
    for prev, cur in itertools.pairwise(stages):
        if prev.count == 0:
            raise BadFunnel(f"Mẫu số bằng 0 ở “{prev.name}”.")
        if cur.count > prev.count and not allow_increase:
            raise BadFunnel(
                f"“{cur.name}” ({cur.count}) lớn hơn “{prev.name}” ({prev.count}); "
                "phễu chuyển đổi yêu cầu số giảm (hoặc bằng) theo từng bước."
            )
        steps.append(
            Step(
                from_stage=prev.name,
                to_stage=cur.name,
                rate=cur.count / prev.count,
                from_count=prev.count,
                to_count=cur.count,
            )
        )
    return steps


def result_of(
    stages: list[Stage],
    *,
    allow_increase: bool = False,
) -> dict[str, Any]:
    steps = conversions(stages, allow_increase=allow_increase)
    overall = stages[-1].count / stages[0].count if stages[0].count else D(0)
    step_bits = "; ".join(
        f"{s.from_stage} → {s.to_stage}: {vn_number(s.rate * 100, 1)}%" for s in steps
    )
    summary = (
        f"Phễu {stages[0].name} → {stages[-1].name}: chuyển đổi tổng "
        f"{vn_number(overall * 100, 1)}%. Từng bước: {step_bits}."
    )
    return {
        "summary": summary,
        "method": "Tỷ lệ chuyển đổi = số giai đoạn sau / số bước trước; không suy nguyên nhân.",
        "confidence": "Chính xác theo phép chia trên số đã cho",
        "funnel": {
            "overall_rate": format(overall, "f"),
            "stages": [{"name": s.name, "count": format(s.count, "f")} for s in stages],
            "steps": [
                {
                    "from": s.from_stage,
                    "to": s.to_stage,
                    "rate": format(s.rate, "f"),
                    "from_count": format(s.from_count, "f"),
                    "to_count": format(s.to_count, "f"),
                }
                for s in steps
            ],
        },
    }
