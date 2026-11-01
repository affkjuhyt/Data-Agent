"""Report outline skill (skills/report_outline.py)."""

from __future__ import annotations

import pytest

from services.worker_agent.skills.report_outline import AnsweredMetric, outline_of

pytestmark = [pytest.mark.unit]


def test_outline_has_context_findings_and_limits() -> None:
    result = outline_of(
        [
            AnsweredMetric(
                "Tỷ lệ nợ xấu",
                "Q3/2026",
                ("F1", "F2"),
                chart_intent="comparing",
                insight_sentence="Miền Nam cao nhất {{F1}}.",
            ),
            AnsweredMetric("Dư nợ", None, ("F3",), chart_intent="time"),
        ],
        title="NPL quý 3",
        goal="Hiểu NPL theo vùng và dư nợ theo tháng.",
    )
    sections = result["report"]["sections"]
    assert [s["id"] for s in sections] == ["context", "finding_1", "finding_2", "limits"]
    assert sections[0]["body"].startswith("Hiểu NPL")
    assert sections[1]["figure_keys"] == ["F1", "F2"]
    assert sections[1]["chart_intent"] == "comparing"
    assert "nguyên nhân" in sections[-1]["body"].lower() or "Giả thuyết" in sections[-1]["body"]
