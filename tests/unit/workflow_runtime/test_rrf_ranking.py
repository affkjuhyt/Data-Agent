"""rrf_ranking_node (backend/services/workflow_runtime/application/nodes/model_harness/rrf_ranking.py)
— merges the text sources kb_chunks/mcp_results/tool_results into one ranked
list. Query results are not ranked at all (result_sets channel, R0-1): that
they never enter this list is pinned in test_result_set_channel.py."""
from __future__ import annotations

import pathlib
import sys

import pytest

from ._paths import RUNTIME_LAYER_DIRS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (*RUNTIME_LAYER_DIRS,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def test_empty_everything_returns_empty_results():
    from nodes.model_harness.rrf_ranking import rrf_ranking_node

    result = rrf_ranking_node({"kb_chunks": [], "mcp_results": [], "tool_results": [], "config": {}})

    assert result == {"rrf_results": []}


def test_sources_merge_and_are_labelled():
    from nodes.model_harness.rrf_ranking import rrf_ranking_node

    state = {
        "kb_chunks":    [{"id": "kb-1"}],
        "mcp_results":  [{"id": "mcp-1"}],
        "tool_results": [{"id": "tool-1"}],
        "config": {},
    }
    result = rrf_ranking_node(state)

    assert {r["id"]: r["rrf_sources"] for r in result["rrf_results"]} == {
        "kb-1": ["kb"], "mcp-1": ["mcp"], "tool-1": ["tool"],
    }
    assert result["rrf_results"][0]["rrf_rank"] == 1


def test_respects_rrf_top_n_config():
    from nodes.model_harness.rrf_ranking import rrf_ranking_node

    state = {
        "kb_chunks": [{"id": f"kb-{i}"} for i in range(5)], "mcp_results": [], "tool_results": [],
        "config": {"rrf_top_n": 2},
    }
    result = rrf_ranking_node(state)

    assert len(result["rrf_results"]) == 2
