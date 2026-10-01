"""resolve_snowflake_password (PR review gap — see
docs/Sandbox_SchemaCrawler_Implementation.md §8.9/§9): a canvas node's
snowflakeSecretId must resolve to a real password via OpenBao (read-only
AppRole), never trust a raw snowflakePassword value out of Mongo. Postgres
(secrets_vault lookup) and hvac (vault read) are both mocked — no real
OpenBao/Postgres needed for this resolution logic.
"""
from __future__ import annotations

import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from ._paths import RUNTIME_LAYER_DIRS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")
TENANT_ID = "11111111-1111-1111-1111-111111111111"
SECRET_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (*RUNTIME_LAYER_DIRS,) if p not in sys.path]
    sys.path[:0] = added
    sys.modules.pop("warehouse_secrets", None)
    yield
    sys.modules.pop("warehouse_secrets", None)
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


class _FakeAsyncSessionCtx:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def _fake_pg_client(*, connected=True, row=None):
    client = MagicMock()
    client.is_connected.return_value = connected
    session = MagicMock()
    session.scalar = AsyncMock(return_value=row)
    client.get_client.return_value = _FakeAsyncSessionCtx(session)
    return client


def _patch_pg_client(monkeypatch, fake_client):
    import libs.database_connector.postgres_connector as pg_mod
    monkeypatch.setattr(pg_mod, "client", fake_client)


@pytest.mark.unit
async def test_returns_none_when_postgres_not_connected(monkeypatch):
    import warehouse_secrets as ws

    _patch_pg_client(monkeypatch, _fake_pg_client(connected=False))

    result = await ws.resolve_snowflake_password(TENANT_ID, SECRET_ID)

    assert result is None


@pytest.mark.unit
async def test_returns_none_when_secret_not_found(monkeypatch):
    import warehouse_secrets as ws

    _patch_pg_client(monkeypatch, _fake_pg_client(row=None))

    result = await ws.resolve_snowflake_password(TENANT_ID, SECRET_ID)

    assert result is None


@pytest.mark.unit
async def test_returns_none_when_secret_inactive(monkeypatch):
    import warehouse_secrets as ws

    row = SimpleNamespace(key_name="snowflake-acme", key_type="WAREHOUSE_PASSWORD", is_active=False)
    _patch_pg_client(monkeypatch, _fake_pg_client(row=row))

    result = await ws.resolve_snowflake_password(TENANT_ID, SECRET_ID)

    assert result is None


@pytest.mark.unit
async def test_returns_none_when_secret_wrong_key_type(monkeypatch):
    import warehouse_secrets as ws

    row = SimpleNamespace(key_name="some-other-key", key_type="BEARER_TOKEN", is_active=True)
    _patch_pg_client(monkeypatch, _fake_pg_client(row=row))

    result = await ws.resolve_snowflake_password(TENANT_ID, SECRET_ID)

    assert result is None


@pytest.mark.unit
async def test_returns_none_when_vault_not_configured(monkeypatch):
    import warehouse_secrets as ws

    monkeypatch.setattr(ws, "OPENBAO_ROLE_ID", "")
    monkeypatch.setattr(ws, "OPENBAO_SECRET_ID", "")
    row = SimpleNamespace(key_name="snowflake-acme", key_type="WAREHOUSE_PASSWORD", is_active=True)
    _patch_pg_client(monkeypatch, _fake_pg_client(row=row))

    result = await ws.resolve_snowflake_password(TENANT_ID, SECRET_ID)

    assert result is None


@pytest.mark.unit
async def test_resolves_password_from_vault(monkeypatch):
    import warehouse_secrets as ws

    monkeypatch.setattr(ws, "OPENBAO_ROLE_ID", "role-1")
    monkeypatch.setattr(ws, "OPENBAO_SECRET_ID", "secret-id-1")
    row = SimpleNamespace(key_name="snowflake-acme", key_type="WAREHOUSE_PASSWORD", is_active=True)
    _patch_pg_client(monkeypatch, _fake_pg_client(row=row))

    fake_vault = MagicMock()
    fake_vault.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": {"value": "s3cr3t-pw"}}}
    monkeypatch.setattr(ws, "_get_vault", lambda: fake_vault)

    result = await ws.resolve_snowflake_password(TENANT_ID, SECRET_ID)

    assert result == "s3cr3t-pw"
    fake_vault.secrets.kv.v2.read_secret_version.assert_called_once_with(
        mount_point="secret", path=f"tenants/{TENANT_ID}/snowflake-acme", raise_on_deleted_version=True,
    )


@pytest.mark.unit
async def test_returns_none_when_vault_read_raises(monkeypatch):
    import warehouse_secrets as ws

    monkeypatch.setattr(ws, "OPENBAO_ROLE_ID", "role-1")
    monkeypatch.setattr(ws, "OPENBAO_SECRET_ID", "secret-id-1")
    row = SimpleNamespace(key_name="snowflake-acme", key_type="WAREHOUSE_PASSWORD", is_active=True)
    _patch_pg_client(monkeypatch, _fake_pg_client(row=row))

    fake_vault = MagicMock()
    fake_vault.secrets.kv.v2.read_secret_version.side_effect = RuntimeError("vault unreachable")
    monkeypatch.setattr(ws, "_get_vault", lambda: fake_vault)

    result = await ws.resolve_snowflake_password(TENANT_ID, SECRET_ID)

    assert result is None


@pytest.mark.unit
async def test_resolve_db_connector_password_uses_its_own_key_type(monkeypatch):
    """resolve_db_connector_password must only accept DB_CONNECTOR_PASSWORD
    rows — a WAREHOUSE_PASSWORD secret_id (or any other key_type) must never
    resolve through this function, and vice versa (test below)."""
    import warehouse_secrets as ws

    monkeypatch.setattr(ws, "OPENBAO_ROLE_ID", "role-1")
    monkeypatch.setattr(ws, "OPENBAO_SECRET_ID", "secret-id-1")
    row = SimpleNamespace(key_name="db-connector-sales", key_type="DB_CONNECTOR_PASSWORD", is_active=True)
    _patch_pg_client(monkeypatch, _fake_pg_client(row=row))

    fake_vault = MagicMock()
    fake_vault.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": {"value": "db-pw"}}}
    monkeypatch.setattr(ws, "_get_vault", lambda: fake_vault)

    result = await ws.resolve_db_connector_password(TENANT_ID, SECRET_ID)

    assert result == "db-pw"


@pytest.mark.unit
async def test_resolve_db_connector_password_rejects_warehouse_key_type(monkeypatch):
    import warehouse_secrets as ws

    monkeypatch.setattr(ws, "OPENBAO_ROLE_ID", "role-1")
    monkeypatch.setattr(ws, "OPENBAO_SECRET_ID", "secret-id-1")
    row = SimpleNamespace(key_name="snowflake-acme", key_type="WAREHOUSE_PASSWORD", is_active=True)
    _patch_pg_client(monkeypatch, _fake_pg_client(row=row))

    result = await ws.resolve_db_connector_password(TENANT_ID, SECRET_ID)

    assert result is None


@pytest.mark.unit
async def test_resolve_snowflake_password_rejects_db_connector_key_type(monkeypatch):
    import warehouse_secrets as ws

    row = SimpleNamespace(key_name="db-connector-sales", key_type="DB_CONNECTOR_PASSWORD", is_active=True)
    _patch_pg_client(monkeypatch, _fake_pg_client(row=row))

    result = await ws.resolve_snowflake_password(TENANT_ID, SECRET_ID)

    assert result is None


@pytest.mark.unit
def test_get_vault_returns_none_without_approle_credentials(monkeypatch):
    import warehouse_secrets as ws

    monkeypatch.setattr(ws, "OPENBAO_ROLE_ID", "")
    monkeypatch.setattr(ws, "OPENBAO_SECRET_ID", "")
    monkeypatch.setattr(ws, "_vault_client", None)

    assert ws._get_vault() is None


@pytest.mark.unit
def test_get_vault_logs_in_and_caches(monkeypatch):
    import warehouse_secrets as ws

    monkeypatch.setattr(ws, "OPENBAO_ROLE_ID", "role-1")
    monkeypatch.setattr(ws, "OPENBAO_SECRET_ID", "secret-id-1")
    monkeypatch.setattr(ws, "_vault_client", None)

    fake_client = MagicMock()
    fake_client.is_authenticated.return_value = True
    client_cls = MagicMock(return_value=fake_client)
    monkeypatch.setattr(ws.hvac, "Client", client_cls)

    first = ws._get_vault()
    second = ws._get_vault()

    assert first is fake_client
    assert second is fake_client
    client_cls.assert_called_once()
    fake_client.auth.approle.login.assert_called_once_with(role_id="role-1", secret_id="secret-id-1")
