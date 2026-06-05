"""grant_runtime_writer_permissions

Revision ID: a1b2c3d4e5f6
Revises: ff9ae8dcd7f7
Create Date: 2026-06-05 10:00:00.000000

"""
from typing import Sequence, Union
from alembic import op

revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'ff9ae8dcd7f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'runtime_writer') THEN
                CREATE ROLE runtime_writer WITH LOGIN PASSWORD 'runtime_dev_pw';
            END IF;
        END
        $$;
    """)
    op.execute("""
        GRANT SELECT ON
            "LLMProviders",
            "SystemPrompts",
            "Agents",
            "AgentVersions",
            "AgentKB",
            "KBConnections",
            "AgentMCP",
            "MCP",
            "Guardrails",
            "Tenants",
            "Members"
        TO runtime_writer;
    """)
    op.execute("""
        GRANT SELECT, INSERT ON
            "Conversations",
            "Messages",
            "AgentTraces",
            "AgentMemories"
        TO runtime_writer;
    """)
    op.execute("""
        GRANT SELECT ON
            "MemoryPolicies"
        TO runtime_writer;
    """)
    op.execute("""
        GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO runtime_writer;
    """)


def downgrade() -> None:
    op.execute('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM runtime_writer;')
    op.execute('DROP ROLE IF EXISTS runtime_writer;')
