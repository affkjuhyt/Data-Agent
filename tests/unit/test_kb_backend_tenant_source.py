"""Tenant must come from the verified identity, never from the request body."""
from __future__ import annotations

import ast
import pathlib

import pytest
from fastapi import HTTPException

from libs.shared.auth import require_tenant
from services.kb_backend.schemas import QdrantSearchRequest, RetrieveRequest

pytestmark = pytest.mark.unit

ROUTERS = pathlib.Path(__file__).resolve().parents[2] / "backend" / "services" / "kb_backend" / "router"
TENANT = "a0000000-0000-0000-0000-000000000001"
OTHER_TENANT = "b0000000-0000-0000-0000-000000000002"


def _tenant_reads_from_body() -> list[str]:
    """Locations where a router reads a tenant off the request body."""
    found = []
    for path in ROUTERS.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            is_body_attribute = (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in ("body", "payload")
                and node.attr == "tenant_id"
            )
            if is_body_attribute:
                found.append(f"{path.name}:{node.lineno}")
    return found


class TestRequestBodiesCannotNameATenant:
    def test_no_router_reads_a_tenant_from_the_body(self):
        found = _tenant_reads_from_body()
        assert not found, f"tenant read from the request body at: {found}"

    def test_retrieve_request_has_no_tenant_field(self):
        assert "tenant_id" not in RetrieveRequest.model_fields

    def test_qdrant_search_request_has_no_tenant_field(self):
        assert "tenant_id" not in QdrantSearchRequest.model_fields

    def test_a_tenant_sent_in_the_body_is_discarded(self):
        """Older callers still send it; it must not survive parsing."""
        parsed = RetrieveRequest(query="anything", tenant_id=OTHER_TENANT)
        assert not hasattr(parsed, "tenant_id")


class TestNoDefaultTenantRemains:
    def test_no_router_falls_back_to_a_default_tenant(self):
        """`ctx["tenant_id"] or DEFAULT_TENANT` silently pointed writes at
        tenant …0001 — the pattern require_tenant exists to replace."""
        offenders = []
        for path in ROUTERS.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if "DEFAULT_TENANT" in line:
                    offenders.append(f"{path.name}:{number}")
        assert not offenders, f"default-tenant fallback at: {offenders}"

    def test_the_constant_itself_is_gone(self):
        from services.kb_backend import config

        assert not hasattr(config, "DEFAULT_TENANT_ID")


class TestRequireTenantFailsClosed:
    def test_a_verified_caller_gets_their_own_tenant(self):
        assert require_tenant({"tenant_id": TENANT}) == TENANT

    def test_a_caller_without_a_tenant_is_refused(self):
        with pytest.raises(HTTPException) as raised:
            require_tenant({"user_id": "u1", "auth_source": "gateway"})
        assert raised.value.status_code == 403

    def test_no_default_tenant_is_substituted(self):
        with pytest.raises(HTTPException):
            require_tenant({"tenant_id": ""})
