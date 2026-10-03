"""drop_redundant_indexes

Twenty-two single-column btree indexes whose column already leads another
(non-partial) btree index on the same table, so every lookup they serve the
wider index serves too, while each write pays for both. Found from pg_index
at 2e9a4c6b8d0f; most are the ix_* twins the ORM created with index=True next
to a composite it also declared (ix_KBData_tenant_id sat beside eight
indexes that start with tenant_id). Unique and partial indexes, and indexes
whose cover is partial (KBTextBlockVersion.block_id), are kept.

Revision ID: 3f1b5d7a9c2e
Revises: 2e9a4c6b8d0f
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3f1b5d7a9c2e"
down_revision: str | None = "2e9a4c6b8d0f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (index, table, column)
_REDUNDANT = (
    ("idx_agentmemories_tenant", "AgentMemories", "tenant_id"),
    ("ix_AgentMemories_agent_id", "AgentMemories", "agent_id"),
    ("ix_AgentTraces_message_id", "AgentTraces", "message_id"),
    ("ix_AgentVersions_agent_id", "AgentVersions", "agent_id"),
    ("idx_conversations_tenant", "Conversations", "tenant_id"),
    ("ix_ip_allowlists_tenant", "ip_allowlists", "tenant_id"),
    ("ix_KBConflict_tenant_id", "KBConflict", "tenant_id"),
    ("ix_KBData_tenant_id", "KBData", "tenant_id"),
    ("ix_KBEntityLookup_tenant_id", "KBEntityLookup", "tenant_id"),
    ("ix_KBExtractionPolicy_tenant_id", "KBExtractionPolicy", "tenant_id"),
    ("ix_KBFilterPolicy_tenant_id", "KBFilterPolicy", "tenant_id"),
    ("ix_KBKnowledgeBase_tenant_id", "KBKnowledgeBase", "tenant_id"),
    ("idx_kbsourcecrawlqueue_source_id", "KBSourceCrawlQueue", "source_id"),
    ("ix_KBStructuredImportJob_owner_id", "KBStructuredImportJob", "owner_id"),
    ("ix_KBTextBlock_owner_id", "KBTextBlock", "owner_id"),
    ("ix_KBWarehouseConfig_warehouse_id", "KBWarehouseConfig", "warehouse_id"),
    ("ix_Messages_conversation_id", "Messages", "conversation_id"),
    ("idx_platformsettings_tenant", "PlatformSettings", "tenant_id"),
    ("idx_role_permissions_role", "RolePermissions", "role_id"),
    ("idx_semantic_metric_tenant", "SemanticMetric", "tenant_id"),
    ("idx_workflowmemory_tenant", "WorkflowMemory", "tenant_id"),
    ("ix_WorkflowVersions_workflow_id", "WorkflowVersions", "workflow_id"),
)


def upgrade() -> None:
    for index, _, _ in _REDUNDANT:
        op.drop_index(index)


def downgrade() -> None:
    for index, table, column in _REDUNDANT:
        op.create_index(index, table, [column], unique=False)
