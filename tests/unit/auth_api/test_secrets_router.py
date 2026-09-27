"""Unit tests for the role/key_type authorization logic in
backend/services/auth_api/routers/secrets.py — specifically the change that lets
tenant-admin self-service DB_CONNECTOR_PASSWORD secrets for their OWN tenant,
without gaining any access to other key_types (SIGNING_KEY/ENCRYPTION_KEY/
HMAC_KEY/BEARER_TOKEN/MCP_TOKEN/KB_API_KEY/WAREHOUSE_PASSWORD stay
platform-admin-only). No DB/OpenBao/Kong needed — these are pure functions.
"""
from __future__ import annotations

import pytest

from .._loader import SERVICES, load_module_from_path

pytestmark = pytest.mark.unit

# auth_api lives in a hyphenated dir; secrets.py does `from auth_core import *`
# (needs backend/services/auth_api on sys.path) which itself does
# `from shared.auth import ...` (needs services/ on sys.path).
secrets_router = load_module_from_path(
    "secrets_router",
    SERVICES / "auth_api" / "routers" / "secrets.py",
    extra_syspath=[SERVICES, SERVICES / "auth_api"],
)


def _user(roles: list[str], tenant_id: str = "tenant-1") -> dict:
    return {"user_id": "u1", "roles": roles, "tenant_id": tenant_id}


class TestIsPlatformAdmin:
    def test_true_when_role_present(self) -> None:
        assert secrets_router._is_platform_admin(_user(["platform-admin"])) is True

    def test_false_when_role_absent(self) -> None:
        assert secrets_router._is_platform_admin(_user(["tenant-admin"])) is False


class TestIsTenantAdminFor:
    def test_true_for_own_tenant(self) -> None:
        user = _user(["tenant-admin"], tenant_id="tenant-1")
        assert secrets_router._is_tenant_admin_for(user, "tenant-1") is True

    def test_false_for_other_tenant(self) -> None:
        """A tenant-admin's role only ever applies within their OWN tenant —
        requesting a DIFFERENT tenant_id must not be granted, even though
        require_role() itself has no tenant-scoping built in (see
        auth_core.require_role) and would let this through if this manual
        check were skipped."""
        user = _user(["tenant-admin"], tenant_id="tenant-1")
        assert secrets_router._is_tenant_admin_for(user, "tenant-2") is False

    def test_false_without_tenant_admin_role(self) -> None:
        user = _user(["viewer"], tenant_id="tenant-1")
        assert secrets_router._is_tenant_admin_for(user, "tenant-1") is False


class TestKeyTypeScoping:
    """VALID_KEY_TYPES / TENANT_MANAGEABLE_KEY_TYPES — the actual security
    boundary: tenant-admin's extra access must be limited to EXACTLY
    DB_CONNECTOR_PASSWORD, nothing else."""

    def test_db_connector_password_is_tenant_manageable(self) -> None:
        assert "DB_CONNECTOR_PASSWORD" in secrets_router.TENANT_MANAGEABLE_KEY_TYPES

    def test_db_connector_password_is_a_valid_key_type(self) -> None:
        assert "DB_CONNECTOR_PASSWORD" in secrets_router.VALID_KEY_TYPES

    @pytest.mark.parametrize("sensitive_type", [
        "SIGNING_KEY", "ENCRYPTION_KEY", "HMAC_KEY",
        "BEARER_TOKEN", "MCP_TOKEN", "KB_API_KEY", "WAREHOUSE_PASSWORD",
    ])
    def test_no_other_key_type_became_tenant_manageable(self, sensitive_type: str) -> None:
        assert sensitive_type not in secrets_router.TENANT_MANAGEABLE_KEY_TYPES
