"""llm_describe / embed (sandbox_schema_docs.docx §2.4, §2.5). embed's HTTP
shape is tested via respx (no real LLM, no real network), same technique as
test_workflow_memory_embed.py; describe_table goes through common/llm.ask,
whose HTTP shape test_llm_ask.py covers, so it is stubbed at that seam.
"""
from __future__ import annotations

import pathlib
import sys

import httpx
import pytest
import respx

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "platform_workers")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.mark.unit
@respx.mock
def test_embed_parses_successful_response():
    from workers.schema_crawler import embed as embed_mod

    respx.post(f"{embed_mod.LITELLM_BASE}/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
    )

    vector = embed_mod.embed("fact_stock_price OHLCV")

    assert vector == [0.1, 0.2]


@pytest.mark.unit
def test_describe_table_parses_json_response(monkeypatch):
    from workers.schema_crawler import llm_describe

    answer = llm_describe.Description(
        keywords=["OHLCV", "stock price"],
        categories={"ticker": "stock symbol"},
        use_cases=["compute daily returns"],
        join_hints=["dim_company.ticker"],
    )
    monkeypatch.setattr(llm_describe.llm_client, "ask", lambda *a, **k: answer)

    result = llm_describe.describe_table(
        "fact_stock_price",
        [{"name": "ticker", "type": "VARCHAR", "comment": None}],
        4820000,
    )

    assert result["keywords"] == ["OHLCV", "stock price"]
    assert result["categories"] == {"ticker": "stock symbol"}
    assert result["use_cases"] == ["compute daily returns"]
    assert result["join_hints"] == ["dim_company.ticker"]


@pytest.mark.unit
def test_describe_table_handles_non_json_response_gracefully(monkeypatch):
    from workers.schema_crawler import llm_describe

    monkeypatch.setattr(llm_describe.llm_client, "ask", lambda *a, **k: None)

    result = llm_describe.describe_table("t", [{"name": "a", "type": "VARCHAR"}], None)

    assert result == {"keywords": [], "categories": {}, "use_cases": [], "join_hints": []}


@pytest.mark.unit
def test_render_text_description_includes_all_sections():
    from workers.schema_crawler.llm_describe import render_text_description

    description = {
        "keywords": ["OHLCV", "stock price"],
        "categories": {"ticker": "stock symbol"},
        "use_cases": ["compute daily returns"],
        "join_hints": ["dim_company.ticker"],
    }

    text = render_text_description("fact_stock_price", description)

    assert "Name: fact_stock_price" in text
    assert "OHLCV" in text
    assert "ticker — stock symbol" in text
    assert "compute daily returns" in text
    assert "dim_company.ticker" in text


@pytest.mark.unit
def test_render_text_description_omits_empty_sections():
    from workers.schema_crawler.llm_describe import render_text_description

    text = render_text_description("t", {"keywords": [], "categories": {}, "use_cases": [], "join_hints": []})

    assert text == "Name: t"
