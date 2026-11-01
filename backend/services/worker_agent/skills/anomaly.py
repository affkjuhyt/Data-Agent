"""Phát hiện bất thường (skill category `quality`): MAD robust z-score over members.

Same rule answer/insights.py uses for "stands apart" (|robust z| > 3.5, ≥ 5 members).
Pure arithmetic over values the caller already queried; no model, no sandbox. Numbers in
the result are decimal strings so BR-171-style tracing can wrap them later.
"""

from __future__ import annotations

import decimal
import statistics
from dataclasses import dataclass
from typing import Any

from services.worker_agent.answer.narrate import vn_number

D = decimal.Decimal
OUTLIER_Z = D("3.5")
MAD_SCALE = D("0.6745")
MIN_MEMBERS = 5


@dataclass(frozen=True)
class Point:
    member: str
    value: decimal.Decimal


@dataclass(frozen=True)
class Finding:
    member: str
    value: decimal.Decimal
    robust_z: decimal.Decimal


class NotEnough(ValueError):
    """Fewer than MIN_MEMBERS finite values — MAD has nothing to say."""


def find_outliers(points: list[Point]) -> list[Finding]:
    """Members whose |robust z| exceeds OUTLIER_Z. Empty when MAD is zero (no spread)."""
    if len(points) < MIN_MEMBERS:
        raise NotEnough(f"Cần ít nhất {MIN_MEMBERS} giá trị, nhận {len(points)}.")
    values = [p.value for p in points]
    median = D(str(statistics.median(values)))
    deviations = [abs(v - median) for v in values]
    mad = D(str(statistics.median(deviations)))
    if mad == 0:
        return []
    found: list[Finding] = []
    for p in points:
        z = MAD_SCALE * (p.value - median) / mad
        if abs(z) > OUTLIER_Z:
            found.append(Finding(p.member, p.value, z))
    return sorted(found, key=lambda f: abs(f.robust_z), reverse=True)


def result_of(
    points: list[Point],
    *,
    metric_name: str,
    dimension_name: str,
    unit: str | None,
    decimals: int,
) -> dict[str, Any]:
    outliers = find_outliers(points)
    unit_s = f" {unit}" if unit and unit != "%" else (unit or "")
    if not outliers:
        summary = (
            f"{metric_name} theo {dimension_name.lower()}: không có nhóm nào lệch hẳn "
            f"(MAD, |z| > {OUTLIER_Z}) trên {len(points)} nhóm."
        )
    else:
        names = ", ".join(
            f"{f.member} ({vn_number(f.value, decimals)}{unit_s}, z={vn_number(f.robust_z, 1)})"
            for f in outliers
        )
        summary = (
            f"{metric_name} theo {dimension_name.lower()}: lệch hẳn so với các nhóm khác: {names}. "
            "Đây là quan sát thống kê, không phải nguyên nhân."
        )
    return {
        "summary": summary,
        "method": (
            "Median absolute deviation (Iglewicz & Hoaglin): |0,6745 * (x - median) / MAD| > "
            f"{OUTLIER_Z}. Không suy nguyên nhân."
        ),
        "confidence": "Quan sát thống kê trên mẫu hiện có",
        "anomaly": {
            "outliers": [
                {
                    "member": f.member,
                    "value": format(f.value, "f"),
                    "robust_z": format(f.robust_z, "f"),
                }
                for f in outliers
            ],
            "n_members": len(points),
            "threshold": format(OUTLIER_Z, "f"),
        },
    }
