"""Check that tenant scoping is enforced by the connection path and deployment.

The V3 schema uses one `svc_<schema>` role per data domain. `scoped_session` sets the
transaction-local organisation and user settings, while the role's NOBYPASSRLS attribute makes
the database policies effective.

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

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
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
                parts = path.relative_to(REPO_ROOT).parts
                service = parts[2]   # backend/<services|libs>/<name>
                # The KB Kafka workers live inside kb_backend but, unlike its API,
                # do not set the tenant per request: keep them a separate unit so
                # kb_backend's ambient-tenant exemption does not hide them.
                if parts[2] == "kb_backend" and parts[3] == "workers":
                    service = f"kb_worker_{parts[4]}"
                if any(k.arg == "tenant_id" for k in node.keywords) or node.args:
                    scoped[service] += 1
                else:
                    unscoped[service] += 1
                    sites[service].append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    return scoped, unscoped, sites


# Services that set the tenant once per request (postgres_connector's
# set_current_tenant) so a bare get_client() is already scoped. Counting their
# call sites measures nothing; what matters there is how often they opt OUT,
# which the platform-client test below caps instead.
_AMBIENT_TENANT_SERVICES = {"kb_backend"}

# The inventory as it stands. A ratchet, not a target: the number may go DOWN as
# services adopt tenant scoping, and a rise means a new unscoped call site was
# added to a surface that already cannot isolate.
_UNSCOPED_BUDGET = {
    # kb_ingestion_worker's 49 split across the four processes and libs/ingestion.
    # 32: the Silver tier update/rollback, the auto-Gold hand-off read and the
    # trash-flush graph lookup now pass the tenant they already had. 31: the Gold
    "ingestion": 24,
    "kb_worker_conflict_resolution": 16,
    "kb_worker_promote_gold": 2,
    "kb_worker_structured_data": 2,
    "kb_worker_promote_silver": 1,
    # libs/shared is at 0: the document advisory lock moved to
    # get_platform_client(), the deliberate unscoped form.
    "database_connector": 15,
    # 5 + mongo_client's save_canvas/load_canvas (ex flow_builder): Mongo has no
    # tenant scoping to pass, the canvas routes gate them on
    # workflow_version_belongs_to instead. jobs/memory_cleanup.py's 3 are gone:
    # it runs as app_user and cleans one tenant at a time. 6: the ROI scheduler
    # walks "Tenants" instead of scanning PlatformSettings unscoped.
    "workflow_runtime": 6,
    "sandbox_executor": 1,
    "platform_workers": 1,
}


class TestTheInventoryOnlyShrinks:
    def test_no_service_gains_unscoped_call_sites(self):
        _, unscoped, sites = _get_client_calls()
        regressions = {}
        for service, count in unscoped.items():
            if service in _AMBIENT_TENANT_SERVICES:
                continue
            budget = _UNSCOPED_BUDGET.get(service, 0)
            if count > budget:
                regressions[service] = (budget, count, sites[service][-3:])
        assert not regressions, (
            "new unscoped get_client() call site(s): "
            f"{regressions} — pass tenant_id, or lower the budget if these are "
            "genuinely platform-level (no tenant_id column)"
        )

    def test_opting_out_of_tenant_scoping_stays_rare(self):
        """get_platform_client() is the deliberate escape hatch. It is correct
        for genuinely global tables (RolePermissions, information_schema) and a
        cross-tenant read anywhere else, so the count is capped rather than
        merely allowed."""
        opt_outs = []
        for service in _AMBIENT_TENANT_SERVICES:
            root = REPO_ROOT / "backend" / "services" / service
            for path in root.rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8", errors="ignore"))):
                    is_opt_out = (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "get_platform_client"
                    )
                    if is_opt_out:
                        opt_outs.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

        assert len(opt_outs) <= 2, (
            f"get_platform_client() is used {len(opt_outs)} times: {opt_outs} — each one "
            "reads across every tenant, so it belongs only on tables with no tenant_id"
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


class TestV3RolesAreDeployable:
    def test_grant_sql_keeps_cluster_roles_and_nobypassrls(self):
        sql = (INFRA / "sql" / "grant-roles.sql").read_text(encoding="utf-8").upper()
        assert "NOBYPASSRLS" in sql
        assert "NOSUPERUSER" in sql
        assert "GRANT CONNECT" in sql
        assert "PGBOUNCER_AUTH" in sql
        assert "APP_USER" not in sql
        assert "RUNTIME_WRITER" not in sql

    def test_db_init_waits_for_the_v3_schema(self):
        for path in (
            REPO_ROOT / "deploy" / "k8s" / "dev" / "job" / "db-init.yaml",
            REPO_ROOT / "deploy" / "k8s" / "production" / "job" / "db-init.yaml",
        ):
            text = path.read_text(encoding="utf-8")
            assert "org.organization" in text
            assert "Agents" not in text

    def test_v3_role_passwords_are_supplied_as_json(self):
        create_secrets = (REPO_ROOT / "deploy" / "k8s" / "create-secrets.sh").read_text(encoding="utf-8")
        env_example = (REPO_ROOT / "deploy" / "k8s" / "secrets.env.example").read_text(encoding="utf-8")
        assert "DB_ROLE_PASSWORDS" in create_secrets
        assert "DB_ROLE_PASSWORDS={}" in env_example

    def test_no_role_password_is_hardcoded_in_sql(self):
        """A password in the SQL cannot match the one the DSN carries. The
        db-init Job sets every role password from the secret instead."""
        offenders = []
        for f in sorted((INFRA / "sql").glob("*.sql")):
            body = f.read_text(encoding="utf-8")
            for line in body.splitlines():
                stripped = line.strip()
                if stripped.startswith("--"):
                    continue
                if "PASSWORD '" in stripped.upper():
                    offenders.append(f"{f.name}: {stripped}")
        assert not offenders, f"hardcoded role password: {offenders}"
