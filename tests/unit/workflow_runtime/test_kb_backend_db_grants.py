"""kb_backend's Postgres role is table-scoped, so the grant list is a contract.

A table adopted by the service without a matching GRANT fails at runtime with
"permission denied", in whichever request happens to touch it first. These
tests move that failure to CI.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
GRANTS = REPO_ROOT / "deploy" / "docker" / "sql" / "grant-roles.sql"
SERVICE = REPO_ROOT / "backend" / "services" / "kb_backend"
MIGRATIONS = REPO_ROOT / "backend" / "alembic" / "versions"
# Each deployable manifest set carries its own db-init Job.
DB_INITS = {
    manifest_set: REPO_ROOT / "deploy" / "k8s" / manifest_set / "job" / "db-init.yaml"
    for manifest_set in ("production", "dev")
}


@pytest.fixture(params=sorted(DB_INITS))
def db_init(request) -> pathlib.Path:
    return DB_INITS[request.param]

WRITE_TABLES = {
    "KBData", "KBTextBlock", "KBTextBlockVersion", "KBTextTable", "KBTable",
    "KBKnowledgeBase", "KBLifecycleHistory", "KBConflict", "KBConflictBatch",
    "KBFilterPolicy", "KBExtractionPolicy", "KBWarehouse", "KBWarehouseConfig",
    "KBStructuredImportJob", "KBQdrantCollection",
}
READ_TABLES = {
    "KBQdrantConnection", "KBNeo4jNode", "KBNeo4jRelationship",
    "KBEntityLookup", "RolePermissions",
}


def _granted_tables(privilege: str) -> set[str]:
    """Tables granted to kb_backend under a GRANT starting with `privilege`."""
    sql = GRANTS.read_text(encoding="utf-8")
    pattern = rf"GRANT {privilege}\s+ON\s+([^;]*?)\s*TO kb_backend;"
    granted: set[str] = set()
    for match in re.finditer(pattern, sql, re.S):
        granted |= set(re.findall(r'"([A-Za-z0-9_]+)"', match.group(1)))
    return granted


def _tables_used_by_service() -> set[str]:
    source = " ".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in SERVICE.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    orm_dir = REPO_ROOT / "backend" / "libs" / "basemodel" / "services_databaseconnector" / "postgres_orm"
    used: set[str] = set()
    for orm_file in orm_dir.glob("*.py"):
        # Split per class: __tablename__ is not always the line after the class.
        for chunk in orm_file.read_text(encoding="utf-8", errors="ignore").split("\nclass ")[1:]:
            declared = re.match(r"(\w+)\(Base\)", chunk)
            table = re.search(r'__tablename__ = "([^"]+)"', chunk)
            if declared and table and re.search(rf"\b{declared.group(1)}\b", source):
                used.add(table.group(1))
    for model in re.findall(r"\b(KB\w+|RolePermissions)(?:Insert|Update|Delete)\(", source):
        used.add(model)
    # ReadJoinRequest / WhereFilter name their tables as strings, not ORM classes.
    for quoted in re.findall(r'(?:joins_table=\[|table_name=)"?\[?"([A-Za-z0-9_]+)"', source):
        used.add(quoted)
    return used


class TestTheGrantListCoversWhatTheServiceUses:
    def test_every_table_the_service_touches_is_granted(self):
        granted = _granted_tables("SELECT, INSERT, UPDATE, DELETE") | _granted_tables("SELECT")
        missing = sorted(_tables_used_by_service() - granted)
        assert not missing, (
            f"kb_backend uses these tables but its role is not granted them: {missing} "
            "- add them to deploy/docker/sql/grant-roles.sql"
        )

    def test_no_table_is_granted_that_the_service_does_not_use(self):
        granted = _granted_tables("SELECT, INSERT, UPDATE, DELETE") | _granted_tables("SELECT")
        unused = sorted(granted - _tables_used_by_service())
        assert not unused, f"granted but unused, so the scope is wider than it needs to be: {unused}"

    def test_read_only_tables_are_not_writable(self):
        writable = _granted_tables("SELECT, INSERT, UPDATE, DELETE")
        overlap = sorted(writable & READ_TABLES)
        assert not overlap, f"granted write access to read-only tables: {overlap}"

    def test_the_expected_split_is_what_the_file_says(self):
        assert _granted_tables("SELECT, INSERT, UPDATE, DELETE") == WRITE_TABLES
        assert _granted_tables("SELECT") == READ_TABLES


class TestTheRoleCannotBypassIsolation:
    def test_kb_backend_is_created_without_superuser_or_bypassrls(self):
        sql = GRANTS.read_text(encoding="utf-8")
        assert "ALTER ROLE kb_backend NOSUPERUSER;" in sql
        assert "ALTER ROLE kb_backend NOBYPASSRLS;" in sql

    def test_kb_backend_gets_no_blanket_grant_on_public(self):
        """app_user has ALL TABLES and ALTER DEFAULT PRIVILEGES; the whole point
        of a per-service role is that it has neither."""
        sql = GRANTS.read_text(encoding="utf-8")
        assert "ON ALL TABLES IN SCHEMA public TO kb_backend" not in sql
        assert "ON TABLES TO kb_backend" not in sql

    def test_every_granted_table_actually_exists(self):
        """A typo here fails the db-init Job under ON_ERROR_STOP=1 and blocks the deploy."""
        created: set[str] = set()
        for migration in MIGRATIONS.glob("*.py"):
            created |= set(re.findall(
                r"""op\.create_table\(\s*['"]([A-Za-z0-9_]+)['"]""",
                migration.read_text(encoding="utf-8"),
            ))
        granted = _granted_tables("SELECT, INSERT, UPDATE, DELETE") | _granted_tables("SELECT")
        assert not sorted(granted - created)


class TestTheInitJobActuallyAppliesTheGrants:
    """grant-roles.sql only matters if something runs it. The chain is
    create-configmaps.sh -> ConfigMap -> Job volume -> mount -> psql loop, and a
    break anywhere leaves kb_backend with a role that has no privileges."""

    def _job(self, db_init: pathlib.Path) -> dict:
        import yaml
        documents = yaml.safe_load_all(db_init.read_text(encoding="utf-8"))
        return next(d for d in documents if d and d["kind"] == "Job")

    def test_the_sql_file_is_packaged_into_a_configmap(self):
        script = (REPO_ROOT / "deploy" / "k8s" / "create-configmaps.sh").read_text(encoding="utf-8")

        # Join each `cm <ns> <name> \ ...` invocation back into one line.
        blocks, current = [], ""
        for line in script.split("\n"):
            stripped = line.strip()
            if current or stripped.startswith("cm "):
                current += " " + stripped.rstrip("\\")
                if not stripped.endswith("\\"):
                    blocks.append(current.strip())
                    current = ""

        packaging = [b for b in blocks if "grant-roles.sql" in b]
        assert packaging, "grant-roles.sql is not added to any ConfigMap"
        assert re.match(r'cm\s+"?\$?\w+"?\s+db-init-sql\b', packaging[0]), packaging[0]

    def test_the_job_mounts_that_configmap(self, db_init):
        spec = self._job(db_init)["spec"]["template"]["spec"]
        volume = next(v for v in spec["volumes"] if v.get("configMap", {}).get("name") == "db-init-sql")
        mount = next(m for m in spec["containers"][0]["volumeMounts"] if m["name"] == volume["name"])
        assert mount["mountPath"] == "/sql"

    def test_the_job_executes_every_sql_file_it_mounts(self, db_init):
        command = self._job(db_init)["spec"]["template"]["spec"]["containers"][0]["command"][-1]
        assert "/sql/*.sql" in command
        assert "psql" in command and "ON_ERROR_STOP=1" in command

    def test_grants_run_before_the_role_passwords_are_set(self, db_init):
        """ALTER ROLE would fail on a role grant-roles.sql has not created yet."""
        command = self._job(db_init)["spec"]["template"]["spec"]["containers"][0]["command"][-1]
        assert command.index("/sql/*.sql") < command.index("ALTER ROLE")


class TestRolesTheInitJobExpects:
    def test_every_role_given_a_password_is_also_created(self, db_init):
        """db-init runs ALTER ROLE under ON_ERROR_STOP=1, so a role it sets a
        password for must exist by then or the deploy stops."""
        job = db_init.read_text(encoding="utf-8")
        sql = GRANTS.read_text(encoding="utf-8")

        altered = set(re.findall(r"ALTER ROLE (\w+)\s+WITH LOGIN PASSWORD", job))
        created = set(re.findall(r"CREATE ROLE (\w+)", sql))
        assert altered, "no ALTER ROLE lines found - did db-init.yaml move?"
        assert not sorted(altered - created)
