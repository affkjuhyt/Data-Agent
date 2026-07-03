"""Pure-mock test for the importance gate added to memory-consumer's
process_insight() (doc §II Bước 2 / §VI — "only insights worth remembering
get stored"). No Docker, no DB, no LLM — cur/qdrant are MagicMocks; we only
assert whether process_insight() dispatches to a write path at all.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "memory-consumer")


@pytest.fixture(autouse=True)
def _sys_path():
    """schema-crawler-worker also has a main.py — without evicting the
    cached module, whichever test file imports "main" first in the session
    wins sys.modules['main'] for every test after it, regardless of sys.path."""
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    sys.modules.pop("main", None)
    yield
    sys.modules.pop("main", None)
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.mark.unit
def test_low_importance_add_is_skipped(monkeypatch):
    import main as mc_main

    cur, qdrant = MagicMock(), MagicMock()
    insight = {"memory_type": "semantic", "action": "add", "importance_score": 0.2,
               "content": "trời hôm nay đẹp nhỉ", "domain": "general"}

    mc_main.process_insight(cur, qdrant, "t", "a", "u", insight, "m", 0)

    cur.execute.assert_not_called()


@pytest.mark.unit
def test_high_importance_add_is_not_skipped(monkeypatch):
    import main as mc_main

    cur, qdrant = MagicMock(), MagicMock()
    monkeypatch.setattr(mc_main, "process_semantic_insight", MagicMock())
    insight = {"memory_type": "semantic", "action": "add", "importance_score": 0.8,
               "content": "Tôi thích báo cáo PDF", "domain": "user_pref"}

    mc_main.process_insight(cur, qdrant, "t", "a", "u", insight, "m", 0)

    mc_main.process_semantic_insight.assert_called_once()


@pytest.mark.unit
def test_low_importance_delete_bypasses_the_gate():
    """DELETE is a direct command, not an insight being judged for
    storage-worthiness — must not be silently dropped just because the LLM
    assigned it a low importance_score."""
    import main as mc_main

    cur, qdrant = MagicMock(), MagicMock()
    qdrant.search.return_value = []
    insight = {"memory_type": "semantic", "action": "delete", "importance_score": 0.1,
               "fact_key": "preferred_report_format", "domain": "user_pref"}

    mc_main.process_insight(cur, qdrant, "t", "a", "u", insight, "m", 0)

    cur.execute.assert_called_once()
    assert "deleted_at" in cur.execute.call_args[0][0]


@pytest.mark.unit
def test_missing_importance_score_defaults_to_neutral_and_is_not_gated():
    """No importance_score at all (LLM omitted it) should default to 0.5 —
    at the threshold, not below it — same default used elsewhere in this
    module for the actual stored importance column."""
    import main as mc_main

    assert mc_main.MIN_IMPORTANCE_TO_STORE == 0.5

    cur, qdrant = MagicMock(), MagicMock()
    qdrant.search.return_value = []
    insight = {"memory_type": "episodic_future", "action": "add", "content": "nhắc tôi",
               "execution_time": "2026-07-01T08:00:00Z"}

    mc_main.process_insight(cur, qdrant, "t", "a", "u", insight, "m", 0)

    cur.execute.assert_called_once()
