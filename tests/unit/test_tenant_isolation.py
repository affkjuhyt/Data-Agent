"""Is row-level security actually doing anything?

`backend/alembic/versions/a1b2c3d4e5f6` enables and FORCEs RLS on every tenant-scoped
table, the tenant_isolation policy is correct, and
`get_client(tenant_id=...)` sets `app.tenant_id` correctly. None of that
isolates anything unless the connecting role is subject to RLS — and a Postgres
SUPERUSER is not, unconditionally. FORCE does not change that: it subjects the
table OWNER to its own policies, and says nothing about superusers or roles
holding BYPASSRLS.

Every service except workflow_runtime connects as `${POSTGRES_USER:-admin}`.
workflow_runtime connects as `runtime_writer`, which
`deploy/docker/sql/grant-runtime-writer.sql` explicitly sets NOBYPASSRLS — so it is the
one service where the policies do anything at all today.

`tests/integration/test_rls_enforcement.py` proves the policy works, and proves
it against `app_user` — a role no service connects as. Its
`test_owner_role_bypasses_rls` even asserts that the DSN every other service
uses returns all tenants' rows. Both tests pass. The deployment is unprotected.

So the checks here are about the DEPLOYMENT rather than the policy:

  * a scoped session on a role that bypasses RLS is the dangerous case, because
    it reads as isolated at every call site and is not;
  * the inventory of unscoped call sites is a ratchet — it may shrink, never
    grow;
  * the role that makes RLS real has to be deployable, not just named in a
    migration comment.
"""
from __future__ import annotations

import ast
import collections
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
INFRA = REPO_ROOT / "deploy" / "docker"

pytestmark = pytest.mark.unit


def _get_client_calls() -> tuple[collections.Counter, collections.Counter, dict]:
    """(scoped, unscoped, {service: [call sites]}) across the repo, via AST."""
    scoped: collections.Counter = collections.Counter()
    unscoped: collections.Counter = collections.Counter()
    sites: dict[str, list[str]] = collections.defaultdict(list)

    roots = [REPO_ROOT / "backend" / "services", REPO_ROOT / "backend" / "libs"]
    for root in roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if node.func.attr != "get_client":
                    continue
                service = path.relative_to(REPO_ROOT).parts[2]   # backend/<services|libs>/<name>
                if any(k.arg == "tenant_id" for k in node.keywords) or node.args:
                    scoped[service] += 1
                else:
                    unscoped[service] += 1
                    sites[service].append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    return scoped, unscoped, sites


# The inventory as it stands. A ratchet, not a target: the number may go DOWN as
# services adopt tenant scoping, and a rise means a new unscoped call site was
# added to a surface that already cannot isolate.
_UNSCOPED_BUDGET = {
    "kb_backend": 71,
    "kb_ingestion_worker": 49,
    "database_connector": 18,
    "flow_builder": 8,
    "workflow_runtime": 5,
    "sandbox_executor": 1,
    "platform_workers": 1,
}


class TestTheInventoryOnlyShrinks:
    def test_no_service_gains_unscoped_call_sites(self):
        _, unscoped, sites = _get_client_calls()
        regressions = {}
        for service, count in unscoped.items():
            budget = _UNSCOPED_BUDGET.get(service, 0)
            if count > budget:
                regressions[service] = (budget, count, sites[service][-3:])
        assert not regressions, (
            "new unscoped get_client() call site(s): "
            f"{regressions} — pass tenant_id, or lower the budget if these are "
            "genuinely platform-level (no tenant_id column)"
        )

    def test_the_budget_is_not_quietly_inflated(self):
        """A budget above the real count hides room for a regression to land in."""
        _, unscoped, _ = _get_client_calls()
        stale = {
            service: (budget, unscoped.get(service, 0))
            for service, budget in _UNSCOPED_BUDGET.items()
            if unscoped.get(service, 0) < budget
        }
        assert not stale, f"budget is above the real count — lower it: {stale}"

    def test_workflow_runtime_is_mostly_scoped(self):
        """The one service whose role is NOBYPASSRLS, so the one where an
        unscoped call returns zero rows rather than everything. Its scoped
        majority is what makes that survivable."""
        scoped, unscoped, _ = _get_client_calls()
        assert scoped["workflow_runtime"] > unscoped["workflow_runtime"] * 2


class TestTheScopedFormAdmitsWhenItCannotEnforce:
    """A scoped call on a bypassing role is worse than an unscoped one: the
    unscoped form at least looks like what it is."""

    def _client(self, bypasses):
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from libs.database_connector.postgres_connector import PostgresClient

        client = PostgresClient()
        object.__setattr__(client, "_session_factory", lambda: None)
        object.__setattr__(client, "_bypasses_rls", bypasses)
        return client

    def test_strict_mode_refuses_a_session_it_cannot_isolate(self, monkeypatch):
        pytest.importorskip("sqlalchemy", reason="database_connector needs sqlalchemy")
        monkeypatch.setenv("STRICT_TENANT_ISOLATION", "1")
        client = self._client(bypasses=True)

        with pytest.raises(RuntimeError, match="bypasses RLS"):
            client.get_client(tenant_id="11111111-1111-1111-1111-111111111111")

    def test_strict_mode_allows_a_session_it_can(self, monkeypatch):
        pytest.importorskip("sqlalchemy", reason="database_connector needs sqlalchemy")
        monkeypatch.setenv("STRICT_TENANT_ISOLATION", "1")
        client = self._client(bypasses=False)

        client.get_client(tenant_id="11111111-1111-1111-1111-111111111111")

    def test_default_mode_warns_rather_than_breaking_the_platform(self, monkeypatch, caplog):
        """Refusing by default would take every service down rather than
        isolate it — they all connect as a superuser today."""
        pytest.importorskip("sqlalchemy", reason="database_connector needs sqlalchemy")
        monkeypatch.delenv("STRICT_TENANT_ISOLATION", raising=False)
        import libs.database_connector.postgres_connector as pc
        monkeypatch.setattr(pc, "_WARNED_SCOPED_WITHOUT_RLS", False)

        client = self._client(bypasses=True)
        with caplog.at_level("WARNING"):
            client.get_client(tenant_id="11111111-1111-1111-1111-111111111111")

        assert any("NOT isolated" in r.message for r in caplog.records)

    def test_the_exposure_check_runs_at_connect_time(self):
        """Once per connection, not per query — it is a property of the role."""
        source = (REPO_ROOT / "backend" / "libs" / "database_connector" / "postgres_connector.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        open_fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "open"
        )
        called = {
            n.func.attr for n in ast.walk(open_fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "_measure_rls_exposure" in called

    def test_it_asks_for_both_superuser_and_bypassrls(self):
        """rolsuper alone is not enough: BYPASSRLS is a separate attribute and
        the migration's own documented rollback grants exactly that."""
        source = (REPO_ROOT / "backend" / "libs" / "database_connector" / "postgres_connector.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "_measure_rls_exposure"
        )
        sql = " ".join(
            n.value for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )
        assert "rolsuper" in sql
        assert "rolbypassrls" in sql


class TestTheRoleThatMakesRlsRealIsDeployable:
    def test_app_user_has_grants_and_cannot_bypass(self):
        """The migration creates app_user with no password and no privileges,
        and nothing ever supplied either — so the role RLS is written for could
        not be connected as even if a service wanted to."""
        sql = (INFRA / "sql" / "grant-app-user.sql").read_text(encoding="utf-8").upper()
        assert "NOBYPASSRLS" in sql
        assert "NOSUPERUSER" in sql
        assert "GRANT CONNECT" in sql
        assert "GRANT SELECT, INSERT, UPDATE, DELETE" in sql
        assert "ALTER DEFAULT PRIVILEGES" in sql, (
            "tables added by later migrations would not be readable"
        )

    def test_runtime_writer_is_still_nobypassrls(self):
        """The only role a service uses today that RLS applies to. If this
        regresses, the last place isolation is real goes quiet."""
        sql = (INFRA / "sql" / "grant-runtime-writer.sql").read_text(encoding="utf-8").upper()
        assert "ALTER ROLE RUNTIME_WRITER NOBYPASSRLS" in sql

    def test_the_switch_is_documented_where_an_operator_would_look(self):
        env = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        assert "APP_DATABASE_URL" in env
        assert "STRICT_TENANT_ISOLATION" in env


class TestTheContractTestDoesNotProveTheDeployment:
    """Pins the gap itself, so nobody reads a green RLS suite as isolation.

    If someone later points the services at app_user, this test should be
    updated — its failure would mean the situation improved.
    """

    def test_the_rls_suite_targets_app_user_which_no_service_connects_as(self):
        import re

        suite = (REPO_ROOT / "tests" / "integration" / "test_rls_enforcement.py").read_text(encoding="utf-8")
        assert "app_user" in suite

        # Connection strings live in the k8s manifests and the documented Secret.
        k8s = REPO_ROOT / "deploy" / "k8s"
        sources = [f for sub in ("", "database", "service", "job")
                   for f in sorted((k8s / sub).glob("*.yaml"))] + [k8s / "README.md"]
        dsn_lines = [
            line
            for path in sources
            for line in path.read_text(encoding="utf-8").splitlines()
            if re.search(r"(DATABASE_URL|POSTGRES_URL|POSTGRES_DSN|RUNTIME_DB_URL)", line)
        ]
        assert dsn_lines, "no service DSNs found — the check has drifted"
        assert not any("app_user" in line for line in dsn_lines), (
            "a service now connects as app_user — update this test and the "
            "unscoped budget above, because RLS just became load-bearing for it"
        )
