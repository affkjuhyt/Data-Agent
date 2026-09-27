"""Unit tests for workflow_runtime config_loader pure helpers.

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

from .._loader import SERVICES, load_module_from_path

pytestmark = pytest.mark.unit

# workflow_runtime is a hyphenated dir; config_loader only imports dotenv +
# sqlalchemy at module scope (its DB/Mongo imports are lazy, inside functions).
config_loader = load_module_from_path(
    "config_loader",
    SERVICES / "workflow_runtime" / "config_loader.py",
    extra_syspath=[SERVICES / "workflow_runtime"],
)


def _node(node_type: str, **data) -> dict:
    return {"data": {"nodeType": node_type, **data}}


class TestExtractNodeConfig:
    def test_empty_flow_returns_empty(self) -> None:
        assert config_loader._extract_node_config([]) == {}

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
        # Chỉ set "top_n" trên canvas (không có "model_id") — cfg chỉ nên có
        # đúng key tương ứng field đã nhập, không tự điền field còn lại.
        cfg = config_loader._extract_node_config([_node("reranker", top_n=3)])
        assert cfg == {"reranker_top_n": 3}

    def test_unknown_node_type_ignored(self) -> None:
        assert config_loader._extract_node_config([_node("mystery", foo=1)]) == {}

    def test_multiple_nodes_merge(self) -> None:
        cfg = config_loader._extract_node_config(
            [_node("rrf_ranking", top_n=4), _node("responder", max_tokens=100)]
        )
        assert cfg == {"rrf_top_n": 4, "responder_max_tokens": 100}

    def test_falls_back_to_type_and_config_keys(self) -> None:
        # Node shape without `data.nodeType`: type + config keys instead.
        node = {"type": "rrf_ranking", "config": {"top_n": 9}}
        assert config_loader._extract_node_config([node]) == {"rrf_top_n": 9}


class TestMetadataInjectorNodeConfig:
    def test_parses_key_value_pairs(self) -> None:
        cfg = config_loader._extract_node_config(
            [_node("metadata_injector", metadataKv="locale=vi-VN,tenant_policy=no medical advice")]
        )
        assert cfg == {"injected_metadata": {"locale": "vi-VN", "tenant_policy": "no medical advice"}}

    def test_missing_metadata_kv_returns_empty_dict(self) -> None:
        cfg = config_loader._extract_node_config([_node("metadata_injector")])
        assert cfg == {"injected_metadata": {}}

    def test_malformed_pairs_are_skipped(self) -> None:
        cfg = config_loader._extract_node_config(
            [_node("metadata_injector", metadataKv="no_equals_sign,=empty_key,ok=1")]
        )
        assert cfg == {"injected_metadata": {"ok": "1"}}

    def test_whitespace_around_key_and_value_is_stripped(self) -> None:
        cfg = config_loader._extract_node_config(
            [_node("metadata_injector", metadataKv=" locale = vi-VN ")]
        )
        assert cfg == {"injected_metadata": {"locale": "vi-VN"}}


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


class TestDbConnectorNodeConfig:
    def test_parses_connections_into_dict_by_name(self) -> None:
        import json
        conns = [
            {"name": "Sales", "host": "h1", "port": 5432, "database": "salesdb", "user": "u1", "secretId": "sec-1", "note": "orders table"},
            {"name": "Warehouse", "host": "h2", "database": "whdb", "user": "u2", "secretId": "sec-2"},
        ]
        cfg = config_loader._extract_node_config([_node("db_connector", dbConnections=json.dumps(conns))])
        db_connections = cfg["tool_config"]["db_connections"]
        assert set(db_connections) == {"Sales", "Warehouse"}
        assert db_connections["Sales"] == {
            "host": "h1", "port": 5432, "database": "salesdb", "user": "u1",
            "secret_id": "sec-1", "password": "", "note": "orders table",
        }
        assert db_connections["Warehouse"]["port"] == 5432  # default when omitted

    def test_entries_missing_required_fields_are_skipped(self) -> None:
        import json
        conns = [{"name": "NoHost", "database": "db1"}, {"host": "h1", "database": "db1"}]
        cfg = config_loader._extract_node_config([_node("db_connector", dbConnections=json.dumps(conns))])
        assert cfg == {}

    def test_missing_db_connections_returns_empty(self) -> None:
        assert config_loader._extract_node_config([_node("db_connector")]) == {}

    def test_malformed_json_is_safe(self) -> None:
        cfg = config_loader._extract_node_config([_node("db_connector", dbConnections="not json")])
        assert cfg == {}

    def test_merges_tool_config_with_tool_executor_regardless_of_order(self) -> None:
        import json
        conns = [{"name": "Sales", "host": "h1", "database": "salesdb"}]
        nodes = [
            _node("db_connector", dbConnections=json.dumps(conns)),
            _node("tool_executor", allowedBuiltinTools=["web_search"], bashEnabled=True),
        ]
        cfg = config_loader._extract_node_config(nodes)
        assert "db_connections" in cfg["tool_config"]
        assert "bash" in cfg["tool_config"]


class TestResolveDbConnectorSecrets:
    """_resolve_db_connector_secrets — mutates node_cfg in place, resolving
    each named connection's secret_id via warehouse_secrets.resolve_db_connector_password
    (mocked via sys.modules, same pattern as TestResolveWarehouseSecret)."""

    def _stub_warehouse_secrets(self, monkeypatch, resolve_mock) -> None:
        fake_module = types.ModuleType("warehouse_secrets")
        fake_module.resolve_db_connector_password = resolve_mock
        monkeypatch.setitem(sys.modules, "warehouse_secrets", fake_module)

    async def test_noop_when_no_db_connector_node(self) -> None:
        node_cfg: dict = {}
        await config_loader._resolve_db_connector_secrets(node_cfg, "tenant-1")
        assert node_cfg == {}

    async def test_resolves_each_connection_independently(self, monkeypatch) -> None:
        async def fake_resolve(tenant_id, secret_id):
            return {"sec-1": "pw-sales", "sec-2": "pw-wh"}.get(secret_id)
        self._stub_warehouse_secrets(monkeypatch, fake_resolve)
        node_cfg = {"tool_config": {"db_connections": {
            "Sales":     {"host": "h1", "secret_id": "sec-1", "password": ""},
            "Warehouse": {"host": "h2", "secret_id": "sec-2", "password": ""},
        }}}

        await config_loader._resolve_db_connector_secrets(node_cfg, "tenant-1")

        db_connections = node_cfg["tool_config"]["db_connections"]
        assert db_connections["Sales"]["password"] == "pw-sales"
        assert db_connections["Warehouse"]["password"] == "pw-wh"
        assert "secret_id" not in db_connections["Sales"]
        assert "secret_id" not in db_connections["Warehouse"]

    async def test_resolution_failure_leaves_password_empty(self, monkeypatch) -> None:
        self._stub_warehouse_secrets(monkeypatch, AsyncMock(return_value=None))
        node_cfg = {"tool_config": {"db_connections": {"Sales": {"host": "h1", "secret_id": "sec-1", "password": ""}}}}

        await config_loader._resolve_db_connector_secrets(node_cfg, "tenant-1")

        assert node_cfg["tool_config"]["db_connections"]["Sales"]["password"] == ""

    async def test_secret_id_stripped_even_without_tenant_id(self, monkeypatch) -> None:
        resolve_spy = AsyncMock(return_value="pw")
        self._stub_warehouse_secrets(monkeypatch, resolve_spy)
        node_cfg = {"tool_config": {"db_connections": {"Sales": {"host": "h1", "secret_id": "sec-1", "password": ""}}}}

        await config_loader._resolve_db_connector_secrets(node_cfg, None)

        resolve_spy.assert_not_called()
        assert "secret_id" not in node_cfg["tool_config"]["db_connections"]["Sales"]


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
