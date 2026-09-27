"""cache_get_schema/cache_set_schema (backend/services/workflow_runtime/redis_cache.py)
— DB Connector's information_schema introspection cache. Key is deliberately
(tenant_id, host, port, database), NOT the user-chosen connection name or
user/password (see plan: 2 differently-named connections to the same physical
DB should share a cache entry; renaming a connection shouldn't invalidate it).
Fail-open (return None / no-op) when Redis is unavailable, matching the
existing cache_get_messages/cache_set_messages pattern in this module."""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import AsyncMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow_runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    sys.modules.pop("redis_cache", None)
    yield
    sys.modules.pop("redis_cache", None)
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def test_schema_key_uses_tenant_host_port_database_not_name():
    import redis_cache as rc

    key_a = rc._schema_key("tenant-1", "db.internal", 5432, "salesdb")
    key_b = rc._schema_key("tenant-1", "db.internal", 5432, "salesdb")
    assert key_a == key_b

    # Different physical DB → different key.
    assert rc._schema_key("tenant-1", "db.internal", 5432, "otherdb") != key_a


async def test_cache_get_schema_returns_none_when_client_unavailable(monkeypatch):
    import redis_cache as rc
    monkeypatch.setattr(rc, "_client", None)

    result = await rc.cache_get_schema("tenant-1", "h", 5432, "d")

    assert result is None


async def test_cache_set_schema_is_noop_when_client_unavailable(monkeypatch):
    import redis_cache as rc
    monkeypatch.setattr(rc, "_client", None)

    # Should not raise even though there's no client to write to.
    await rc.cache_set_schema("tenant-1", "h", 5432, "d", "orders(id int)")


async def test_cache_get_schema_reads_from_correct_key(monkeypatch):
    import redis_cache as rc
    fake_client = AsyncMock()
    fake_client.get.return_value = "orders(id int)"
    monkeypatch.setattr(rc, "_client", fake_client)

    result = await rc.cache_get_schema("tenant-1", "h", 5432, "d")

    assert result == "orders(id int)"
    fake_client.get.assert_called_once_with(rc._schema_key("tenant-1", "h", 5432, "d"))


async def test_cache_set_schema_writes_with_ttl(monkeypatch):
    import redis_cache as rc
    fake_client = AsyncMock()
    monkeypatch.setattr(rc, "_client", fake_client)

    await rc.cache_set_schema("tenant-1", "h", 5432, "d", "orders(id int)")

    fake_client.set.assert_called_once_with(
        rc._schema_key("tenant-1", "h", 5432, "d"), "orders(id int)", ex=rc._SCHEMA_TTL,
    )


async def test_cache_get_schema_fails_open_on_exception(monkeypatch):
    import redis_cache as rc
    fake_client = AsyncMock()
    fake_client.get.side_effect = RuntimeError("connection reset")
    monkeypatch.setattr(rc, "_client", fake_client)

    result = await rc.cache_get_schema("tenant-1", "h", 5432, "d")

    assert result is None


async def test_cache_set_schema_fails_open_on_exception(monkeypatch):
    import redis_cache as rc
    fake_client = AsyncMock()
    fake_client.set.side_effect = RuntimeError("connection reset")
    monkeypatch.setattr(rc, "_client", fake_client)

    # Should not raise.
    await rc.cache_set_schema("tenant-1", "h", 5432, "d", "orders(id int)")


async def test_two_names_pointing_to_same_physical_db_share_cache(monkeypatch):
    """Same host/port/database, different connection 'name' isn't even part
    of the key — so 2 differently-named connections naturally share 1 cache
    entry, and renaming a connection doesn't invalidate its cached schema."""
    import redis_cache as rc
    fake_client = AsyncMock()
    fake_client.get.return_value = "orders(id int)"
    monkeypatch.setattr(rc, "_client", fake_client)

    result_as_sales = await rc.cache_get_schema("tenant-1", "db.internal", 5432, "salesdb")
    result_as_renamed = await rc.cache_get_schema("tenant-1", "db.internal", 5432, "salesdb")

    assert result_as_sales == result_as_renamed == "orders(id int)"
    assert fake_client.get.call_count == 2
    assert fake_client.get.call_args_list[0] == fake_client.get.call_args_list[1]
