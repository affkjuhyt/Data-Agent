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
# (single-node debugging box, see the "Dev set" section of deploy/k8s/README.md).
K8S = REPO_ROOT / "deploy" / "k8s" / "production"
SERVICES = REPO_ROOT / "backend" / "services"

pytestmark = pytest.mark.unit

# Not Python application services: the gateway itself, datastores, and the
# frontend, none of which resolve a caller identity in this codebase.
_NOT_APP_SERVICES = {
    "kong",
    "kong-admin",
    "frontend",
    "postgres",
    "mongo",
    "redis",
    "kafka",
    "qdrant",
    "minio",
    "keycloak",
    "litellm",
    "prometheus",
    "grafana",
    "loki",
    "langfuse",
    "clickhouse",
    "zookeeper",
    "otel-collector",
}


# Every Python service in the tree. Listed rather than globbed: a removed service
# can leave an ignored __pycache__-only directory behind in a working copy.
_LIVE_SERVICES = (
    "api",
    "auth_api",
    "kb_backend",
    "model_service",
    "platform_workers",
    "sandbox_executor",
    "worker_agent",
)


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
            names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
                node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.Import | ast.ImportFrom):
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
            d
            for d in _k8s_docs()
            if d["kind"] == "Service" and d["spec"].get("type") in ("NodePort", "LoadBalancer")
        ]

    def test_only_kong_is_reachable_from_outside_the_cluster(self):
        """Kong is the only way in. Everything else is ClusterIP, so an Ingress
        cannot accidentally be pointed at a service that trusts its caller.

        Keycloak is the one exception: browsers follow the login redirect to it
        directly (no ingress controller; see its Service in keycloak.yaml), and
        it authenticates everything it serves itself."""
        offenders = [
            s["metadata"]["name"]
            for s in self._exposed_services()
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
        agent_tier = {"worker-agent", "sandbox-executor"}
        offenders = [
            s["metadata"]["name"]
            for s in self._exposed_services()
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
            if any("tenant" in n.lower() for n in names) and any("DEV_TENANT" in n for n in names):
                hits.append(" or ".join(sorted(names)))
        return hits

    @pytest.mark.parametrize("service", _LIVE_SERVICES)
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


# The Kong service names of the application services behind the gateway.
_KONG_SERVICES = ("api", "auth_api", "kb-backend")


class TestGatewayConfigMatchesTheClosedPorts:
    def test_every_unpublished_app_service_has_a_kong_route(self):
        """Closing a port without a route in front of it does not harden the
        service, it removes it."""
        setup = (REPO_ROOT / "deploy" / "docker" / "kong" / "kong-setup.sh").read_text(
            encoding="utf-8"
        )
        for service in _KONG_SERVICES:
            assert f"$KONG_ADMIN/services/{service}" in setup, f"{service} has no Kong service"

    def test_kong_defines_each_service_exactly_once(self):
        """Two PUTs of the same service is not an error at runtime — the second
        silently wins — so a duplicate would hide whichever config lost."""
        setup = (REPO_ROOT / "deploy" / "docker" / "kong" / "kong-setup.sh").read_text(
            encoding="utf-8"
        )
        for service in _KONG_SERVICES:
            creates = setup.count(f'-X PUT "$KONG_ADMIN/services/{service}"')
            assert creates == 1, f"{service} is created {creates} times in kong-setup.sh"

    def test_the_retired_runtime_is_not_recreated(self):
        """workflow_runtime was removed (2c1dac2c); kong-setup.sh only deletes its
        leftover service and routes, so a stale PUT would route to nothing."""
        setup = (REPO_ROOT / "deploy" / "docker" / "kong" / "kong-setup.sh").read_text(
            encoding="utf-8"
        )
        for service in ("workflow_runtime", "flow_builder"):
            assert f'-X PUT "$KONG_ADMIN/services/{service}' not in setup, service
