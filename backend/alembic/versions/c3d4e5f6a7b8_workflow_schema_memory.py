"""workflow_schema_memory

Implements docs/STM_LTM_Memory_Architecture.docx §3.7 — the two memory
layers specific to a data task platform that section 3.1-3.5 (covered by
b2c3d4e5f6a7) does not address:

  * WorkflowMemory — Episodic Memory specific to data execution context.
    Records what task_type/pipeline was run, with what params, and whether
    it succeeded — so the agent can retrieve a similar past run instead of
    re-planning from scratch. PostgreSQL holds structured metadata; Qdrant
    (collection "workflow_memory", separate from "agent_memories") holds
    the task_description embedding for semantic search.
  * SchemaMemory — read-only (from the agent's POV) catalog of tables the
    agent is allowed to query: columns, types, business description,
    sample values. Written by Admin only, never by the agent. Retrieval is
    exact-match (tenant_id, data_source, table_name), not semantic search —
    so no Qdrant point for this one.

Both tables get the same tenant_isolation RLS policy as every other
tenant-scoped table (see a1b2c3d4e5f6 / b2c3d4e5f6a7 for the pattern this
copies).

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_APPLY_RLS_NEW_TABLES = r"""
DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['WorkflowMemory', 'SchemaMemory']
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO app_user', tbl);
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format($p$
            CREATE POLICY tenant_isolation ON %I
            AS PERMISSIVE
            FOR ALL
            TO PUBLIC
            USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        $p$, tbl);
    END LOOP;
END
$$;
"""

_REVERT_RLS_NEW_TABLES = r"""
DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['WorkflowMemory', 'SchemaMemory']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', tbl);
        EXECUTE format('ALTER TABLE %I NO FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('REVOKE ALL ON %I FROM app_user', tbl);
    END LOOP;
END
$$;
"""


def upgrade() -> None:
    # ─── WorkflowMemory (Episodic Memory for data execution context) ──────
    op.create_table(
        'WorkflowMemory',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('agent_id', sa.UUID(), nullable=False),
        sa.Column('task_type', sa.String(length=64), nullable=False),
        sa.Column('task_description', sa.Text(), nullable=False),
        sa.Column('param_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('outcome', sa.String(length=16), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('qdrant_point_id', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['Agents.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['Tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_workflowmemory_tenant', 'WorkflowMemory', ['tenant_id'])
    op.create_index('idx_workflowmemory_task_type', 'WorkflowMemory', ['tenant_id', 'agent_id', 'task_type'])
    op.create_index('idx_workflowmemory_created_at_desc', 'WorkflowMemory', [sa.text('created_at DESC')])

    # ─── SchemaMemory (Admin-managed, read-only to the agent) ─────────────
    op.create_table(
        'SchemaMemory',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('data_source', sa.String(length=64), nullable=False),
        sa.Column('table_name', sa.String(length=128), nullable=False),
        sa.Column('column_metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('business_description', sa.Text(), nullable=False),
        sa.Column('access_roles', postgresql.ARRAY(sa.String()), server_default='{}', nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['Tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'data_source', 'table_name', name='uq_schemamemory_table'),
    )
    op.create_index('idx_schemamemory_lookup', 'SchemaMemory', ['tenant_id', 'data_source'])

    op.execute(_APPLY_RLS_NEW_TABLES)


def downgrade() -> None:
    op.execute(_REVERT_RLS_NEW_TABLES)

    op.drop_index('idx_schemamemory_lookup', table_name='SchemaMemory')
    op.drop_table('SchemaMemory')

    op.drop_index('idx_workflowmemory_created_at_desc', table_name='WorkflowMemory')
    op.drop_index('idx_workflowmemory_task_type', table_name='WorkflowMemory')
    op.drop_index('idx_workflowmemory_tenant', table_name='WorkflowMemory')
    op.drop_table('WorkflowMemory')
