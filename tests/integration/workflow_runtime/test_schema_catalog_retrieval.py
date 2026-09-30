"""Integration tests for backend/services/workflow_runtime/infrastructure/schema_catalog.py (the
agent-facing read side of the Schema Crawler Worker) and its wiring into
_get_schema_text — real Qdrant testcontainer, embedding stubbed to a
fixed-but-distinguishable vector (same technique as
test_workflow_schema_memory.py's `wm` fixture). No real LLM/Snowflake
contacted anywhere in this file.
"""
from __future__ import annotations

import importlib
from unittest.mock import AsyncMock

import pytest
from qdrant_client.models import PointStruct

pytestmark = pytest.mark.integration


def _fake_vector(text: str, dim: int = 4) -> list[float]:
    h = abs(hash(text))
    return [((h >> (i * 4)) % 97) / 97.0 for i in range(dim)]


@pytest.fixture
async def sc(qdrant_container, monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIM", "4")
    monkeypatch.setenv("QDRANT_URL", qdrant_container["url"])

    # The package path, which is how the text_to_sql handler imports it; a
    # bare `import schema_catalog` is a second module object whose Qdrant
    # client the handler never sees.
    import services.workflow_runtime.infrastructure.schema_catalog as sc_mod
    importlib.reload(sc_mod)

    async def _fake_embed(text: str):
        return _fake_vector(text)
    monkeypatch.setattr(sc_mod, "_embed", _fake_embed)

    await sc_mod.init_schema_catalog_qdrant()
    yield sc_mod
    await sc_mod.close_schema_catalog_qdrant()


async def _seed_point(
    sc_mod, tenant_id: str, table_name: str, text: str, schema_name: str = "ANALYTICS", warehouse_id: str = "",
):
    await sc_mod._qdrant.upsert(sc_mod._COLLECTION, points=[PointStruct(
        id=__import__("uuid").uuid4().hex,
        vector=_fake_vector(text),
        payload={
            "tenant_id": tenant_id, "schema_name": schema_name, "table_name": table_name,
            "text_description": text, "columns": ["ticker", "close"],
            "row_count": 100, "join_hints": ["dim_company.ticker"],
            "warehouse_id": warehouse_id,
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


# ─── text_to_sql takes its schema block from the catalog ──────────────────

async def test_a_catalogued_connection_gets_its_schema_from_the_catalog(sc, monkeypatch):
    """A connection naming its crawled warehouse is described by the catalog,
    and information_schema is never introspected for it."""
    warehouse = "b0000000-0000-0000-0000-000000000002"
    await _seed_point(sc, "tenant-1", "fact_stock_price", "OHLCV stock price daily", warehouse_id=warehouse)
    await _seed_point(sc, "tenant-1", "other_wh_table", "OHLCV stock price daily", warehouse_id="another-warehouse")

    from services.workflow_runtime.application.nodes.sandbox.tool_executor.handlers import data as data_mod
    monkeypatch.setattr(data_mod, "_list_tables", AsyncMock(side_effect=AssertionError("introspected")))

    text = await data_mod._get_schema_text(
        "tenant-1",
        {"type": "postgres", "host": "h", "database": "d", "catalog_warehouse_id": warehouse},
        "OHLCV stock price daily",
    )

    assert "fact_stock_price" in text
    assert "OHLCV" in text
    assert "other_wh_table" not in text
