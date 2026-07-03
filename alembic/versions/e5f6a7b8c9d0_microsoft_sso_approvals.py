"""microsoft_sso_approvals

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-09 10:00:00.000000

Renamed from b2c3d4e5f6a7 — that id collided with
b2c3d4e5f6a7_stm_ltm_memory_architecture.py (both independently branched off
a1b2c3d4e5f6 and happened to pick the same hex id), which made `alembic
upgrade head` fail with "Multiple head revisions are present" for every
migration on top of either branch, including this repo's existing
STM/LTM/Workflow/Schema-Memory chain. Re-chained linearly after
d4e5f6a7b8c9 (this migration's content is unrelated to and doesn't depend
on any of that work) since this repo's whole migration history is linear —
no other branch_labels/merge revisions exist to model parallel branches.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'microsoft_sso_approvals',
        sa.Column('id', sa.Text(), server_default=sa.text("gen_random_uuid()::text"), nullable=False),
        sa.Column('keycloak_user_id', sa.Text(), nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('display_name', sa.Text(), server_default='', nullable=False),
        sa.Column('status', sa.Text(), server_default='pending', nullable=False),
        sa.Column('requested_role', sa.Text(), nullable=True),
        sa.Column('requested_tenant_id', sa.Text(), nullable=True),
        sa.Column('reviewed_by', sa.Text(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('keycloak_user_id', name='uq_ms_approvals_kc_user'),
        sa.CheckConstraint("status IN ('pending','approved','rejected')", name='ck_ms_approvals_status'),
    )
    op.create_index('idx_ms_approvals_status_created', 'microsoft_sso_approvals', ['status', 'created_at'])


def downgrade() -> None:
    op.drop_index('idx_ms_approvals_status_created', 'microsoft_sso_approvals')
    op.drop_table('microsoft_sso_approvals')
