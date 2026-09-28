"""Authentication on the agent tier — workflow_runtime and the orchestrator
package inside it.

The agent tier previously had NO authentication of any kind: the orchestrator
read the tenant from a raw header and fell back to DEV_TENANT_ID, workflow-
runtime did the same, and both were published on host ports. Kong already had
routes and the aeroflow-jwks plugin (deploy/docker/kong/kong-setup.sh) — the
services just never checked that a request had been through it.

These tests pin the shared trust decision, the identity the orchestrator hands
workflow_runtime for each in-process agent turn (it used to be a signed HMAC
assertion over HTTP; now it is the same caller dict, built directly), and the
fact that the ports are no longer published.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest
from fastapi import HTTPException

from libs.shared.auth import allowed_services, resolve_caller
from libs.shared.service_identity import mint_service_assertion

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

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


def _caller_for():
    """The orchestrator's in-process caller builder. agent_client.py is loaded
    by path (with workflow_runtime on sys.path for its `orchestrator.config`
    import) so the test does not drag in `orchestrator.executor`'s package
    __init__ — and with it the planner, litellm and langfuse."""
    svc = REPO_ROOT / "backend" / "services" / "workflow_runtime"
    sys.path.insert(0, str(svc))
    try:
        spec = importlib.util.spec_from_file_location(
            "orch_agent_client_under_test", svc / "orchestrator" / "executor" / "agent_client.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module._caller_for
    finally:
        if str(svc) in sys.path:
            sys.path.remove(str(svc))


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
        """workflow_runtime accepts the orchestrator; it must not accept just any
        service that happens to hold the shared secret."""
        with pytest.raises(HTTPException) as exc:
            _resolve(x_service_assertion=_assertion(["platform-admin"], service="some-worker"))
        assert exc.value.status_code == 401

    def test_an_empty_allowlist_rejects_a_signed_assertion(self):
        """workflow_runtime accepts no asserting services by default now (the
        orchestrator calls it in-process): an empty allowlist rejects even a
        correctly signed token."""
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
    """The orchestrator runs inside workflow_runtime and calls
    run_conversation_stream(req, request, caller) directly. `caller` must be
    the same dict the auth dependency would have produced, so the runtime's
    tenant/role checks see exactly the real user."""

    def test_orchestrator_asserts_the_real_caller_roles(self):
        caller = _caller_for()("user-42", TENANT, ["business-operator"])

        assert caller["user_id"] == "user-42"
        assert caller["tenant_id"] == TENANT
        assert caller["roles"] == ["business-operator"]
        assert caller["auth_source"] == "service:orchestrator"

    def test_caller_has_exactly_the_shape_the_auth_dependency_produces(self):
        """Nothing extra (no headers, no secrets) and nothing missing — the
        runtime reads these keys and must not have to special-case the
        orchestrator."""
        caller = _caller_for()("user-42", TENANT, ["viewer"])
        via_gateway = _resolve(
            x_kong_verified="true", x_gateway_key=GATEWAY_KEY,
            x_tenant_id=TENANT, x_user_roles="viewer", x_user_id="user-42",
        )
        expected = {"user_id", "user_name", "roles", "tenant_id", "auth_source"}

        assert set(caller) == expected
        assert {k: caller[k] for k in ("user_id", "roles", "tenant_id")} == {
            "user_id": via_gateway.user_id, "roles": via_gateway.roles, "tenant_id": via_gateway.tenant_id,
        }

    def test_empty_roles_travel_as_empty(self):
        """The orchestrator must not invent roles when the caller has none —
        workflow_runtime then hands an empty list to kb_search, which fails
        closed. Substituting a default here would reopen the escalation."""
        caller = _caller_for()("user-42", TENANT, [])

        assert caller["roles"] == []

    def test_roles_are_copied_not_shared(self):
        roles = ["viewer"]
        caller = _caller_for()("user-42", TENANT, roles)
        roles.append("platform-admin")

        assert caller["roles"] == ["viewer"]


class TestPortsAreClosed:
    """The dependency is the real control, but leaving the tier reachable would
    keep a direct path open for anything that later regressed."""

    def test_no_service_exposes_the_agent_tier_outside_the_cluster(self):
        import yaml

        # The production set. dev/ publishes every Service as a NodePort on
        # purpose (single-node debugging box, see the "Dev set" section of deploy/k8s/README.md).
        k8s = REPO_ROOT / "deploy" / "k8s" / "production"
        services = [
            d
            for sub in ("", "database", "service", "job")
            for f in sorted((k8s / sub).glob("*.yaml"))
            for d in yaml.safe_load_all(f.read_text(encoding="utf-8"))
            if d and d["kind"] == "Service"
        ]
        assert services, "no Services found — deploy/k8s moved again"

        # Keycloak is published because browsers follow the login redirect to
        # it directly (no ingress controller; see its Service in keycloak.yaml).
        exposed = [
            s["metadata"]["name"] for s in services
            if s["spec"].get("type") in ("NodePort", "LoadBalancer")
            and s["metadata"]["name"] not in ("kong", "keycloak")
        ]
        assert not exposed, (
            f"{exposed} reachable without Kong — workflow-runtime (and the "
            "orchestrator inside it) must only be reachable through the gateway"
        )

    def test_vite_dev_proxy_does_not_forge_an_identity(self):
        """The dev server used to be a second gateway, injecting a hardcoded
        admin identity straight to those ports."""
        vite = (REPO_ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")

        assert "X-User-Roles" not in vite
        assert "X-Tenant-Id" not in vite
