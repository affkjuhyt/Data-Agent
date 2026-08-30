"""rrf_ranking_node (backend/services/workflow-runtime/nodes/model_harness/rrf_ranking.py)
— merges kb_chunks/mcp_results/tool_results/db_results into one ranked list.
db_results is the 4th source (added for DB Connector) — must merge exactly
like the other 3, not require any special-casing downstream."""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow-runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def test_empty_everything_returns_empty_results():
    from nodes.model_harness.rrf_ranking import rrf_ranking_node

    result = rrf_ranking_node({"kb_chunks": [], "mcp_results": [], "tool_results": [], "db_results": [], "config": {}})

    assert result == {"rrf_results": []}


def test_missing_db_results_key_is_treated_as_empty():
    """Older state snapshots (before db_results existed) shouldn't crash this node."""
    from nodes.model_harness.rrf_ranking import rrf_ranking_node

    state = {"kb_chunks": [{"id": "a"}], "mcp_results": [], "tool_results": [], "config": {}}
    result = rrf_ranking_node(state)

    assert len(result["rrf_results"]) == 1


def test_db_results_merge_alongside_other_sources():
    from nodes.model_harness.rrf_ranking import rrf_ranking_node

    state = {
        "kb_chunks":    [{"id": "kb-1"}],
        "mcp_results":  [],
        "tool_results": [],
        "db_results":   [{"id": "db-1"}, {"id": "db-2"}],
        "config": {},
    }
    result = rrf_ranking_node(state)

    ids = {r["id"] for r in result["rrf_results"]}
    assert ids == {"kb-1", "db-1", "db-2"}
    db_1 = next(r for r in result["rrf_results"] if r["id"] == "db-1")
    assert "db" in db_1["rrf_sources"]


def test_db_only_result_ranked_correctly():
    from nodes.model_harness.rrf_ranking import rrf_ranking_node

    state = {
        "kb_chunks": [], "mcp_results": [], "tool_results": [],
        "db_results": [{"id": "db-1"}, {"id": "db-2"}, {"id": "db-3"}],
        "config": {},
    }
    result = rrf_ranking_node(state)

    assert len(result["rrf_results"]) == 3
    assert result["rrf_results"][0]["rrf_rank"] == 1


def test_respects_rrf_top_n_config():
    from nodes.model_harness.rrf_ranking import rrf_ranking_node

    state = {
        "kb_chunks": [], "mcp_results": [], "tool_results": [],
        "db_results": [{"id": f"db-{i}"} for i in range(5)],
        "config": {"rrf_top_n": 2},
    }
    result = rrf_ranking_node(state)

    assert len(result["rrf_results"]) == 2
