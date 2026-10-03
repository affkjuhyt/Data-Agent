"""ingest_raw_tables_use_rls_helper

ingest.artifact and ingest.fetch (9f3c7a1e5d2b, S2-02) were created with a
hand-written FOR ALL tenant_isolation policy before the NOP-148 helper existed.
V2 tables take their row-level security from libs/shared/tenant_rls.py
(docs/v2/10 §4), so they move to it: per-command policies, no DELETE policy
(append-only, ADR-V2-30; no role holds UPDATE or DELETE either), PUBLIC revoked.

Revision ID: a2c4e6f8b0d1
Revises: 9f3c7a1e5d2b
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

from libs.shared.tenant_rls import disable_tenant_rls, enable_tenant_rls

revision: str = "a2c4e6f8b0d1"
down_revision: str | None = "9f3c7a1e5d2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("artifact", "fetch")
_TID = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON ingest.{table}")
        for sql in enable_tenant_rls("ingest", table):
            op.execute(sql)


def downgrade() -> None:
    for table in _TABLES:
        for sql in disable_tenant_rls("ingest", table):
            op.execute(sql)
        op.execute(f"ALTER TABLE ingest.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE ingest.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON ingest.{table} AS PERMISSIVE FOR ALL TO PUBLIC "
            f"USING (tenant_id = {_TID}) WITH CHECK (tenant_id = {_TID})"
        )
