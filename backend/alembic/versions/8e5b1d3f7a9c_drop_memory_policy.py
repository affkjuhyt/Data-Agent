"""drop_memory_policy

"MemoryPolicy" gated memory extraction: a turn was only sent to the memory
worker when its agent had an enabled row with action_type 'add'. No API, seed
or job ever wrote the table (0 rows on the dev cluster, 02/10/2026), so no turn
was ever extracted and AgentMemories, EpisodicMemory and FutureMemory stayed
empty even for agents with Memory switched on. AgentVersions.memory_enabled —
the Memory toggle in the agent screen — is now the one switch, and the table
goes. condition and created_by were never read.

Downgrade recreates the table as 4e8a1c6d2b9f left it (no rows; ix_*
twin index already dropped by 9d4f2b7c1e3a).

Revision ID: 8e5b1d3f7a9c
Revises: 7a3c9e1f5b2d
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "8e5b1d3f7a9c"
down_revision: str | None = "7a3c9e1f5b2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "MemoryPolicy"')


def _timestamp(name: str) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def downgrade() -> None:
    op.create_table(
        "MemoryPolicy",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("agent_id", sa.UUID(), nullable=False),
        sa.Column("action_type", sa.String(length=20), nullable=False),
        sa.Column("condition", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        _timestamp("created_at"),
        _timestamp("inserted_at"),
        sa.ForeignKeyConstraint(["agent_id"], ["Agents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_memorypolicy_agent", "MemoryPolicy", ["agent_id"], unique=False)
    op.create_index(
        "idx_memorypolicy_inserted_at_desc",
        "MemoryPolicy",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
