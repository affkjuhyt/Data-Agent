"""Query results travel on their own channel (V3 R0-1, NOP-279).

A SQL result used to be one more text chunk: ranked by RRF against document
passages, cut by the reranker's top-N, trimmed to the responder's character
budget, and invisible to a canvas without rrf/reranker. These tests pin the
four failures that design allowed, now that results are ResultSets in
`state["result_sets"]` (application/result_sets.py).
"""

from __future__ import annotations

import json
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from ._paths import RUNTIME_LAYER_DIRS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

pytest.importorskip("litellm", reason="litellm not installed in this environment")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (*RUNTIME_LAYER_DIRS,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


# Doanh thu thuần tháng 8 theo chi nhánh: 48.215.300.000 in total, a figure no
# single row shows.
_BRANCHES = [
    {"chi_nhanh": "Hà Nội", "doanh_thu_thuan": 20115300000},
    {"chi_nhanh": "TP.HCM", "doanh_thu_thuan": 18900000000},
    {"chi_nhanh": "Đà Nẵng", "doanh_thu_thuan": 9200000000},
]


def _rs(rows: list[dict] = _BRANCHES) -> dict:
    from services.workflow_runtime.application import result_sets

    return result_sets.build(
        rows,
        connection_id="dw_pilot",
        engine="postgres",
        generated_by="semantic_layer",
        sql="SELECT chi_nhanh, SUM(doanh_thu_thuan) AS doanh_thu_thuan FROM mart.fact_doanh_thu GROUP BY 1",
        metric={"metric": "doanh_thu_thuan@3", "unit": "VND"},
    )


def _chunks(n: int) -> list[dict]:
    return [
        {
            "id": f"kb_{i}",
            "content": f"Đoạn quy định số {i} về ghi nhận doanh thu. " * 20,
            "source": f"policy-{i}.pdf",
            "doc_id": f"doc-{i}",
            "page": 1,
        }
        for i in range(n)
    ]


def _llm(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=content))]
    resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    return resp


async def _respond(monkeypatch, state: dict) -> tuple[dict, str]:
    """Run the responder; return its output and the system prompt it sent."""
    import nodes.model_harness.responder as responder

    call = AsyncMock(return_value=_llm("Tổng doanh thu thuần là 48.215.300.000 đồng [1]."))
    monkeypatch.setattr(responder.litellm, "acompletion", call)
    out = await responder.responder_node(
        {"query": "doanh thu thuần tháng 8", "messages": [], **state}
    )
    return out, call.await_args.kwargs["messages"][0]["content"]


async def test_a_canvas_without_rrf_or_reranker_still_answers_from_the_table(monkeypatch):
    """db_connector wired straight to the responder: nothing ever fills
    reranked_chunks, which used to mean the responder saw no data at all."""
    rs = _rs()
    out, prompt = await _respond(
        monkeypatch,
        {
            "config": {"system_prompt": "x"},
            "result_sets": [rs],
        },
    )

    assert f"result={rs['result_id']}" in prompt
    for row in _BRANCHES:
        assert row["chi_nhanh"] in prompt
    assert [r["id"] for r in out["evidence"]["runs"]] == [rs["result_id"]]


async def test_six_chunks_through_ranking_do_not_push_the_table_out(monkeypatch):
    """RRF no longer sees the table, so the reranker's top-N cannot drop it,
    and the chunk character budget cannot trim it: it is [1], whole."""
    from nodes.model_harness.rrf_ranking import rrf_ranking_node

    rs = _rs()
    state = {
        "kb_chunks": _chunks(6),
        "mcp_results": [],
        "tool_results": [],
        "result_sets": [rs],
        "config": {},
    }
    ranked = rrf_ranking_node(state)["rrf_results"]
    assert {r["id"] for r in ranked} == {f"kb_{i}" for i in range(6)}

    # What a reranker with top_n=5 hands on, under a budget the chunks overflow.
    out, prompt = await _respond(
        monkeypatch,
        {
            "config": {"system_prompt": "x", "context_max_chars": 300},
            "result_sets": [rs],
            "reranked_chunks": ranked[:5],
        },
    )

    assert prompt.index("[1] source=dw_pilot") < prompt.index("[2] source=policy-")
    assert json.dumps(_BRANCHES, ensure_ascii=False) in prompt
    assert "sum=48215300000" in prompt
    assert out["citations"][0]["result_id"] == rs["result_id"]


@pytest.mark.parametrize(
    ("answer", "verdict"),
    [
        ("Tổng doanh thu thuần là 48.215.300.000 đồng [1].", "PASS"),
        ("Tổng doanh thu thuần là 52.000.000.000 đồng [1].", "FAIL"),
    ],
)
async def test_the_judge_checks_a_total_against_the_stats(monkeypatch, answer, verdict):
    """The total appears in no row; it is grounded by the stats computed over
    every row. A figure matching neither is GROUNDING_ERROR without asking a
    model."""
    import nodes.model_harness.judge as judge_mod

    llm = AsyncMock(
        return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"verdict": "PASS"}'))]
        )
    )
    monkeypatch.setattr(judge_mod.llm_call, "acompletion", llm)

    result = await judge_mod.judge_node(
        {
            "query": "doanh thu thuần tháng 8",
            "response": answer,
            "config": {},
            "loop_iteration": 0,
            "result_sets": [_rs()],
            "reranked_chunks": _chunks(2),
        }
    )

    assert result["judge_verdict"] == verdict
    if verdict == "FAIL":
        assert result["judge_error_type"] == "GROUNDING_ERROR"
        llm.assert_not_awaited()
    else:
        assert result["verifier"]["passed"] is True


def test_the_frame_carries_at_most_fifty_rows_and_stats_over_all_of_them():
    """1 200 rows: the frame previews 50, says 1 200, and its stats are the
    whole table's — the rest is fetched by result_id, never streamed."""
    from libs.contracts.v1 import ResultSet
    from services.workflow_runtime.application import result_sets

    rows = [{"ngay": f"2026-08-{d % 28 + 1:02d}", "so_luong": 2} for d in range(1200)]
    frame = result_sets.frame(_rs(rows), citation_index=1)

    assert len(frame["rows_preview"]) == 50
    assert frame["row_count"] == 1200
    assert frame["stats"]["so_luong"]["sum"] == "2400"
    assert frame["kind"] == "series"
    assert len(json.dumps(frame, ensure_ascii=False)) < 8000
    with pytest.raises(ValueError, match="at most 50 items"):
        ResultSet.model_validate({**_rs(rows), "rows_preview": rows[:51]})


def test_result_set_redacts_sql_literals_but_hashes_the_executed_statement():
    from libs.shared import audit
    from services.workflow_runtime.application import result_sets

    sql = "SELECT amount FROM sales WHERE email = 'alice@example.com' AND branch_id = 42"
    rs = result_sets.build(
        [{"amount": 10}],
        connection_id="dw",
        engine="postgres",
        generated_by="llm",
        sql=sql,
    )

    assert rs["provenance"]["sql"] == "SELECT amount FROM sales WHERE email = ? AND branch_id = ?"
    assert rs["provenance"]["sql_hash"] == audit.statement_hash(sql)
    assert rs["provenance"]["tables"] == ["sales"]


def test_result_context_row_budget_is_bounded_and_tolerates_bad_config():
    from services.workflow_runtime.application import result_sets

    assert result_sets.context_max_rows({"context_max_result_rows": 12}) == 12
    assert result_sets.context_max_rows({"context_max_result_rows": 500}) == 50
    assert result_sets.context_max_rows({"context_max_result_rows": "bad"}) == 50


async def test_a_pandas_frame_becomes_a_result_set_with_its_real_length(monkeypatch, tmp_path):
    """The epilogue prints rows, not a markdown picture; when the slice that
    fits stdout is shorter than the frame, the result says it was cut."""
    from services.workflow_runtime.application import result_sets
    from services.workflow_runtime.application.nodes.sandbox.tool_executor.handlers import data

    csv = tmp_path / "sales.csv"
    csv.write_bytes(b"region,amount\nnorth,10\n")
    monkeypatch.setattr(data.llm_call, "acompletion", AsyncMock(return_value=_llm("result = 1")))
    rows = [{"region": "north", "amount": 10}, {"region": "south", "amount": 5}]
    stdout = f"{data._FRAME_SENTINEL}\n740\n{json.dumps(rows)}"
    monkeypatch.setattr(
        data, "submit_and_poll", AsyncMock(return_value={"success": True, "stdout": stdout})
    )

    item, error = await data._text_to_pandas_handler(
        {"arguments": {"file_path": str(csv)}, "query": "doanh thu theo vùng"},
        {"tool_config": {"text_to_pandas": {"allowed_dir": str(tmp_path)}}},
    )

    assert error is None and result_sets.is_tabular(item)
    rs, all_rows = result_sets.from_handler_item(item)
    assert all_rows == rows
    assert rs["provenance"]["engine"] == "pandas"
    assert rs["provenance"]["truncation"]["rows_total"] == 740
    assert [w["code"] for w in rs["warnings"]] == ["truncated"]
