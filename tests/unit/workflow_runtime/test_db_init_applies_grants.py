"""The per-service role only exists if the init Job actually applies its SQL.

grant-roles.sql sitting in the repo proves nothing: it reaches Postgres through
a chain of four separate files, and a break anywhere leaves kb_backend without
a role while every manifest still looks correct.
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
DB_INIT = REPO_ROOT / "deploy" / "k8s" / "job" / "db-init.yaml"
SECRETS = REPO_ROOT / "deploy" / "k8s" / "create-secrets.sh"

SQL_CONFIGMAP = "db-init-sql"
SQL_MOUNT = "/sql"


def _db_init_documents() -> list[dict]:
    return [doc for doc in yaml.safe_load_all(DB_INIT.read_text(encoding="utf-8")) if doc]


def _db_init_job() -> dict:
    return next(doc for doc in _db_init_documents() if doc["kind"] == "Job")


def _db_init_container() -> dict:
    return _db_init_job()["spec"]["template"]["spec"]["containers"][0]


class TestTheGrantsReachPostgres:
    def test_the_configmap_carries_grant_roles_sql(self):
        assert re.search(
            rf'cm\s+"\$SVC"\s+{SQL_CONFIGMAP}[^\n]*\n(?:[^\n]*\n)*?[^\n]*grant-roles\.sql',
            CONFIGMAPS.read_text(encoding="utf-8"),
        ), f"grant-roles.sql is not packaged into the {SQL_CONFIGMAP} ConfigMap"

    def test_the_job_mounts_that_configmap(self):
        job = _db_init_job()
        volume = next(
            (v for v in job["spec"]["template"]["spec"]["volumes"]
             if v.get("configMap", {}).get("name") == SQL_CONFIGMAP),
            None,
        )
        assert volume, f"the Job does not mount the {SQL_CONFIGMAP} ConfigMap"

        mount = next(
            (m for m in _db_init_container()["volumeMounts"] if m["name"] == volume["name"]),
            None,
        )
        assert mount and mount["mountPath"] == SQL_MOUNT

    def test_the_job_executes_every_mounted_sql_file(self):
        command = _db_init_container()["command"][-1]
        assert f"{SQL_MOUNT}/*.sql" in command, "the Job never runs the mounted SQL"
        assert "psql" in command and "ON_ERROR_STOP=1" in command, (
            "a failed grant must stop the deploy rather than pass silently"
        )

    def test_the_job_waits_for_migrations_first(self):
        """grant-roles.sql grants on named tables, so it cannot run before the
        migration that creates them."""
        command = _db_init_container()["command"][-1]
        assert "information_schema.tables" in command and "until" in command


class TestRolePasswordsAreSetWhereRolesAreCreated:
    def test_every_role_the_job_alters_is_created_by_the_sql(self):
        altered = set(re.findall(r"ALTER ROLE (\w+)\s+WITH LOGIN PASSWORD", DB_INIT.read_text(encoding="utf-8")))
        created = set(re.findall(r"CREATE ROLE (\w+)", GRANTS_SQL.read_text(encoding="utf-8")))
        assert altered, "no role passwords are being set — did the Job change?"
        missing = sorted(altered - created)
        assert not missing, f"ALTER ROLE under ON_ERROR_STOP=1 for uncreated role(s): {missing}"

    def test_kb_backend_is_among_them(self):
        assert "CREATE ROLE kb_backend" in GRANTS_SQL.read_text(encoding="utf-8")
        assert "ALTER ROLE kb_backend" in DB_INIT.read_text(encoding="utf-8")

    def test_every_password_variable_is_supplied_by_the_secret(self):
        command = _db_init_container()["command"][-1]
        used = set(re.findall(r'\$([A-Z_]+_PASSWORD)', command))
        supplied = set(re.findall(r"--from-literal=([A-Z_]+_PASSWORD)=", SECRETS.read_text(encoding="utf-8")))
        missing = sorted(used - supplied)
        assert not missing, f"the Job reads {missing}, which create-secrets.sh never sets"
