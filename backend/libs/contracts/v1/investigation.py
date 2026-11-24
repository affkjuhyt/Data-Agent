"""The plan an investigation opened from an alert starts with (AL-11, ANALYTICS_SPEC F-20 B.4).

The api seeds it in the workspace it creates from the alert, and the notebook shows it; the worker
runs the same plan when the investigation's question reaches Ask. Both build it here from the
alert's own subject (metric, period, split), so what the person saw is what runs, and the worker
never runs a plan read back from the workspace's params, which an editor may change.

Its steps are the agent's PlanStep shape (worker_agent/agentic/plan.py): the metric in the
alert's period against the one before, then that change broken down by every dimension the asker
may cut by (explain_change reads an empty list so, with the asker's rights, when it runs).
Nothing runs until the person asks (L4, 01 §2.3).
"""

from __future__ import annotations

from typing import Any

COMPARE_TO = "previous_period"


def question(metric_name: str, period: str) -> str:
    # "Vì sao": the why the agent's explain_change step answers (ExplainChange.asked).
    return f"Vì sao {metric_name} kỳ {period} thay đổi so với kỳ trước?"


def investigation(
    *, metric_code: str, metric_name: str, period: str, split: str | None
) -> dict[str, Any]:
    """{"question", "plan"}: the question Ask is sent and the plan that answers it."""
    return {
        "question": question(metric_name, period),
        "plan": {
            "goal": f"Tìm nguyên nhân {metric_name} kỳ {period} vượt luật theo dõi",
            "thought": "Lấy số kỳ này so với kỳ trước, rồi phân rã chênh lệch theo các chiều.",
            "mode": "analyze",
            "steps": [
                {
                    "id": "s1",
                    "tool": "query_metric",
                    "instruction": f"Lấy {metric_name} kỳ {period} so với kỳ trước",
                    "depends_on": [],
                    "args": {
                        "metric": metric_code,
                        "period": period,
                        "compare_to": COMPARE_TO,
                        "group_by": [split] if split else [],
                    },
                },
                {
                    "id": "s2",
                    "tool": "explain_change",
                    "instruction": (
                        f"Phân rã chênh lệch của {metric_name} kỳ {period} theo các chiều được phép"
                    ),
                    "depends_on": ["s1"],
                    "args": {
                        "metric": metric_code,
                        "period": period,
                        "compare_to": COMPARE_TO,
                        "dimensions": [],
                    },
                },
            ],
        },
    }
