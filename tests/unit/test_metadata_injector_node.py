"""Unit tests for metadata_injector_node
(services/workflow-runtime/nodes/context/metadata_injector.py).

The node's only job is copying the already-parsed `injected_metadata` dict
from state["config"] (parsed by config_loader._extract_node_config, see
test_config_loader_nodes.py::TestMetadataInjectorNodeConfig) into state, so
planner_node/responder_node can read it without reaching into state["config"]
themselves."""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "workflow-runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


async def test_copies_injected_metadata_from_config():
    from nodes.context.metadata_injector import metadata_injector_node

    state = {"config": {"injected_metadata": {"locale": "vi-VN", "tenant_policy": "no medical advice"}}}
    result = await metadata_injector_node(state)

    assert result == {"injected_metadata": {"locale": "vi-VN", "tenant_policy": "no medical advice"}}


async def test_missing_injected_metadata_in_config_returns_empty_dict():
    from nodes.context.metadata_injector import metadata_injector_node

    result = await metadata_injector_node({"config": {}})

    assert result == {"injected_metadata": {}}


async def test_missing_config_key_returns_empty_dict():
    from nodes.context.metadata_injector import metadata_injector_node

    result = await metadata_injector_node({})

    assert result == {"injected_metadata": {}}
