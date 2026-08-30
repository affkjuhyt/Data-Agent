"""kb-backend request authentication (backend/services/kb_backend/dependencies/auth.py).

Two authenticated routes in, and nothing else: a signed service assertion, or
Kong proven by the shared gateway key. The property under test throughout is
that identity can only arrive by a route the caller had to prove — previously
`X-Kong-Verified: true` plus any `X-User-Roles` was enough, and that is exactly
what workflow-runtime was sending.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi import HTTPException

from libs.shared.service_identity import mint_service_assertion

pytestmark = pytest.mark.unit

SECRET = "test-shared-secret"
TENANT = "a0000000-0000-0000-0000-000000000001"
GATEWAY_KEY = "kong-proves-itself-with-this"


@pytest.fixture
def auth(monkeypatch):
    """Re-import so module-level env reads (KB_ALLOWED_SERVICES) are re-evaluated."""
    monkeypatch.setenv("SERVICE_AUTH_SECRET", SECRET)
    monkeypatch.setenv("KB_ALLOWED_SERVICES", "workflow-runtime")
    monkeypatch.setenv("KB_GATEWAY_KEY", GATEWAY_KEY)
    import services.kb_backend.dependencies.auth as auth_mod
    return importlib.reload(auth_mod)


def _assertion(roles, *, service="workflow-runtime", tenant=TENANT, user="u1", secret=SECRET):
    return mint_service_assertion(
        service=service, tenant_id=tenant, user_id=user, roles=roles, secret=secret,
    )


def _call(auth, **kwargs):
    """Invoke authenticate() with FastAPI's Header defaults filled in."""
    params = {
        "x_kong_verified": "", "x_gateway_key": "", "x_service_assertion": "",
        "x_user_id": "", "x_user_name": "", "x_tenant_id": "",
        "x_user_roles": "", "x_role_id": "",
    }
    params.update(kwargs)
    return auth.authenticate(**params)


class TestServiceAssertionPath:
    def test_valid_assertion_yields_the_asserted_role(self, auth):
        ctx = _call(auth, x_service_assertion=_assertion(["business-operator"]))

        assert ctx["role"] == "BUSINESS_OPERATOR"
        assert ctx["tenant_id"] == TENANT
        assert ctx["auth_source"] == "service:workflow-runtime"

    def test_headers_cannot_widen_a_narrow_assertion(self, auth):
        """The escalation this change exists to stop: identity must come from
        the token alone, so accompanying headers are ignored entirely."""
        ctx = _call(
            auth,
            x_service_assertion=_assertion(["business-operator"]),
            x_user_roles="platform-admin",
            x_role_id="platform-admin",
            x_user_id="somebody-else",
            x_tenant_id="b0000000-0000-0000-0000-000000000002",
        )

        assert ctx["role"] == "BUSINESS_OPERATOR"
        assert ctx["roles"] == ["BUSINESS_OPERATOR"]
        assert ctx["user_id"] == "u1"
        assert ctx["tenant_id"] == TENANT

    def test_role_id_matches_the_asserted_role(self, auth):
        """role_id is what access.py filters documents by — the whole point of
        propagating identity is that this stops being the admin's id."""
        admin = _call(auth, x_service_assertion=_assertion(["platform-admin"]))["role_id"]
        operator = _call(auth, x_service_assertion=_assertion(["business-operator"]))["role_id"]

        assert admin != operator

    def test_tampered_assertion_is_rejected(self, auth):
        token = _assertion(["business-operator"])
        prefix, payload, signature = token.split(".")
        tampered = f"{prefix}.{payload}.{'A' * len(signature)}"

        with pytest.raises(HTTPException) as exc:
            _call(auth, x_service_assertion=tampered)
        assert exc.value.status_code == 401

    def test_assertion_signed_with_another_secret_is_rejected(self, auth):
        with pytest.raises(HTTPException) as exc:
            _call(auth, x_service_assertion=_assertion(["platform-admin"], secret="attacker-secret"))
        assert exc.value.status_code == 401

    def test_assertion_from_an_unlisted_service_is_rejected(self, auth):
        with pytest.raises(HTTPException) as exc:
            _call(auth, x_service_assertion=_assertion(["platform-admin"], service="rogue-service"))
        assert exc.value.status_code == 401

    def test_empty_roles_yields_no_permissions(self, auth):
        """An assertion with no roles must not become a wildcard."""
        ctx = _call(auth, x_service_assertion=_assertion([]))

        assert ctx["role"] == ""
        assert auth.RBAC.can(ctx["role"], "retrieveKnowledge") is False

    def test_assertion_with_a_bad_tenant_is_a_400(self, auth):
        with pytest.raises(HTTPException) as exc:
            _call(auth, x_service_assertion=_assertion(["business-operator"], tenant="not-a-uuid"))
        assert exc.value.status_code == 400


class TestGatewayPath:
    def test_kong_with_the_gateway_key_is_accepted(self, auth):
        ctx = _call(
            auth, x_kong_verified="true", x_gateway_key=GATEWAY_KEY,
            x_tenant_id=TENANT, x_user_roles="platform-admin", x_user_id="admin-1",
        )

        assert ctx["role"] == "PLATFORM_ADMIN"
        assert ctx["auth_source"] == "gateway"

    def test_forged_kong_header_without_the_key_is_rejected(self, auth):
        """The direct-to-port bypass. Anyone reaching kb-backend could send
        these headers; without the gateway key they no longer mean anything."""
        with pytest.raises(HTTPException) as exc:
            _call(
                auth, x_kong_verified="true",
                x_tenant_id=TENANT, x_user_roles="platform-admin",
            )
        assert exc.value.status_code == 401

    def test_wrong_gateway_key_is_rejected(self, auth):
        with pytest.raises(HTTPException) as exc:
            _call(
                auth, x_kong_verified="true", x_gateway_key="guessed",
                x_tenant_id=TENANT, x_user_roles="platform-admin",
            )
        assert exc.value.status_code == 401

    def test_unset_gateway_key_keeps_the_previous_behaviour(self, monkeypatch):
        """Deliberate compatibility escape hatch so an existing deployment does
        not lock itself out mid-upgrade. It is a downgrade, and auth.py logs a
        warning on every request that takes it."""
        monkeypatch.setenv("SERVICE_AUTH_SECRET", SECRET)
        monkeypatch.delenv("KB_GATEWAY_KEY", raising=False)
        import services.kb_backend.dependencies.auth as auth_mod
        auth = importlib.reload(auth_mod)

        ctx = _call(auth, x_kong_verified="true", x_tenant_id=TENANT, x_user_roles="platform-admin")

        assert ctx["role"] == "PLATFORM_ADMIN"


class TestNoAuthAtAll:
    def test_bare_request_is_rejected(self, auth):
        with pytest.raises(HTTPException) as exc:
            _call(auth)
        assert exc.value.status_code == 401

    def test_identity_headers_alone_are_rejected(self, auth):
        with pytest.raises(HTTPException) as exc:
            _call(auth, x_tenant_id=TENANT, x_user_roles="platform-admin", x_user_id="nobody")
        assert exc.value.status_code == 401

    def test_assertion_rejected_does_not_fall_through_to_the_kong_path(self, auth):
        """A bad assertion must fail the request, not degrade into header
        trust — otherwise an attacker just sends both."""
        with pytest.raises(HTTPException) as exc:
            _call(
                auth,
                x_service_assertion="v1.garbage.garbage",
                x_kong_verified="true", x_gateway_key=GATEWAY_KEY,
                x_tenant_id=TENANT, x_user_roles="platform-admin",
            )
        assert exc.value.status_code == 401
