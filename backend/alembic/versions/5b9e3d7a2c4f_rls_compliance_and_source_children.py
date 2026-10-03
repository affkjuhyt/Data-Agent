"""rls_compliance_and_source_children

Row-level security for three tables that had none (10-database-design §2.2):

  * compliance_settings has a tenant_id: the standard tenant_isolation policy.
  * "KBSourceConfig" and "KBSourceRun" carry no tenant_id; they belong to a
    "KBSource", so a tenant_isolation_child policy walks source_id up to its
    tenant, as f1a2b3c4d5e6 did for the KB child tables.

Every service reading them now sets app.tenant_id: auth-api (get_session per
X-Tenant-Id), kb-backend (the request's tenant), the Gold PII-policy read, and
the web crawler, which walks the tenants for its run scans.

A policy subquery runs with the querying role's privileges, so the role that
reads "KBSourceConfig" without touching "KBSource" (kb_ingestion_worker,
storage_boundary.py) needs SELECT on it. deploy/docker/sql/grant-roles.sql
grants it; the grant is repeated here so the window between this migration and
the db-init Job does not fail promotions.

Revision ID: 5b9e3d7a2c4f
Revises: 4a7c2e9f1b3d
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5b9e3d7a2c4f"
down_revision: str | None = "4a7c2e9f1b3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TID = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

# table -> (policy name, predicate)
_POLICIES: dict[str, tuple[str, str]] = {
    "compliance_settings": (
        "tenant_isolation",
        "lower(tenant_id) = lower(NULLIF(current_setting('app.tenant_id', true), ''))",
    ),
    "KBSourceConfig": (
        "tenant_isolation_child",
        f'EXISTS (SELECT 1 FROM "KBSource" s '
        f'WHERE s.source_id = "KBSourceConfig".source_id AND s.tenant_id = {_TID})',
    ),
    "KBSourceRun": (
        "tenant_isolation_child",
        f'EXISTS (SELECT 1 FROM "KBSource" s '
        f'WHERE s.source_id = "KBSourceRun".source_id AND s.tenant_id = {_TID})',
    ),
}


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'kb_ingestion_worker') THEN "
        'GRANT SELECT ON "KBSource" TO kb_ingestion_worker; '
        "END IF; END $$"
    )
    for table, (policy, predicate) in _POLICIES.items():
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {policy} ON "{table}"')
        op.execute(
            f'CREATE POLICY {policy} ON "{table}" AS PERMISSIVE FOR ALL TO PUBLIC '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )


def downgrade() -> None:
    for table, (policy, _predicate) in _POLICIES.items():
        op.execute(f'DROP POLICY IF EXISTS {policy} ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    # The KBSource grant stays: grant-roles.sql owns it and gives it again anyway.
