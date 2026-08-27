"""Authentication on the agent tier — orchestrator and workflow-runtime.

Both services previously had NO authentication of any kind: the orchestrator
read the tenant from a raw header and fell back to DEV_TENANT_ID, workflow-
runtime did the same, and both were published on host ports. Kong already had
routes and the aeroflow-jwks plugin for each (infra/kong/kong-setup.sh) — the
services just never checked that a request had been through it.

These tests pin the shared trust decision they now use, the orchestrator's
service-to-service leg to workflow-runtime, and the fact that the ports are no
longer published.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi import HTTPException

from libs.shared.auth import allowed_services, resolve_caller
from libs.shared.service_identity import mint_service_assertion

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.unit

SECRET = "test-shared-secret"
TENANT = "a0000000-0000-0000-0000-000000000001"
GATEWAY_KEY = "kong-proves-itself"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SERVICE_AUTH_SECRET", SECRET)
    monkeypatch.setenv("GATEWAY_KEY", GATEWAY_KEY)


def _resolve(*, accepted=("orchestrator",), **kwargs):
    params = {
        "x_kong_verified": "", "x_gateway_key": "", "x_service_assertion": "",
        "x_user_id": "", "x_user_roles": "", "x_tenant_id": "",
    }
    params.update(kwargs)
    return resolve_caller(**params, accepted_services=accepted)


def _assertion(roles, *, service="orchestrator", user="u1", secret=SECRET):
    return mint_service_assertion(
        service=service, tenant_id=TENANT, user_id=user, roles=roles, secret=secret,
    )


def _agent_client():
    """Import the orchestrator module under its own sys.path entry."""
    svc = str(REPO_ROOT / "services" / "orchestrator")
    sys.path.insert(0, svc)
    try:
        from executor.agent_client import _identity_headers
        return _identity_headers
    finally:
        if svc in sys.path:
            sys.path.remove(svc)


class TestSharedTrustDecision:
    def test_kong_with_gateway_key_is_accepted(self):
        caller = _resolve(
            x_kong_verified="true", x_gateway_key=GATEWAY_KEY,
            x_tenant_id=TENANT, x_user_roles="business-operator", x_user_id="u9",
        )

        assert caller.roles == ["business-operator"]
        assert caller.auth_source == "gateway"

    def test_forged_kong_header_alone_is_rejected(self):
        """The bypass that host-published ports made trivial."""
        with pytest.raises(HTTPException) as exc:
            _resolve(x_kong_verified="true", x_tenant_id=TENANT, x_user_roles="platform-admin")
        assert exc.value.status_code == 401

    def test_no_credentials_at_all_is_rejected(self):
        with pytest.raises(HTTPException) as exc:
            _resolve(x_tenant_id=TENANT, x_user_roles="platform-admin", x_user_id="nobody")
        assert exc.value.status_code == 401

    def test_service_assertion_is_accepted_and_headers_ignored(self):
        caller = _resolve(
            x_service_assertion=_assertion(["business-operator"]),
            # Widening attempt alongside a narrow assertion.
            x_user_roles="platform-admin", x_tenant_id="b0000000-0000-0000-0000-000000000002",
        )

        assert caller.roles == ["business-operator"]
        assert caller.tenant_id == TENANT
        assert caller.auth_source == "service:orchestrator"

    def test_assertion_from_an_unaccepted_service_is_rejected(self):
        """workflow-runtime accepts the orchestrator; it must not accept just any
        service that happens to hold the shared secret."""
        with pytest.raises(HTTPException) as exc:
            _resolve(x_service_assertion=_assertion(["platform-admin"], service="some-worker"))
        assert exc.value.status_code == 401

    def test_orchestrator_accepts_no_asserting_services_by_default(self):
        """Nothing calls the orchestrator service-to-service, so that route in is
        shut: an empty allowlist rejects even a correctly signed token."""
        with pytest.raises(HTTPException) as exc:
            _resolve(x_service_assertion=_assertion(["platform-admin"]), accepted=())
        assert exc.value.status_code == 401

    def test_bad_assertion_does_not_fall_through_to_the_kong_path(self):
        with pytest.raises(HTTPException) as exc:
            _resolve(
                x_service_assertion="v1.garbage.garbage",
                x_kong_verified="true", x_gateway_key=GATEWAY_KEY,
                x_tenant_id=TENANT, x_user_roles="platform-admin",
            )
        assert exc.value.status_code == 401

    def test_legacy_gateway_key_env_still_works(self, monkeypatch):
        """KB_GATEWAY_KEY predates the rename; a stack already using it must not
        start rejecting Kong traffic on upgrade."""
        monkeypatch.delenv("GATEWAY_KEY", raising=False)
        monkeypatch.setenv("KB_GATEWAY_KEY", "legacy-value")

        caller = _resolve(
            x_kong_verified="true", x_gateway_key="legacy-value",
            x_tenant_id=TENANT, x_user_roles="viewer",
        )

        assert caller.auth_source == "gateway"


class TestAllowlistParsing:
    def test_default_is_used_when_unset(self, monkeypatch):
        monkeypatch.delenv("RUNTIME_ALLOWED_SERVICES", raising=False)
        assert allowed_services("RUNTIME_ALLOWED_SERVICES", "orchestrator") == frozenset({"orchestrator"})

    def test_empty_value_overrides_the_default_to_nothing(self, monkeypatch):
        """Setting it empty must CLOSE the assertion route, not silently fall back
        to the default — that is how an operator turns the path off."""
        monkeypatch.setenv("RUNTIME_ALLOWED_SERVICES", "")
        assert allowed_services("RUNTIME_ALLOWED_SERVICES", "orchestrator") == frozenset()

    def test_whitespace_and_blanks_are_trimmed(self, monkeypatch):
        monkeypatch.setenv("X_SVCS", " a , ,b ")
        assert allowed_services("X_SVCS") == frozenset({"a", "b"})


class TestOrchestratorToRuntimeLeg:
    def test_orchestrator_asserts_the_real_caller_roles(self):
        headers = _agent_client()("user-42", TENANT, ["business-operator"])
        caller = _resolve(x_service_assertion=headers["X-Service-Assertion"])

        assert caller.user_id == "user-42"
        assert caller.roles == ["business-operator"]

    def test_orchestrator_sends_no_forgeable_identity_headers(self):
        headers = _agent_client()("user-42", TENANT, ["viewer"])

        assert set(headers) == {"X-Service-Assertion"}

    def test_empty_roles_travel_as_empty(self):
        """The orchestrator must not invent roles when the caller has none —
        workflow-runtime then hands an empty list to kb_search, which fails
        closed. Substituting a default here would reopen the escalation."""
        headers = _agent_client()("user-42", TENANT, [])
        caller = _resolve(x_service_assertion=headers["X-Service-Assertion"])

        assert caller.roles == []


class TestPortsAreClosed:
    """The dependency is the real control, but leaving the ports published would
    keep a direct path open for anything that later regressed."""

    def test_compose_does_not_publish_the_agent_tier(self):
        compose = (REPO_ROOT / "infra" / "docker-compose.app.yml").read_text(encoding="utf-8")

        for mapping in ('"8001:8001"', '"8003:8003"'):
            assert mapping not in compose, (
                f"{mapping} is published again — orchestrator and workflow-runtime must be "
                "reachable only through Kong"
            )

    def test_vite_dev_proxy_does_not_forge_an_identity(self):
        """The dev server used to be a second gateway, injecting a hardcoded
        admin identity straight to those ports."""
        vite = (REPO_ROOT / "vite.config.ts").read_text(encoding="utf-8")

        assert "X-User-Roles" not in vite
        assert "X-Tenant-Id" not in vite
