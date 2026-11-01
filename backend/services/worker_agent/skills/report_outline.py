"""Dàn báo cáo (skill category `report`): section outline from answered metrics.

Builds a steerable deep-analysis skeleton (F3): context → findings per metric →
limits. No prose generation — only headings and the figure keys / metric names
the caller already has, so a later Narrator can fill text under BR-171.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnsweredMetric:
    """One answered slice to place in the report."""

    metric_name: str
    period_label: str | None
    figure_keys: tuple[str, ...]
    chart_intent: str | None = None
    insight_sentence: str | None = None


def outline_of(
    metrics: list[AnsweredMetric],
    *,
    title: str,
    goal: str | None = None,
) -> dict[str, Any]:
    sections: list[dict[str, Any]] = [
        {
            "id": "context",
            "heading": "Bối cảnh",
            "body": goal or "Câu hỏi phân tích đã đặt.",
            "figure_keys": [],
        }
    ]
    for i, m in enumerate(metrics, start=1):
        period = f" ({m.period_label})" if m.period_label else ""
        sections.append(
            {
                "id": f"finding_{i}",
                "heading": f"{m.metric_name}{period}",
                "body": m.insight_sentence
                or "Điền nhận định từ figures (không thêm số ngoài figure_keys).",
                "figure_keys": list(m.figure_keys),
                "chart_intent": m.chart_intent,
            }
        )
    sections.append(
        {
            "id": "limits",
            "heading": "Giới hạn",
            "body": (
                "Chỉ dùng số đã có nguồn (figure / query run). Không suy nguyên nhân "
                "ngoài khối Giả thuyết đã duyệt."
            ),
            "figure_keys": [],
        }
    )
    return {
        "summary": f"Dàn báo cáo “{title}”: {len(metrics)} phần nhận định.",
        "method": "Khung cố định context → findings → limits; số chỉ từ figure_keys.",
        "confidence": "Khung cấu trúc; nội dung số phụ thuộc nguồn đã truy vấn",
        "report": {"title": title, "sections": sections},
    }
