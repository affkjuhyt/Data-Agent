"""The per-service role only exists if the init Job actually applies its SQL.

grant-roles.sql sitting in the repo proves nothing: it reaches Postgres through
a chain of four separate files, and a break anywhere leaves the svc_ roles
without a login while every manifest still looks correct.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
GRANTS_SQL = REPO_ROOT / "deploy" / "docker" / "sql" / "grant-roles.sql"
CONFIGMAPS = REPO_ROOT / "deploy" / "k8s" / "create-configmaps.sh"
# Both manifest sets are deployable (deploy.yml picks one per inventory), and
# each carries its own copy of the Job.
DB_INITS = {
    manifest_set: REPO_ROOT / "deploy" / "k8s" / manifest_set / "job" / "db-init.yaml"
    for manifest_set in ("production", "dev")
}
SECRETS = REPO_ROOT / "deploy" / "k8s" / "create-secrets.sh"
MIGRATIONS = REPO_ROOT / "backend" / "alembic" / "versions"

SQL_CONFIGMAP = "db-init-sql"
SQL_MOUNT = "/sql"


@pytest.fixture(params=sorted(DB_INITS))
def db_init(request) -> pathlib.Path:
    return DB_INITS[request.param]


def _db_init_job(db_init: pathlib.Path) -> dict:
    documents = yaml.safe_load_all(db_init.read_text(encoding="utf-8"))
    return next(doc for doc in documents if doc and doc["kind"] == "Job")


def _db_init_container(db_init: pathlib.Path) -> dict:
    return _db_init_job(db_init)["spec"]["template"]["spec"]["containers"][0]


class TestTheGrantsReachPostgres:
    def test_the_configmap_carries_grant_roles_sql(self):
        assert re.search(
            rf'cm\s+"\$SVC"\s+{SQL_CONFIGMAP}[^\n]*\n(?:[^\n]*\n)*?[^\n]*grant-roles\.sql',
            CONFIGMAPS.read_text(encoding="utf-8"),
        ), f"grant-roles.sql is not packaged into the {SQL_CONFIGMAP} ConfigMap"

    def test_the_job_mounts_that_configmap(self, db_init):
        job = _db_init_job(db_init)
        volume = next(
            (
                v
                for v in job["spec"]["template"]["spec"]["volumes"]
                if v.get("configMap", {}).get("name") == SQL_CONFIGMAP
            ),
            None,
        )
        assert volume, f"the Job does not mount the {SQL_CONFIGMAP} ConfigMap"

        mount = next(
            (m for m in _db_init_container(db_init)["volumeMounts"] if m["name"] == volume["name"]),
            None,
        )
        assert mount and mount["mountPath"] == SQL_MOUNT

    def test_the_job_executes_every_mounted_sql_file(self, db_init):
        command = _db_init_container(db_init)["command"][-1]
        assert f"{SQL_MOUNT}/*.sql" in command, "the Job never runs the mounted SQL"
        assert (
            "psql" in command and "ON_ERROR_STOP=1" in command
        ), "a failed grant must stop the deploy rather than pass silently"

    def test_the_job_waits_for_migrations_first(self, db_init):
        """grant-roles.sql loops over the svc_ roles the V3 baseline creates, so it
        cannot run before that migration: the Job polls for the baseline's first
        table until it exists."""
        command = _db_init_container(db_init)["command"][-1]
        assert re.search(
            r"until psql[^\n]*to_regclass\('org\.organization'\)", command
        ), "the Job does not wait for the V3 baseline before applying its SQL"
        assert command.index("to_regclass") < command.index(f"{SQL_MOUNT}/*.sql")


class TestRolePasswordsAreSetWhereRolesAreCreated:
    def test_every_role_the_job_alters_is_created_by_the_sql(self, db_init):
        """Under ON_ERROR_STOP=1 an ALTER ROLE on a missing role fails the Job. A
        role counts as created when grant-roles.sql or a migration creates it:
        the Job waits for the migrations (alembic's env.py runs them in one
        transaction, so the baseline table it polls appears together with the
        roles the later revisions create — app_user, v3_0003)."""
        altered = set(
            re.findall(
                r"ALTER ROLE (\w+)\s+WITH LOGIN PASSWORD", db_init.read_text(encoding="utf-8")
            )
        )
        created = set(re.findall(r"CREATE ROLE (\w+)", GRANTS_SQL.read_text(encoding="utf-8")))
        for migration in sorted(MIGRATIONS.glob("*.py")):
            created |= set(re.findall(r"CREATE ROLE (\w+)", migration.read_text(encoding="utf-8")))
        assert altered, "no role passwords are being set — did the Job change?"
        missing = sorted(altered - created)
        assert not missing, f"ALTER ROLE under ON_ERROR_STOP=1 for uncreated role(s): {missing}"

    def test_the_svc_roles_get_their_passwords_from_db_role_passwords(self, db_init):
        """The migration creates the svc_ roles without a password; the Job is the
        only place they become logins, one per svc_ key of the DB_ROLE_PASSWORDS JSON."""
        command = _db_init_container(db_init)["command"][-1]
        assert 'passwords="$DB_ROLE_PASSWORDS"' in command
        assert re.search(
            r"format\('ALTER ROLE %I WITH LOGIN PASSWORD %L', key, value\)\s*"
            r"FROM jsonb_each_text\(:'passwords'::jsonb\)\s*"
            r"WHERE key LIKE 'svc\\_%'\s*\\gexec",
            command,
        ), "the Job no longer turns the svc_ roles of DB_ROLE_PASSWORDS into logins"

    def test_pgbouncer_auth_gets_its_password(self, db_init):
        command = _db_init_container(db_init)["command"][-1]
        assert 'pgbouncer_auth_password="$PGBOUNCER_AUTH_PASSWORD"' in command
        assert "ALTER ROLE pgbouncer_auth WITH LOGIN PASSWORD :'pgbouncer_auth_password'" in command

    def test_every_password_variable_is_supplied_by_the_secret(self, db_init):
        container = _db_init_container(db_init)
        command = container["command"][-1]
        # Whole names: a `_PASSWORD` suffix match reads $DB_ROLE_PASSWORDS as
        # DB_ROLE_PASSWORD and reports a variable nobody uses.
        used = set(re.findall(r"\$\{?([A-Z_]*PASSWORDS?)\b", command))
        assert {"DB_ROLE_PASSWORDS", "PGBOUNCER_AUTH_PASSWORD"} <= used
        secret = next(e["secretRef"]["name"] for e in container["envFrom"] if "secretRef" in e)
        # Only that secret's keys count: the same name set on another secret never
        # reaches this Job.
        block = re.search(
            rf"create secret generic {re.escape(secret)} \\\n((?:[^\n]*\\\n)*)",
            SECRETS.read_text(encoding="utf-8"),
        )
        assert block, f"create-secrets.sh never creates {secret}"
        supplied = set(re.findall(r"--from-literal=([A-Z_]+)=", block.group(1)))
        missing = sorted(used - supplied)
        assert (
            not missing
        ), f"the Job reads {missing}, which create-secrets.sh never puts in {secret}"
