"""Integration tests for backend/services/workflow_runtime/schema_catalog.py (the
agent-facing read side of the Schema Crawler Worker) and its wiring into
_text_to_sql_handler — real Qdrant testcontainer, embedding stubbed to a
fixed-but-distinguishable vector (same technique as
test_workflow_schema_memory.py's `wm` fixture). No real LLM/Snowflake
contacted anywhere in this file.
"""
from __future__ import annotations

import importlib
import pathlib
import sys
from unittest.mock import MagicMock

import pytest
from qdrant_client.models import PointStruct

pytestmark = pytest.mark.integration

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")


def _fake_vector(text: str, dim: int = 4) -> list[float]:
    h = abs(hash(text))
    return [((h >> (i * 4)) % 97) / 97.0 for i in range(dim)]


@pytest.fixture
async def sc(qdrant_container, monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIM", "4")
    monkeypatch.setenv("QDRANT_URL", qdrant_container["url"])

    added = [p for p in (_SVC_DIR, str(REPO_ROOT)) if p not in sys.path]
    sys.path[:0] = added
    try:
        import schema_catalog as sc_mod
        importlib.reload(sc_mod)

        async def _fake_embed(text: str):
            return _fake_vector(text)
        monkeypatch.setattr(sc_mod, "_embed", _fake_embed)

        await sc_mod.init_schema_catalog_qdrant()
        yield sc_mod
        await sc_mod.close_schema_catalog_qdrant()
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)


async def _seed_point(sc_mod, tenant_id: str, table_name: str, text: str, schema_name: str = "ANALYTICS"):
    await sc_mod._qdrant.upsert(sc_mod._COLLECTION, points=[PointStruct(
        id=__import__("uuid").uuid4().hex,
        vector=_fake_vector(text),
        payload={
            "tenant_id": tenant_id, "schema_name": schema_name, "table_name": table_name,
            "text_description": text, "columns": ["ticker", "close"],
            "row_count": 100, "join_hints": ["dim_company.ticker"],
        },
    )])


async def test_retrieve_schema_catalog_finds_seeded_table(sc):
    await _seed_point(sc, "tenant-1", "fact_stock_price", "OHLCV stock price daily")

    results = await sc.retrieve_schema_catalog("tenant-1", "OHLCV stock price daily")

    assert len(results) == 1
    assert results[0]["table_name"] == "fact_stock_price"


async def test_retrieve_schema_catalog_isolates_tenants(sc):
    await _seed_point(sc, "tenant-1", "fact_stock_price", "OHLCV stock price daily")

    results = await sc.retrieve_schema_catalog("tenant-2", "OHLCV stock price daily")

    assert results == []


async def test_retrieve_schema_catalog_filters_by_schema_name(sc):
    await _seed_point(sc, "tenant-1", "fact_stock_price", "OHLCV stock price daily", schema_name="ANALYTICS")

    results = await sc.retrieve_schema_catalog("tenant-1", "OHLCV stock price daily", schema_name="OTHER_SCHEMA")

    assert results == []


async def test_format_catalog_context_renders_columns_and_join_hints(sc):
    tables = [{
        "schema_name": "ANALYTICS", "table_name": "fact_stock_price",
        "text_description": "OHLCV stock price", "columns": ["ticker", "close"],
        "join_hints": ["dim_company.ticker"],
    }]

    ctx = sc.format_catalog_context(tables)

    assert "TABLE ANALYTICS.fact_stock_price" in ctx
    assert "ticker" in ctx and "close" in ctx
    assert "dim_company.ticker" in ctx


async def test_format_catalog_context_empty_list_returns_empty_string(sc):
    assert sc.format_catalog_context([]) == ""


# ─── _text_to_sql_handler prefers Schema Catalog over Schema Memory ────────

async def test_text_to_sql_handler_prefers_schema_catalog_over_schema_memory(sc, monkeypatch):
    await _seed_point(sc, "tenant-1", "fact_stock_price", "OHLCV stock price daily")

    added = [p for p in (_SVC_DIR, str(REPO_ROOT)) if p not in sys.path]
    sys.path[:0] = added
    try:
        import importlib as _il
        data_mod = _il.import_module("nodes.sandbox.tool_executor.handlers.data")
        _il.reload(data_mod)

        # Schema Memory must NOT even be queried when Schema Catalog has a hit.
        memory_spy = MagicMock(return_value=[])

        async def _fake_retrieve_schema_memory(*a, **k):
            memory_spy()
            return []
        monkeypatch.setattr(data_mod, "retrieve_schema_memory", _fake_retrieve_schema_memory)

        captured = {}

        def fake_completion(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            resp = MagicMock()
            resp.choices = [MagicMock(message=MagicMock(content="SELECT 1"))]
            return resp
        monkeypatch.setattr(data_mod.litellm, "completion", fake_completion)

        # asyncpg.connect would try a real network connection — short-circuit
        # by giving a bogus db_url and accepting the eventual connection
        # error; the prompt is captured before that point, which is what
        # this test is verifying.
        result, error = await data_mod._text_to_sql_handler(
            call={"query": "OHLCV stock price daily"},
            cfg={
                "tool_config": {"text_to_sql": {"db_url": "postgresql://bad:bad@localhost:1/bad", "data_source": "snowflake"}},
                "tenant_id": "tenant-1",
            },
        )

        assert "fact_stock_price" in captured["prompt"]
        assert "OHLCV" in captured["prompt"]
        memory_spy.assert_not_called()
    finally:
        for p in added:
            if p in sys.path:
                sys.path.remove(p)
