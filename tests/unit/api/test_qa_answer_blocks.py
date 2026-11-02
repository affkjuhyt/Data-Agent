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
        chart_spec={
            "chart_type": "bar",
            "title": "Doanh thu",
            "x_key": "region",
            "y_keys": ["value"],
        },
        citations=[],
    )

    blocks = blocks_of(answer)

    assert [block["type"] for block in blocks] == ["markdown", "metric", "chart", "table", "sql"]
    assert blocks[1]["value"] == "120"
    assert blocks[3]["rows"] == [{"region": "North", "value": "120", "unit": "VND"}]
    assert blocks[4]["query_run_id"] == "run-1"


def test_refusal_is_one_block_without_empty_data_sections() -> None:
    answer = types.SimpleNamespace(
        id="answer-2",
        kind="refusal",
        text="Không có dữ liệu.",
        figures=[],
        chart_spec=None,
        citations=[],
    )

    assert blocks_of(answer) == [
        {"type": "markdown", "id": "answer-2-markdown", "content": "Không có dữ liệu."}
    ]


def _data_answer(**extra: object) -> types.SimpleNamespace:
    base = {
        "id": "answer-2",
        "kind": "data",
        "text": "Doanh thu 2013.",
        "figures": [
            {
                "key": "F1",
                "value": "10",
                "dimensions": {},
                "query_run_id": "run-2",
                "trust": "Theo chỉ số chuẩn",
            }
        ],
        "chart_spec": None,
        "citations": [],
        "number_check": {},
        "reconcile_badge": None,
        "product_scope": "in_scope",
    }
    return types.SimpleNamespace(**{**base, **extra})


def test_caveats_list_failed_checks_and_non_standard_trust() -> None:
    answer = _data_answer(
        figures=[
            {
                "key": "F1",
                "value": "10",
                "dimensions": {},
                "query_run_id": "r",
                "trust": "Khám phá",
            },
            {
                "key": "F2",
                "value": "11",
                "dimensions": {},
                "query_run_id": "r",
                "trust": "Khám phá",
            },
        ],
        number_check={
            "verification": {
                "checks": [
                    {"name": "Số trong câu có nguồn", "passed": True, "detail": "2/2 số"},
                    {
                        "name": "Khớp số đã nộp",
                        "passed": False,
                        "detail": "Số hiện tại khác số đã nộp",
                    },
                ]
            }
        },
        reconcile_badge="differs_from_filed",
        product_scope="out_of_scope",
    )
    caveats = blocks_of(answer)[-1]
    assert caveats == {
        "type": "caveats",
        "id": "answer-2-caveats",
        "items": [
            "Số hiện tại khác số đã nộp",
            "Số liệu Khám phá.",
            "Số hiện tại khác số đã nộp của kỳ.",
            "Câu trả lời nằm ngoài mọi sản phẩm dữ liệu, không có biên nhận.",
        ],
    }


def test_no_caveats_block_when_nothing_to_say() -> None:
    assert "caveats" not in [b["type"] for b in blocks_of(_data_answer())]


def test_the_sql_block_names_the_run_not_its_sql() -> None:
    # The SQL is read through /v1/query-runs/{id}/sql, which checks view_detail and audits.
    [sql] = [b for b in blocks_of(_data_answer()) if b["type"] == "sql"]
    assert sql == {"type": "sql", "id": "answer-2-sql-0", "query_run_id": "run-2"}


def test_caveats_say_which_figures_were_withheld_and_what_was_left_out() -> None:
    answer = _data_answer(
        figures=[
            {
                "key": "F1",
                "value": None,
                "dimensions": {},
                "query_run_id": "r",
                "trust": "Theo chỉ số chuẩn",
                "display_state": "masked_stale",
            },
            {
                "key": "F2",
                "value": None,
                "dimensions": {},
                "query_run_id": "r",
                "trust": "Theo chỉ số chuẩn",
                "display_state": "not_available",
            },
            {
                "key": "F3",
                "value": "4",
                "dimensions": {},
                "query_run_id": "r",
                "trust": "Theo chỉ số chuẩn",
                "display_state": "shown",
            },
        ],
        number_check={
            "notes": [
                "Dư nợ không chia được theo nhóm nợ. Trả về số tổng hợp theo các chiều dữ liệu được phép."
            ]
        },
    )
    caveats = blocks_of(answer)[-1]
    assert caveats["items"] == [
        "Dư nợ không chia được theo nhóm nợ. Trả về số tổng hợp theo các chiều dữ liệu được phép.",
        "1 số bị ẩn vì dữ liệu của sản phẩm dữ liệu đã quá hạn.",
        "1 số chưa có dữ liệu trong kỳ.",
    ]


def test_an_answer_the_agent_composed_is_served_in_the_order_it_chose() -> None:
    composed = [
        {"type": "insights", "id": "b1", "composed": True, "items": [{"text": "x"}]},
        {"type": "chart", "id": "b2", "composed": True, "spec": {"data": []}},
        {"type": "markdown", "id": "b3", "composed": True, "content": "Kết luận."},
        "not a block",
    ]
    answer = types.SimpleNamespace(
        id="answer-2",
        kind="data",
        text="Kết luận.",
        figures=[],
        chart_spec=None,
        citations=[],
        blocks=composed,
    )

    assert [block["type"] for block in blocks_of(answer)] == ["insights", "chart", "markdown"]
