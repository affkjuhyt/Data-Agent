from __future__ import annotations

import pytest
from pydantic import ValidationError

from libs.contracts.v1.analysis_plan import AnalysisPlan

pytestmark = pytest.mark.unit


def test_analysis_plan_accepts_a_dag_and_preserves_output_contract() -> None:
    plan = AnalysisPlan.model_validate(
        {
            "goal": "Phân tích doanh số theo khu vực",
            "steps": [
                {"id": "understand", "type": "understand_intent", "output": "intent"},
                {"id": "query", "type": "query_data", "depends_on": ["understand"], "output": "result"},
                {"id": "visualize", "type": "select_visualization", "depends_on": ["query"], "output": "chart"},
            ],
            "outputs": [{"step": "query", "as": "table"}, {"step": "visualize", "as": "chart"}],
        }
    )

    assert plan.outputs[1].as_ == "chart"


def test_analysis_plan_rejects_cycles_and_unknown_dependencies() -> None:
    with pytest.raises(ValidationError):
        AnalysisPlan.model_validate(
            {
                "goal": "invalid",
                "steps": [
                    {"id": "a", "type": "one", "depends_on": ["b"]},
                    {"id": "b", "type": "two", "depends_on": ["a"]},
                ],
                "outputs": [{"step": "a", "as": "text"}],
            }
        )
