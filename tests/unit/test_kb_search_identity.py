"""Caller identity on the kb_search -> kb-backend call
(backend/services/workflow-runtime/nodes/sandbox/kb_search.py).

This node used to send `X-Kong-Verified: true` plus a fixed
`platform-admin,tenant-admin,kb-admin,engineer` role set on every agent
retrieval, so kb-backend's document-level role filter (operations/access.py)
always saw an administrator regardless of who actually asked. These tests pin
the replacement: the real caller's roles, signed, with no fallback identity.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "workflow-runtime")

pytestmark = pytest.mark.unit

SECRET = "test-shared-secret"
TENANT = "a0000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("SERVICE_AUTH_SECRET", SECRET)


def _kb_search():
    import nodes.sandbox.kb_search as kb_search
    return kb_search


class TestIdentityHeaders:
    def test_real_caller_roles_are_asserted_not_an_admin_set(self):
        from libs.shared.service_identity import verify_service_assertion

        kb_search = _kb_search()
        cfg = {"user_roles": ["business-operator"], "user_ref": "user-42"}

        headers = kb_search._identity_headers(cfg, TENANT)
        payload = verify_service_assertion(
            headers["X-Service-Assertion"], secret=SECRET, allowed_services=["workflow-runtime"],
        )

        assert payload["roles"] == ["business-operator"]
        assert payload["uid"] == "user-42"
        assert payload["tid"] == TENANT

    def test_no_forgeable_identity_headers_are_sent(self):
        """The headers kb-backend used to be told to trust must not be present
        at all — leaving them would let a stale kb-backend keep believing them."""
        kb_search = _kb_search()

        headers = kb_search._identity_headers(
            {"user_roles": ["ai-engineer"], "user_ref": "u1"}, TENANT,
        )

        assert set(headers) == {"X-Service-Assertion"}
        for forgeable in ("X-Kong-Verified", "X-User-Roles", "X-User-Id", "X-Role", "X-Tenant-Id"):
            assert forgeable not in headers

    def test_multiple_roles_are_preserved_in_order(self):
        """kb-backend's authenticate() takes roles[0] as the effective role, so
        order is privilege — it must survive transport exactly."""
        from libs.shared.service_identity import verify_service_assertion

        kb_search = _kb_search()

        headers = kb_search._identity_headers(
            {"user_roles": ["executive-viewer", "business-operator"], "user_ref": "u1"}, TENANT,
        )
        payload = verify_service_assertion(
            headers["X-Service-Assertion"], secret=SECRET, allowed_services=["workflow-runtime"],
        )

        assert payload["roles"] == ["executive-viewer", "business-operator"]


class TestFailClosed:
    def test_no_roles_refuses_instead_of_defaulting(self):
        from libs.shared.service_identity import ServiceAssertionError

        kb_search = _kb_search()

        with pytest.raises(ServiceAssertionError, match="no caller roles"):
            kb_search._identity_headers({"user_roles": [], "user_ref": "u1"}, TENANT)

    def test_missing_roles_key_refuses(self):
        from libs.shared.service_identity import ServiceAssertionError

        kb_search = _kb_search()

        with pytest.raises(ServiceAssertionError, match="no caller roles"):
            kb_search._identity_headers({}, TENANT)

    def test_no_secret_refuses_rather_than_sending_an_unsigned_call(self, monkeypatch):
        from libs.shared.service_identity import ServiceAssertionError

        monkeypatch.delenv("SERVICE_AUTH_SECRET", raising=False)
        kb_search = _kb_search()

        with pytest.raises(ServiceAssertionError):
            kb_search._identity_headers({"user_roles": ["viewer"], "user_ref": "u1"}, TENANT)

    async def test_node_returns_no_chunks_when_identity_is_unavailable(self, monkeypatch):
        """End-to-end at node level: an unidentified turn yields nothing and an
        explicit error, never the whole knowledge base."""
        kb_search = _kb_search()
        called = False

        class _Boom:
            def __init__(self, *a, **kw):
                nonlocal called
                called = True

        monkeypatch.setattr(kb_search.httpx, "AsyncClient", _Boom)

        result = await kb_search.kb_search_node({
            "config": {"kb_endpoint": "http://kb-backend:8000", "tenant_id": TENANT, "user_roles": []},
            "query": "doanh thu quý 3",
            "tool_calls": [],
        })

        assert result["kb_chunks"] == []
        assert "not authorized" in result["kb_error"]
        assert called is False, "must not reach kb-backend at all without an identity"


class TestNoHardcodedIdentityRemains:
    """Regression guard. The defect was a literal in this file, and every test
    above would still pass if someone reintroduced one as a fallback."""

    def test_source_has_no_admin_role_literal_or_placeholder_user(self):
        """Inspects executable string literals via the AST, skipping docstrings
        — the module docstring quotes the old forged header while explaining why
        it is gone, and a raw text scan would trip on that instead of on real
        code."""
        import ast
        import inspect

        kb_search = _kb_search()
        tree = ast.parse(inspect.getsource(kb_search))

        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    docstrings.add(id(body[0].value))

        literals = [
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
        ]

        for forbidden in ("X-Kong-Verified", "platform-admin", "tenant-admin", "kb-admin",
                          "11111111-1111-1111-1111-111111111111"):
            offenders = [lit for lit in literals if forbidden in lit]
            assert not offenders, (
                f"{forbidden!r} appears in a kb_search.py string literal ({offenders!r}) — the "
                "caller's real identity must be asserted, never a hardcoded or defaulted one"
            )
