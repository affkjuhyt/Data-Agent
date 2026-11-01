"""Check that the roles which make tenant scoping real are deployable.

The V3 schema uses one `svc_<schema>` role per data domain. `scoped_session` sets the
transaction-local organisation and user settings, while the role's NOBYPASSRLS attribute makes
the database policies effective. A policy is only as strong as the role it runs under, so these
checks are about the deployment: the roles exist, cannot bypass RLS, and get their passwords from
the secret rather than from SQL.
"""

from __future__ import annotations

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
INFRA = REPO_ROOT / "deploy" / "docker"

pytestmark = pytest.mark.unit


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
        create_secrets = (REPO_ROOT / "deploy" / "k8s" / "create-secrets.sh").read_text(
            encoding="utf-8"
        )
        env_example = (REPO_ROOT / "deploy" / "k8s" / "secrets.env.example").read_text(
            encoding="utf-8"
        )
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
