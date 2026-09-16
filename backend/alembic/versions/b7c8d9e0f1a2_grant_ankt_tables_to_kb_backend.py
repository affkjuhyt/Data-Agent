"""grant the ankt tables to the kb_backend service role

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-09-17

The ankt migrations granted their tables to `app_user`, which is the role the
platform is moving toward — but kb-backend connects as `kb_backend`, and that
role is deliberately table-scoped: deploy/docker/sql/grant-roles.sql revokes
everything in `public` and then lists, table by table, what this service may
touch. Its comment says why: "a new table reaches this service only by being
added to a list below. That is the point" — kb-backend cannot read SecretsVault
or APIKeys at all, and a blanket grant would quietly end that.

So the ankt tables are added to the list rather than the list being widened.
Writes are included because the Nguồn dữ liệu screen runs connectors, which
append observations and releases; reads alone would make the button decorative.

Unlike `admin`, `kb_backend` is NOSUPERUSER NOBYPASSRLS, so this is the first
role for which the tenant_isolation policies on these tables actually do
something.

The same grants belong in grant-roles.sql so a freshly initialised database
matches; this migration is how an existing one catches up.
"""
from alembic import op

# Revision identifiers, used by Alembic.
revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None

_TABLES = (
    "variable", "source_document", "release", "observation",
    "indicator", "threshold", "source", "source_binding", "connector_run",
)
_ROLES = ("kb_backend", "runtime_writer")


def upgrade() -> None:
    for role in _ROLES:
        # Every role here is created by grant-roles.sql at database init, but a
        # migration that assumes so would fail on a database where one service
        # has not been provisioned yet.
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                    EXECUTE 'GRANT USAGE ON SCHEMA ankt TO {role}';
                END IF;
            END
            $$
            """,
        )
        for table in _TABLES:
            op.execute(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE
                                   ON ankt.{table} TO {role}';
                    END IF;
                END
                $$
                """,
            )


def downgrade() -> None:
    for role in _ROLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                    EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA ankt FROM {role}';
                    EXECUTE 'REVOKE USAGE ON SCHEMA ankt FROM {role}';
                END IF;
            END
            $$
            """,
        )
