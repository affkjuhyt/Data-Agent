"""rls_runtime_child_tables

Row-level security for the agent-runtime tables that carry no tenant_id and had
none (10-database-design §2.2). Each gets a tenant_isolation_child policy that
walks its foreign keys up to a tenant-scoped parent, as f1a2b3c4d5e6 did for the
KB children:

    Messages          -> Conversations (conversation_id)
    AgentTraces       -> Messages (message_id) -> Conversations
    Workflows         -> Agents (agent_id)
    WorkflowVersions  -> Workflows (workflow_id) -> Agents

Their readers and writers set app.tenant_id: workflow-runtime passes the tenant
to every session (save_message / save_trace now take it), the memory worker
scopes each extraction event. Every role reading them (runtime_writer,
app_user) can already read the parents the policies look up.

Revision ID: 7d2a5f9c4e6b
Revises: 6c1f4a8e3b5d
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7d2a5f9c4e6b"
down_revision: str | None = "6c1f4a8e3b5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TID = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

_CHILD_PREDICATES: dict[str, str] = {
    "Messages": (
        f'EXISTS (SELECT 1 FROM "Conversations" c '
        f'WHERE c.id = "Messages".conversation_id AND c.tenant_id = {_TID})'
    ),
    "AgentTraces": (
        f'EXISTS (SELECT 1 FROM "Messages" m JOIN "Conversations" c ON c.id = m.conversation_id '
        f'WHERE m.id = "AgentTraces".message_id AND c.tenant_id = {_TID})'
    ),
    "Workflows": (
        f'EXISTS (SELECT 1 FROM "Agents" a '
        f'WHERE a.id = "Workflows".agent_id AND a.tenant_id = {_TID})'
    ),
    "WorkflowVersions": (
        f'EXISTS (SELECT 1 FROM "Workflows" w JOIN "Agents" a ON a.id = w.agent_id '
        f'WHERE w.id = "WorkflowVersions".workflow_id AND a.tenant_id = {_TID})'
    ),
}
_POLICY = "tenant_isolation_child"


def upgrade() -> None:
    for table, predicate in _CHILD_PREDICATES.items():
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS {_POLICY} ON "{table}"')
        op.execute(
            f'CREATE POLICY {_POLICY} ON "{table}" AS PERMISSIVE FOR ALL TO PUBLIC '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )


def downgrade() -> None:
    for table in _CHILD_PREDICATES:
        op.execute(f'DROP POLICY IF EXISTS {_POLICY} ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
