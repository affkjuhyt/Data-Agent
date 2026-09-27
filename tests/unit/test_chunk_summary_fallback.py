"""summarize_chunk's fallback path — hit when the LLM summarization call fails
twice. Before this, a table chunk's fallback was the naive first-sentence
split, which for markdown-table content ("Table: Table_7 ... | Chỉ tiêu | 2018
| ...") cuts right after the header (the first literal '.' in a row like
"1.Nợ công...") and leaves a summary indistinguishable from any other table's
own caption. That summary is what light_rag's rerank() scores against the
query — a useless one silently pushes a chunk with the exact right answer out
of the top results. Found via a live probe on 2026-09-27: 480 of 1504 chunks
in one tenant's KB carried this pattern.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from libs.ingestion.silver import shared

pytestmark = pytest.mark.unit

TABLE = {
    "table_name": "Table_7",
    "data": {
        "headers": ["Chỉ tiêu", "2018", "2019", "2020", "2021", "2022 (P)"],
        "rows": [
            ["1.Nợ công so với tổng sản phẩm quốc dân(GDP) (%)", "58,3", "55,0", "55,9", "42,7", "37,4"],
            ["a. Nợ Chính phủ so với tổng sản phẩm quốc dân (GDP) (%)", "49,9", "48,0", "49,9", "38,7", "34,2"],
        ],
    },
}
TABLE_CONTENT = (
    "Table: Table_7 (7 row(s), 6 column(s): Chỉ tiêu, 2018, 2019, 2020, 2021, 2022 (P))\n"
    "| Chỉ tiêu | 2018 | 2019 | 2020 | 2021 | 2022 (P) |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| 1.Nợ công so với tổng sản phẩm quốc dân(GDP) (%) | 58,3 | 55,0 | 55,9 | 42,7 | 37,4 |"
)


def test_the_old_fallback_cut_a_table_right_after_its_header() -> None:
    # Documents the bug this fix replaces: naive split on the content's own text.
    result = shared._fallback_chunk_summary(TABLE_CONTENT, table=None)
    assert result["summary"].startswith("Table: Table_7")
    assert "37,4" not in result["summary"] and "Nợ công" not in result["summary"]


def test_a_table_fallback_names_real_rows_and_columns_instead_of_the_caption() -> None:
    result = shared._fallback_chunk_summary(TABLE_CONTENT, table=TABLE)
    summary = result["summary"]
    assert "Table_7" in summary
    assert "Nợ công so với tổng sản phẩm quốc dân(GDP)" in summary
    assert "Chỉ tiêu" in summary


def test_a_non_table_chunk_keeps_the_first_sentence_fallback() -> None:
    result = shared._fallback_chunk_summary("Đây là câu đầu tiên. Câu thứ hai không liên quan.", table=None)
    assert result["summary"] == "Đây là câu đầu tiên"


@pytest.mark.parametrize("table", [None, TABLE])
def test_summarize_chunk_falls_back_and_logs_when_the_llm_call_keeps_failing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, table: dict | None,
) -> None:
    monkeypatch.setattr(shared, "_llm_generate_json", AsyncMock(side_effect=RuntimeError("gateway down")))
    with caplog.at_level("WARNING"):
        result = asyncio.run(shared.summarize_chunk(TABLE_CONTENT, table=table))
    assert result["summary"]
    assert any("fallback" in r.message for r in caplog.records)
    if table:
        assert "Nợ công" in result["summary"]


def test_summarize_chunk_uses_the_real_llm_result_when_it_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        shared, "_llm_generate_json",
        AsyncMock(return_value={"summary": "Bảng nợ công 2018-2022", "intents": ["document_content"]}),
    )
    result = asyncio.run(shared.summarize_chunk(TABLE_CONTENT, table=TABLE))
    assert result["summary"] == "Bảng nợ công 2018-2022"
