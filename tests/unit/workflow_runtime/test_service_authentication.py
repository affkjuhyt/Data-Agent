"""Every service that can be reached must decide who is calling.

The audit finding named the agent API — orchestrator and workflow_runtime — and
those were fixed. The defect was not: `flow_builder` was still published on host
port 8002 with no authentication of any kind, and every one of its eighteen
routes took its tenant from `x_tenant_id or DEV_TENANT_ID`, a raw client header
defaulting to a real tenant. Anyone reaching the host could list, edit, delete
and publish any tenant's agents and workflows. The former `minio_service` had
the same class of issue before storage browsing was folded into
`workflow_runtime`.

Fixing the two services named in a finding does not fix the class. So the checks
here are structural and repo-wide rather than per-service: a nineteenth route, or
a new service published next month, has to satisfy them too.

Everything is read through the AST, never by substring. Three separate
regressions in this codebase came from a docstring matching a scan for the very
pattern it was describing — including the ones in the files these tests read.
"""
from __future__ import annotations

import ast
import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
# The production set. dev/ publishes every Service as a NodePort on purpose
# (single-node debugging box, see deploy/k8s/dev/REPORT.md).
K8S = REPO_ROOT / "deploy" / "k8s" / "production"
SERVICES = REPO_ROOT / "backend" / "services"

pytestmark = pytest.mark.unit

# Reached before any token exists — Kong's upstream probe and the kubelet's
# probes both run at startup, and it returns a literal.
_OPEN_ROUTERS = {"health"}

# Not Python application services: the gateway itself, datastores, and the
# frontend, none of which resolve a caller identity in this codebase.
_NOT_APP_SERVICES = {
    "kong", "kong-admin", "frontend", "postgres", "mongo", "redis", "kafka",
    "qdrant", "minio", "keycloak", "litellm", "prometheus", "grafana", "loki",
    "langfuse", "clickhouse", "zookeeper", "otel-collector",
}


def _k8s_docs() -> list[dict]:
    docs = [
        d
        for sub in ("", "database", "service", "job")
        for f in sorted((K8S / sub).glob("*.yaml"))
        for d in yaml.safe_load_all(f.read_text(encoding="utf-8"))
        if d
    ]
    assert docs, "no manifests found — deploy/k8s moved again"
    return docs


def _service_dir(name: str) -> pathlib.Path | None:
    for candidate in (SERVICES / name, SERVICES / name.replace("-", "_")):
        if candidate.is_dir():
            return candidate
    return None


def _python_files(directory: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in directory.rglob("*.py") if "__pycache__" not in p.parts]


def _authenticating_services() -> set[str]:
    """Services whose code resolves a caller identity, by AST not substring."""
    found = set()
    for service in SERVICES.iterdir():
        if not service.is_dir():
            continue
        for path in _python_files(service):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            names = {
                node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
            } | {
                node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            }
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names |= {a.name.split(".")[-1] for a in node.names}
            if {"get_current_user", "resolve_caller", "make_get_current_user"} & names:
                found.add(service.name)
                break
    return found


class TestNothingUnauthenticatedIsPublished:
    """A cluster publishes a workload through its Service type. That is the
    decision to make a service reachable without the gateway, so it is the
    decision that has to come with an identity check."""

    def _exposed_services(self) -> list[dict]:
        return [
            d for d in _k8s_docs()
            if d["kind"] == "Service"
            and d["spec"].get("type") in ("NodePort", "LoadBalancer")
        ]

    def test_only_kong_is_reachable_from_outside_the_cluster(self):
        """Kong is the only way in. Everything else is ClusterIP, so an Ingress
        cannot accidentally be pointed at a service that trusts its caller.

        Keycloak is the one exception: browsers follow the login redirect to it
        directly (no ingress controller; see its Service in keycloak.yaml), and
        it authenticates everything it serves itself."""
        offenders = [
            s["metadata"]["name"] for s in self._exposed_services()
            if s["metadata"]["name"] not in ("kong", "keycloak")
        ]
        assert not offenders, (
            f"NodePort/LoadBalancer on {sorted(offenders)} — reachable without "
            "Kong. Use ClusterIP and route through the gateway."
        )

    def test_every_externally_reachable_app_service_authenticates(self):
        authenticating = _authenticating_services()
        offenders = []
        for svc in self._exposed_services():
            name = svc["metadata"]["name"]
            if name in _NOT_APP_SERVICES or _service_dir(name) is None:
                continue
            # Service names are kebab-case, code directories snake_case.
            if name.replace("-", "_") not in authenticating:
                offenders.append(name)
        assert not offenders, (
            f"externally reachable service(s) with no caller authentication: "
            f"{sorted(offenders)} — either add the shared get_current_user "
            "dependency or make the Service ClusterIP"
        )

    def test_the_agent_tier_has_no_externally_reachable_service(self):
        agent_tier = {"workflow-runtime", "flow-builder", "sandbox-executor"}
        offenders = [
            s["metadata"]["name"] for s in self._exposed_services()
            if s["metadata"]["name"] in agent_tier
        ]
        assert not offenders, f"{offenders} are reachable without Kong"


class TestNoTenantComesFromAClientHeader:
    """`x_tenant_id or DEV_TENANT_ID` is the whole finding in one expression:
    the tenant asserted by the caller, defaulting to a real one."""

    def _boolop_fallbacks(self, tree: ast.AST) -> list[str]:
        hits = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
                continue
            names = {v.id for v in node.values if isinstance(v, ast.Name)}
            if any("tenant" in n.lower() for n in names) and any(
                "DEV_TENANT" in n for n in names
            ):
                hits.append(" or ".join(sorted(names)))
        return hits

    @pytest.mark.parametrize("service", ["flow_builder", "workflow_runtime"])
    def test_no_router_falls_back_to_the_dev_tenant(self, service):
        directory = _service_dir(service)
        assert directory is not None
        offenders = {}
        for path in _python_files(directory):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            hits = self._boolop_fallbacks(tree)
            if hits:
                offenders[str(path.relative_to(REPO_ROOT))] = hits
        assert not offenders, f"dev-tenant fallback still reachable: {offenders}"

    def test_flow_builder_routes_take_no_tenant_header_at_all(self):
        """Not merely "the header is checked" — the parameter is gone, so a new
        route cannot reintroduce it by copying a neighbour."""
        directory = _service_dir("flow_builder")
        for path in _python_files(directory / "routers"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = [a.arg for a in node.args.args + node.args.kwonlyargs]
                    assert "x_tenant_id" not in args, (
                        f"{path.name}:{node.name} still takes a tenant header"
                    )


class TestRoutersAreAuthenticatedAtIncludeTime:
    """Per-route dependencies are ones a new endpoint can silently forget; these
    services had eighteen routes each and zero checks."""

    def _include_calls(self, app_py: pathlib.Path) -> dict[str, bool]:
        """{router module: was it included with dependencies}"""
        tree = ast.parse(app_py.read_text(encoding="utf-8"))
        out: dict[str, bool] = {}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "include_router":
                continue
            has_deps = any(k.arg == "dependencies" for k in node.keywords)
            arg = node.args[0] if node.args else None
            if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
                out[arg.value.id] = has_deps
            elif isinstance(arg, ast.Name):
                # Included from a loop variable — the loop carries the deps.
                out.setdefault("_loop", has_deps)
        return out

    @pytest.mark.parametrize("service", ["flow_builder"])
    def test_only_health_is_included_without_authentication(self, service):
        directory = _service_dir(service)
        includes = self._include_calls(directory / "app.py")
        unguarded = {name for name, guarded in includes.items() if not guarded}
        assert unguarded <= _OPEN_ROUTERS, (
            f"{service} includes {sorted(unguarded - _OPEN_ROUTERS)} without authentication"
        )

    @pytest.mark.parametrize("service", ["flow_builder"])
    def test_the_loop_that_carries_the_dependency_exists(self, service):
        """The routers are included through a loop with `dependencies=`; if
        someone unrolls it, the previous test still passes only if each call
        keeps the argument."""
        directory = _service_dir(service)
        includes = self._include_calls(directory / "app.py")
        assert includes, f"{service}/app.py includes no routers"
        assert any(guarded for guarded in includes.values())


class TestFailClosedOnAMissingTenant:
    def test_require_tenant_refuses_rather_than_defaulting(self):
        """Authentication proves who is calling; it does not by itself say which
        tenant's data they may touch. A verified caller with no tenant used to
        become the dev tenant."""
        from fastapi import HTTPException

        from libs.shared.auth import require_tenant

        assert require_tenant({"tenant_id": "t-1"}) == "t-1"

        for empty in ({}, {"tenant_id": ""}, {"tenant_id": None}, None):
            with pytest.raises(HTTPException) as exc:
                require_tenant(empty)
            assert exc.value.status_code == 403

    def test_the_orchestrator_stream_uses_it(self):
        import ast

        source = (SERVICES / "workflow_runtime" / "orchestrator" / "routers" / "stream.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        called = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "require_tenant" in called


class TestCanvasIsScopedToItsOwner:
    """Authenticating the canvas routes alone would have narrowed "anyone on the
    host" to "any authenticated user" — the canvas lives in Mongo, keyed by
    workflow_version_id, with no row-level security to fall back on."""

    def test_both_canvas_routes_check_ownership(self):
        path = _service_dir("flow_builder") / "routers" / "canvas.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))

        routes = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                and d.func.attr in {"get", "post", "put", "patch", "delete"}
                for d in node.decorator_list
            )
        ]
        assert len(routes) == 2

        for route in routes:
            called = {
                n.func.id for n in ast.walk(route)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            assert "_owned_version" in called, f"{route.name} does not check ownership"

    def test_a_foreign_version_is_indistinguishable_from_a_missing_one(self):
        """404 rather than 403: 403 would confirm that the id exists, which is
        itself a fact about another tenant."""
        source = (_service_dir("flow_builder") / "routers" / "canvas.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        helper = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "_owned_version"
        )
        raised = [
            n.exc for n in ast.walk(helper) if isinstance(n, ast.Raise)
        ]
        codes = [
            a.value for r in raised if isinstance(r, ast.Call)
            for a in r.args if isinstance(a, ast.Constant)
        ]
        assert 404 in codes and 403 not in codes


class TestGatewayConfigMatchesTheClosedPorts:
    def test_every_unpublished_app_service_has_a_kong_route(self):
        """Closing a port without a route in front of it does not harden the
        service, it removes it."""
        setup = (REPO_ROOT / "deploy" / "docker" / "kong" / "kong-setup.sh").read_text(encoding="utf-8")
        for service in ("workflow_runtime",):
            assert f"$KONG_ADMIN/services/{service}" in setup, f"{service} has no Kong service"

    def test_kong_defines_each_service_exactly_once(self):
        """Two PUTs of the same service is not an error at runtime — the second
        silently wins — so a duplicate would hide whichever config lost."""
        setup = (REPO_ROOT / "deploy" / "docker" / "kong" / "kong-setup.sh").read_text(encoding="utf-8")
        for service in ("workflow_runtime",):
            creates = setup.count(f'-X PUT "$KONG_ADMIN/services/{service}"')
            assert creates == 1, f"{service} is created {creates} times in kong-setup.sh"
