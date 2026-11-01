"""The answer API exposes a composable block contract for chat renderers."""

from __future__ import annotations

import types

import pytest

from services.api.modules.qa.answer_blocks import blocks_of

pytestmark = pytest.mark.unit


def test_data_answer_blocks_include_text_metric_chart_table_and_sql() -> None:
    answer = types.SimpleNamespace(
        id="answer-1",
        kind="data",
        text="Doanh thu tăng.",
        figures=[
            {
                "key": "F1",
                "value": "120",
                "unit": "VND",
                "dimensions": {"region": "North"},
                "query_run_id": "run-1",
            }
        ],
        chart_spec={"chart_type": "bar", "title": "Doanh thu", "x_key": "region", "y_keys": ["value"]},
        citations=[],
    )

    blocks = blocks_of(answer)

    assert [block["type"] for block in blocks] == ["markdown", "metric", "chart", "table", "sql"]
    assert blocks[1]["value"] == "120"
    assert blocks[3]["rows"] == [{"region": "North", "value": "120", "unit": "VND"}]
    assert blocks[4]["query_run_id"] == "run-1"


def test_refusal_is_one_block_without_empty_data_sections() -> None:
    answer = types.SimpleNamespace(
        id="answer-2", kind="refusal", text="Không có dữ liệu.", figures=[], chart_spec=None, citations=[]
    )

    assert blocks_of(answer) == [{"type": "markdown", "id": "answer-2-markdown", "content": "Không có dữ liệu."}]
