"""Sessions opened during a request must carry the caller's tenant.

kb-backend has ~80 `get_client()` calls and almost none passed a tenant. Under a
NOBYPASSRLS role that is not an error — the tenant_isolation policy compares
against NULL and returns zero rows, so the service answers every question with
"nothing found". These tests assert the setting is actually issued, because that
is the difference between RLS protecting the data and RLS emptying it.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest

from libs.database_connector.postgres_connector import (
    PostgresClient,
    current_tenant,
    set_current_tenant,
)

pytestmark = pytest.mark.unit

TENANT = "a0000000-0000-0000-0000-000000000001"
OTHER_TENANT = "b0000000-0000-0000-0000-000000000002"


class _StubSession:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self.log = log

    async def execute(self, statement: Any, params: Any = None) -> Any:
        self.log.append((str(statement), params))
        return None

    async def rollback(self) -> None:
        pass

    async def __aenter__(self) -> "_StubSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        pass


def _client_with_log() -> tuple[PostgresClient, list[tuple[str, Any]]]:
    log: list[tuple[str, Any]] = []
    client = PostgresClient()
    client._session_factory = lambda: _StubSession(log)   # type: ignore[attr-defined]
    client._bypasses_rls = False                          # type: ignore[attr-defined]
    return client, log


def _tenant_settings(log: list[tuple[str, Any]]) -> list[Any]:
    return [params for statement, params in log if "set_config" in statement and "app.tenant_id" in statement]


@pytest.fixture(autouse=True)
def _clear_ambient_tenant():
    set_current_tenant(None)
    yield
    set_current_tenant(None)


class TestAmbientTenantScopesSessions:
    def test_a_bare_get_client_uses_the_ambient_tenant(self):
        client, log = _client_with_log()
        set_current_tenant(TENANT)

        async def run():
            async with client.get_client():
                pass

        asyncio.run(run())
        assert _tenant_settings(log) == [{"tid": TENANT}]

    def test_an_explicit_tenant_still_wins(self):
        client, log = _client_with_log()
        set_current_tenant(TENANT)

        async def run():
            async with client.get_client(tenant_id=OTHER_TENANT):
                pass

        asyncio.run(run())
        assert _tenant_settings(log) == [{"tid": OTHER_TENANT}]

    def test_without_a_request_context_the_session_is_unscoped(self):
        """Workers and startup have no caller; they must not invent a tenant."""
        client, log = _client_with_log()

        async def run():
            async with client.get_client():
                pass

        asyncio.run(run())
        assert _tenant_settings(log) == []

    def test_the_platform_client_is_never_scoped(self):
        """RolePermissions and information_schema have no tenant_id, so a
        scoped session there sets a value no policy reads."""
        client, log = _client_with_log()
        set_current_tenant(TENANT)

        async def run():
            async with client.get_platform_client():
                pass

        asyncio.run(run())
        assert _tenant_settings(log) == []

    def test_one_request_does_not_leak_its_tenant_into_another(self):
        client, log = _client_with_log()

        async def request(tenant: str) -> None:
            set_current_tenant(tenant)
            async with client.get_client():
                pass

        async def run():
            # asyncio.gather copies the context per task, which is what keeps
            # concurrent requests from overwriting each other's tenant.
            await asyncio.gather(request(TENANT), request(OTHER_TENANT))

        asyncio.run(run())
        assert sorted(p["tid"] for p in _tenant_settings(log)) == sorted([TENANT, OTHER_TENANT])


class TestAuthenticateSetsTheTenant:
    def test_a_verified_caller_becomes_the_ambient_tenant(self, monkeypatch):
        monkeypatch.setenv("KB_GATEWAY_KEY", "gateway-key")
        import importlib
        import services.kb_backend.dependencies.auth as auth_module
        auth = importlib.reload(auth_module)

        async def handle_request() -> tuple[dict[str, Any], str | None]:
            context = await auth.authenticate(
                x_kong_verified="true", x_gateway_key="gateway-key",
                x_user_id="user-1", x_user_name="", x_tenant_id=TENANT,
                x_user_roles="platform-admin", x_role_id="", x_service_assertion="",
            )
            # Read it where a route would: same task, after the dependency ran.
            return context, current_tenant()

        context, ambient = asyncio.run(handle_request())

        assert context["tenant_id"] == TENANT
        assert ambient == TENANT

    def test_authenticate_is_async_so_the_contextvar_survives(self):
        """A sync dependency runs in a threadpool, where a ContextVar set on the
        worker thread never reaches the request's own task."""
        import inspect
        from services.kb_backend.dependencies.auth import authenticate

        assert inspect.iscoroutinefunction(authenticate)


class TestInsertDoesNotReadAfterCommit:
    def test_no_refresh_follows_the_commit(self):
        """set_config(..., true) is transaction-local, so a refresh after commit
        runs with no tenant and RLS hides the row that was just written."""
        import pathlib
        source = (
            pathlib.Path(__file__).resolve().parents[2]
            / "backend" / "libs" / "database_connector" / "postgres_connector.py"
        ).read_text(encoding="utf-8")
        assert "session.refresh(prep.instance)" not in source
