"""stm_ltm_memory_architecture

Implements docs/STM_LTM_Memory_Architecture.docx for the workflow-runtime
agent:
  * AgentMemories (Semantic LTM) gains domain/fact_key controlled-vocabulary
    columns + a unique constraint so writes can use ON CONFLICT DO UPDATE
    for the ADD/UPDATE/NO_CHANGE decisions.
  * EpisodicMemory (past events) and FutureMemory (scheduled intents) are
    new tables — these LTM types did not exist before.
  * MemoryRetentionConfig makes retention/cap configurable per tenant+domain
    instead of the single global MEMORY_RETENTION_DAYS env var.
  * New tables get the same tenant_isolation RLS policy that
    a1b2c3d4e5f6_enable_tenant_rls.py applied to every other tenant-scoped
    table (that migration already ran, so it won't pick these up itself).

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_TENANT_SCOPED_TABLES = ("EpisodicMemory", "FutureMemory", "MemoryRetentionConfig")

# Same shape as _APPLY_RLS in a1b2c3d4e5f6_enable_tenant_rls.py, but scoped to
# just the tables created in this migration (the dynamic-loop version there
# already ran against the schema as it existed at that time).
_APPLY_RLS_NEW_TABLES = r"""
DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['EpisodicMemory', 'FutureMemory', 'MemoryRetentionConfig']
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
    FOREACH tbl IN ARRAY ARRAY['EpisodicMemory', 'FutureMemory', 'MemoryRetentionConfig']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', tbl);
        EXECUTE format('ALTER TABLE %I NO FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('REVOKE ALL ON %I FROM app_user', tbl);
    END LOOP;
END
$$;
"""

# Seed sane per-domain defaults (doc §3.3) for every tenant that already
# exists. New tenants created later fall back to the hardcoded defaults in
# jobs/memory_cleanup.py when no row is present for their (tenant, domain).
_SEED_RETENTION_CONFIG = """
INSERT INTO "MemoryRetentionConfig" (tenant_id, domain, retention_days, max_records_per_user)
SELECT id, 'user_pref', NULL, 200 FROM "Tenants"
UNION ALL
SELECT id, 'general', 30, 500 FROM "Tenants"
ON CONFLICT (tenant_id, domain) DO NOTHING;
"""


def upgrade() -> None:
    # ─── AgentMemories: domain/fact_key controlled vocabulary ─────────────
    op.add_column('AgentMemories', sa.Column('domain', sa.String(length=64), server_default='general', nullable=False))
    op.add_column('AgentMemories', sa.Column('fact_key', sa.String(length=128), nullable=True))
    op.create_unique_constraint(
        'uq_agentmemories_fact',
        'AgentMemories',
        ['tenant_id', 'agent_id', 'scope', 'user_ref', 'domain', 'fact_key'],
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        'idx_agentmemories_user_domain',
        'AgentMemories',
        ['tenant_id', 'agent_id', 'user_ref', 'domain'],
    )

    # ─── EpisodicMemory (Episodic Past LTM) ────────────────────────────────
    op.create_table(
        'EpisodicMemory',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('agent_id', sa.UUID(), nullable=False),
        sa.Column('user_ref', sa.String(length=255), nullable=True),
        sa.Column('domain', sa.String(length=64), server_default='general', nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('importance_score', sa.Float(), server_default='0.5', nullable=False),
        sa.Column('embedding_id', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='active', nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['Agents.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['Tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_episodicmemory_tenant', 'EpisodicMemory', ['tenant_id'])
    op.create_index('idx_episodicmemory_user_time', 'EpisodicMemory', ['user_ref', sa.text('created_at DESC')])
    op.create_index(
        'idx_episodicmemory_expires',
        'EpisodicMemory',
        ['expires_at'],
        postgresql_where=sa.text('expires_at IS NOT NULL'),
    )

    # ─── FutureMemory (Episodic Future LTM — prospective memory) ───────────
    op.create_table(
        'FutureMemory',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('agent_id', sa.UUID(), nullable=False),
        sa.Column('user_ref', sa.String(length=255), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('execution_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('recurrence', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=16), server_default='pending', nullable=False),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['agent_id'], ['Agents.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['Tenants.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_futurememory_tenant', 'FutureMemory', ['tenant_id'])
    op.create_index('idx_futurememory_user_status_time', 'FutureMemory', ['user_ref', 'status', 'execution_time'])

    # ─── MemoryRetentionConfig (per tenant+domain retention/cap) ───────────
    op.create_table(
        'MemoryRetentionConfig',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('domain', sa.String(length=64), nullable=False),
        sa.Column('retention_days', sa.Integer(), nullable=True),
        sa.Column('max_records_per_user', sa.Integer(), server_default='500', nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), onupdate=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['Tenants.id'], ),
        sa.PrimaryKeyConstraint('tenant_id', 'domain'),
    )

    op.execute(_APPLY_RLS_NEW_TABLES)
    op.execute(_SEED_RETENTION_CONFIG)


def downgrade() -> None:
    op.execute(_REVERT_RLS_NEW_TABLES)

    op.drop_table('MemoryRetentionConfig')

    op.drop_index('idx_futurememory_user_status_time', table_name='FutureMemory')
    op.drop_index('idx_futurememory_tenant', table_name='FutureMemory')
    op.drop_table('FutureMemory')

    op.drop_index('idx_episodicmemory_expires', table_name='EpisodicMemory')
    op.drop_index('idx_episodicmemory_user_time', table_name='EpisodicMemory')
    op.drop_index('idx_episodicmemory_tenant', table_name='EpisodicMemory')
    op.drop_table('EpisodicMemory')

    op.drop_index('idx_agentmemories_user_domain', table_name='AgentMemories')
    op.drop_constraint('uq_agentmemories_fact', 'AgentMemories', type_='unique')
    op.drop_column('AgentMemories', 'fact_key')
    op.drop_column('AgentMemories', 'domain')
