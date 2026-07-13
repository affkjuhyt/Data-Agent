"""schema-crawler-worker's warehouse_secrets.py — sync counterpart of
services/workflow-runtime/warehouse_secrets.py (see
docs/Schema_Crawler_Risk_Remediation_Plan.md #1). The psycopg2 cursor and
hvac vault client are both mocked — no real OpenBao/Postgres needed for this
resolution logic, same technique as tests/unit/test_warehouse_secrets.py for
the async version.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "schema-crawler-worker")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    sys.modules.pop("warehouse_secrets", None)
    yield
    sys.modules.pop("warehouse_secrets", None)
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _fake_cursor(row=None):
    cur = MagicMock()
    cur.fetchone.return_value = row
    return cur


@pytest.mark.unit
def test_returns_none_when_secret_not_found():
    import warehouse_secrets as ws

    result = ws.resolve_snowflake_password(_fake_cursor(row=None), "tenant-1", "secret-1")

    assert result is None


@pytest.mark.unit
def test_returns_none_when_secret_inactive():
    import warehouse_secrets as ws

    row = ("snowflake-acme", "WAREHOUSE_PASSWORD", False)
    result = ws.resolve_snowflake_password(_fake_cursor(row=row), "tenant-1", "secret-1")

    assert result is None


@pytest.mark.unit
def test_returns_none_when_secret_wrong_key_type():
    import warehouse_secrets as ws

    row = ("some-other-key", "BEARER_TOKEN", True)
    result = ws.resolve_snowflake_password(_fake_cursor(row=row), "tenant-1", "secret-1")

    assert result is None


@pytest.mark.unit
def test_returns_none_when_vault_not_configured(monkeypatch):
    import warehouse_secrets as ws

    monkeypatch.setattr(ws, "OPENBAO_ROLE_ID", "")
    monkeypatch.setattr(ws, "OPENBAO_SECRET_ID", "")
    row = ("snowflake-acme", "WAREHOUSE_PASSWORD", True)

    result = ws.resolve_snowflake_password(_fake_cursor(row=row), "tenant-1", "secret-1")

    assert result is None


@pytest.mark.unit
def test_resolves_password_from_vault(monkeypatch):
    import warehouse_secrets as ws

    monkeypatch.setattr(ws, "OPENBAO_ROLE_ID", "role-1")
    monkeypatch.setattr(ws, "OPENBAO_SECRET_ID", "secret-id-1")
    row = ("snowflake-acme", "WAREHOUSE_PASSWORD", True)

    fake_vault = MagicMock()
    fake_vault.secrets.kv.v2.read_secret_version.return_value = {"data": {"data": {"value": "s3cr3t-pw"}}}
    monkeypatch.setattr(ws, "_get_vault", lambda: fake_vault)

    result = ws.resolve_snowflake_password(_fake_cursor(row=row), "tenant-1", "secret-1")

    assert result == "s3cr3t-pw"
    fake_vault.secrets.kv.v2.read_secret_version.assert_called_once_with(
        mount_point="secret", path="tenants/tenant-1/snowflake-acme", raise_on_deleted_version=True,
    )


@pytest.mark.unit
def test_returns_none_when_vault_read_raises(monkeypatch):
    import warehouse_secrets as ws

    monkeypatch.setattr(ws, "OPENBAO_ROLE_ID", "role-1")
    monkeypatch.setattr(ws, "OPENBAO_SECRET_ID", "secret-id-1")
    row = ("snowflake-acme", "WAREHOUSE_PASSWORD", True)

    fake_vault = MagicMock()
    fake_vault.secrets.kv.v2.read_secret_version.side_effect = RuntimeError("vault unreachable")
    monkeypatch.setattr(ws, "_get_vault", lambda: fake_vault)

    result = ws.resolve_snowflake_password(_fake_cursor(row=row), "tenant-1", "secret-1")

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


@pytest.mark.unit
def test_get_vault_passes_explicit_timeout(monkeypatch):
    """A slow/unreachable OpenBao must not hang the sequential crawl_all()
    loop indefinitely — see docs/Schema_Crawler_Risk_Remediation_Results.md
    §3.3 (AppRole token TTL means re-login recurs roughly hourly, not just
    once at startup)."""
    import warehouse_secrets as ws

    monkeypatch.setattr(ws, "OPENBAO_ROLE_ID", "role-1")
    monkeypatch.setattr(ws, "OPENBAO_SECRET_ID", "secret-id-1")
    monkeypatch.setattr(ws, "_vault_client", None)
    monkeypatch.setattr(ws, "OPENBAO_TIMEOUT_SECONDS", 7)

    fake_client = MagicMock()
    client_cls = MagicMock(return_value=fake_client)
    monkeypatch.setattr(ws.hvac, "Client", client_cls)

    ws._get_vault()

    client_cls.assert_called_once_with(url=ws.OPENBAO_ADDR, timeout=7)
