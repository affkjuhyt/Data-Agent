"""Unit tests for workflow-runtime config_loader pure helpers.

`_extract_node_config` translates the visual flow canvas (per-node settings)
into the flat runtime config the graph reads. Getting this wrong means a user's
canvas tweak (top_k, temperature, reranker model) silently doesn't take effect,
which is exactly the class of bug that needs a regression test.
"""
from __future__ import annotations

import pytest

from ._loader import SERVICES, load_module_from_path

pytestmark = pytest.mark.unit

# workflow-runtime is a hyphenated dir; config_loader only imports dotenv +
# sqlalchemy at module scope (its DB/Mongo imports are lazy, inside functions).
config_loader = load_module_from_path(
    "config_loader",
    SERVICES / "workflow-runtime" / "config_loader.py",
    extra_syspath=[SERVICES / "workflow-runtime"],
)


def _node(node_type: str, **data) -> dict:
    return {"data": {"nodeType": node_type, **data}}


class TestExtractNodeConfig:
    def test_empty_flow_returns_empty(self) -> None:
        assert config_loader._extract_node_config([]) == {}

    def test_kb_search_overrides(self) -> None:
        cfg = config_loader._extract_node_config(
            [_node("kb_search", mode="vector", top_k=7, max_depth=2)]
        )
        assert cfg == {"kb_mode": "vector", "top_k": 7, "max_depth": 2}

    def test_rrf_ranking_maps_top_n(self) -> None:
        cfg = config_loader._extract_node_config([_node("rrf_ranking", top_n=15)])
        assert cfg == {"rrf_top_n": 15}

    def test_reranker_maps_fields(self) -> None:
        cfg = config_loader._extract_node_config(
            [_node("reranker", top_n=3, model_id="cohere/rerank")]
        )
        assert cfg == {"reranker_top_n": 3, "reranker_model": "cohere/rerank"}

    def test_responder_overrides(self) -> None:
        cfg = config_loader._extract_node_config(
            [_node("responder", temperature=0.9, max_tokens=512)]
        )
        assert cfg == {"responder_temperature": 0.9, "responder_max_tokens": 512}

    def test_partial_fields_only_set_present_keys(self) -> None:
        cfg = config_loader._extract_node_config([_node("kb_search", top_k=5)])
        assert cfg == {"top_k": 5}

    def test_unknown_node_type_ignored(self) -> None:
        assert config_loader._extract_node_config([_node("mystery", foo=1)]) == {}

    def test_multiple_nodes_merge(self) -> None:
        cfg = config_loader._extract_node_config(
            [_node("kb_search", top_k=4), _node("responder", max_tokens=100)]
        )
        assert cfg == {"top_k": 4, "responder_max_tokens": 100}

    def test_falls_back_to_type_and_config_keys(self) -> None:
        # Node shape without `data.nodeType`: type + config keys instead.
        node = {"type": "rrf_ranking", "config": {"top_n": 9}}
        assert config_loader._extract_node_config([node]) == {"rrf_top_n": 9}


class TestGuardrailDefaults:
    def test_defaults_pull_from_dev_config(self) -> None:
        defaults = config_loader._extract_guardrail_defaults()
        assert defaults["guardrail_model"] == config_loader.DEV_CONFIG["guardrail_model"]
        assert defaults["guardrail_id"] is None
        assert isinstance(defaults["guardrail_input_patterns"], list)
