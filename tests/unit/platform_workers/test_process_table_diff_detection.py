"""process_table's diff-detection (sandbox_schema_docs.docx §2.7) — re-crawl
must skip describing/embedding a table whose schema_hash hasn't changed,
and must do the full describe+embed+upsert when it has. session/qdrant are
mocks; describe_table/embed are monkeypatched so this never touches a real
LLM, Qdrant, or Postgres — the same style as
test_memory_consumer_importance_gate.py.
"""
from __future__ import annotations

import pathlib
import sys
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "platform_workers")


@pytest.fixture(autouse=True)
def _sys_path():
    """platform-workers on sys.path so `workers.schema_crawler` (and the
    `libs.shared` it imports, via the backend/ pythonpath) resolve."""
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


_META = {
    "schema_name": "ANALYTICS",
    "table_name": "fact_stock_price",
    "columns": [{"name": "ticker", "type": "VARCHAR", "nullable": False, "comment": None}],
    "row_count": 100,
    "last_altered": None,
}


@pytest.mark.unit
def test_unchanged_schema_skips_describe_and_embed(monkeypatch):
    from workers.schema_crawler import worker

    from workers.schema_crawler.schema_hash import schema_hash
    unchanged_hash = schema_hash(_META["columns"])

    existing = SimpleNamespace(schema_hash=unchanged_hash, qdrant_point_id=None)
    session = MagicMock()
    session.scalar.return_value = existing
    qdrant = MagicMock()

    describe_spy = MagicMock()
    embed_spy = MagicMock()
    monkeypatch.setattr(worker, "describe_table", describe_spy)
    monkeypatch.setattr(worker, "embed", embed_spy)

    changed = worker.process_table(session, qdrant, "tenant-1", "wh-1", _META)

    assert changed is False
    describe_spy.assert_not_called()
    embed_spy.assert_not_called()
    qdrant.upsert.assert_not_called()
    # Only last_crawled_at is touched on the loaded row; no upsert statement runs.
    assert existing.last_crawled_at is not None
    session.execute.assert_not_called()


@pytest.mark.unit
def test_changed_schema_runs_full_describe_embed_upsert(monkeypatch):
    from workers.schema_crawler import worker

    session = MagicMock()
    session.scalar.return_value = SimpleNamespace(
        schema_hash="stale-hash-does-not-match", qdrant_point_id=str(uuid.uuid4()),
    )
    qdrant = MagicMock()

    monkeypatch.setattr(worker, "describe_table", lambda *a, **k: {
        "keywords": ["x"], "categories": {}, "use_cases": [], "join_hints": ["dim_x.id"],
    })
    monkeypatch.setattr(worker, "embed", lambda text: [0.1, 0.2])

    changed = worker.process_table(session, qdrant, "tenant-1", "wh-1", _META)

    assert changed is True
    qdrant.upsert.assert_called_once()
    payload = qdrant.upsert.call_args.kwargs["points"][0].payload
    assert payload["table_name"] == "fact_stock_price"
    assert payload["join_hints"] == ["dim_x.id"]
    # One INSERT .. ON CONFLICT DO UPDATE
    session.execute.assert_called_once()
    assert "INSERT" in str(session.execute.call_args.args[0])


@pytest.mark.unit
def test_new_table_with_no_existing_row_runs_full_pipeline(monkeypatch):
    from workers.schema_crawler import worker

    session = MagicMock()
    session.scalar.return_value = None  # no SchemaCatalog row yet
    qdrant = MagicMock()

    monkeypatch.setattr(worker, "describe_table", lambda *a, **k: {
        "keywords": [], "categories": {}, "use_cases": [], "join_hints": [],
    })
    monkeypatch.setattr(worker, "embed", lambda text: [0.1])

    changed = worker.process_table(session, qdrant, "tenant-1", "wh-1", _META)

    assert changed is True
    qdrant.upsert.assert_called_once()


@pytest.mark.unit
def test_point_id_is_deterministic_across_recrawls():
    from workers.schema_crawler import worker

    a = worker._point_id("t1", "w1", "ANALYTICS", "fact_stock_price")
    b = worker._point_id("t1", "w1", "ANALYTICS", "fact_stock_price")
    c = worker._point_id("t1", "w1", "ANALYTICS", "dim_company")

    assert a == b
    assert a != c
