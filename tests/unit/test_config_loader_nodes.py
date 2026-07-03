"""Unit tests for workflow-runtime config_loader pure helpers.

`_extract_node_config` translates the visual flow canvas (per-node settings)
into the flat runtime config the graph reads. Getting this wrong means a user's
canvas tweak (top_k, temperature, reranker model) silently doesn't take effect,
which is exactly the class of bug that needs a regression test.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock

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


class TestSnowflakeNodeConfig:
    """PR review gap fix — _extract_node_config must never read a raw
    snowflakePassword value; only snowflakeSecretId (resolved later against
    OpenBao by _resolve_warehouse_secret, see TestResolveWarehouseSecret)."""

    def test_secret_id_carried_through_password_left_empty(self) -> None:
        cfg = config_loader._extract_node_config([_node(
            "tool_executor", snowflakeAccount="acct1", snowflakeUser="u1",
            snowflakeSecretId="sec-123",
        )])
        connection = cfg["tool_config"]["execute_sql"]["connection"]
        assert connection["secret_id"] == "sec-123"
        assert connection["password"] == ""

    def test_no_secret_id_leaves_empty_secret_id_and_password(self) -> None:
        cfg = config_loader._extract_node_config([_node("tool_executor", snowflakeAccount="acct1")])
        connection = cfg["tool_config"]["execute_sql"]["connection"]
        assert connection["secret_id"] == ""
        assert connection["password"] == ""

    def test_legacy_snowflake_password_field_is_ignored(self) -> None:
        """Even if a stray snowflakePassword value is present in the node
        config (e.g. hand-edited Mongo doc), it must never flow through —
        this is the exact plaintext-passthrough the gap fix closes."""
        cfg = config_loader._extract_node_config([_node(
            "tool_executor", snowflakeAccount="acct1", snowflakePassword="plaintext-leak",
        )])
        connection = cfg["tool_config"]["execute_sql"]["connection"]
        assert connection["password"] == ""
        assert "plaintext-leak" not in str(connection)


class TestResolveWarehouseSecret:
    """_resolve_warehouse_secret — mutates node_cfg in place, resolving
    execute_sql's secret_id via warehouse_secrets.resolve_snowflake_password
    (mocked via sys.modules — see warehouse_secrets.py's own unit tests in
    test_warehouse_secrets.py for the resolution logic itself)."""

    def _stub_warehouse_secrets(self, monkeypatch, resolve_mock) -> None:
        fake_module = types.ModuleType("warehouse_secrets")
        fake_module.resolve_snowflake_password = resolve_mock
        monkeypatch.setitem(sys.modules, "warehouse_secrets", fake_module)

    async def test_noop_when_no_execute_sql_node(self) -> None:
        node_cfg: dict = {}
        await config_loader._resolve_warehouse_secret(node_cfg, "tenant-1")
        assert node_cfg == {}

    async def test_noop_when_no_secret_id_set(self, monkeypatch) -> None:
        resolve_spy = AsyncMock(return_value="should-not-be-called")
        self._stub_warehouse_secrets(monkeypatch, resolve_spy)
        node_cfg = {"tool_config": {"execute_sql": {"connection": {"account": "acct1", "password": ""}}}}

        await config_loader._resolve_warehouse_secret(node_cfg, "tenant-1")

        resolve_spy.assert_not_called()
        assert node_cfg["tool_config"]["execute_sql"]["connection"] == {"account": "acct1", "password": ""}

    async def test_resolves_and_strips_secret_id(self, monkeypatch) -> None:
        resolve_spy = AsyncMock(return_value="real-password")
        self._stub_warehouse_secrets(monkeypatch, resolve_spy)
        node_cfg = {"tool_config": {"execute_sql": {"connection": {
            "account": "acct1", "secret_id": "sec-1", "password": "",
        }}}}

        await config_loader._resolve_warehouse_secret(node_cfg, "tenant-1")

        connection = node_cfg["tool_config"]["execute_sql"]["connection"]
        assert connection == {"account": "acct1", "password": "real-password"}
        resolve_spy.assert_called_once_with("tenant-1", "sec-1")

    async def test_resolution_failure_leaves_password_empty(self, monkeypatch) -> None:
        resolve_spy = AsyncMock(return_value=None)
        self._stub_warehouse_secrets(monkeypatch, resolve_spy)
        node_cfg = {"tool_config": {"execute_sql": {"connection": {
            "account": "acct1", "secret_id": "sec-1", "password": "",
        }}}}

        await config_loader._resolve_warehouse_secret(node_cfg, "tenant-1")

        connection = node_cfg["tool_config"]["execute_sql"]["connection"]
        assert connection == {"account": "acct1", "password": ""}

    async def test_secret_id_stripped_even_without_tenant_id(self, monkeypatch) -> None:
        resolve_spy = AsyncMock(return_value="real-password")
        self._stub_warehouse_secrets(monkeypatch, resolve_spy)
        node_cfg = {"tool_config": {"execute_sql": {"connection": {
            "account": "acct1", "secret_id": "sec-1", "password": "",
        }}}}

        await config_loader._resolve_warehouse_secret(node_cfg, None)

        resolve_spy.assert_not_called()
        connection = node_cfg["tool_config"]["execute_sql"]["connection"]
        assert "secret_id" not in connection
