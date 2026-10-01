"""comment_unclear_columns

The database had no COMMENT at all, and the meaning of several columns cannot
be read from their name: KBData.abstract holds the document name, KBData.role_id
is a visibility floor, owner_id is a KBData.data_id, MCP.api_key_ref holds the
token itself, key_rotations.secret_id means different rows per trigger. Each
comment states what code actually writes, found by reading the writers on
01/10/2026; the same text is the ORM's comment= so autogenerate stays quiet.

Revision ID: 4e8a1c6d2b9f
Revises: 9d4f2b7c1e3a
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "4e8a1c6d2b9f"
down_revision: str | None = "9d4f2b7c1e3a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_COMMENTS = (
    (
        "KBNeo4jNode",
        "Postgres mirror of the Neo4j entity nodes, written by GraphStore alongside Neo4j; used for provenance and as the UI fallback when Neo4j is down.",
    ),
    (
        "KBNeo4jRelationship",
        "Postgres mirror of the Neo4j RELATES edges, one row per (from_node, to_node, block_id) so each edge keeps the chunk it came from.",
    ),
    (
        "KBQdrantCollection",
        "Seeded metadata for Qdrant collections. points_count, vector_dimension and similarity_metric are not kept in sync; the UI reads them live from Qdrant.",
    ),
    (
        "KBQdrantConnection",
        "Ties Qdrant collections to a tenant. Holds no URL: the Qdrant endpoint comes from QDRANT_URL.",
    ),
    (
        "PlatformSettings",
        "Per-tenant settings, key -> jsonb (org_profile, schedules, delivery_channels, delivery_outbox, agent_templates). The platform-wide flags are in platform_settings.",
    ),
    (
        "RolePermissions",
        "Permission grants per role (action is a name from kb_backend's permission CATALOG), plus one anchor row per role with resource '_role_anchor': KBData.role_id and KBSource.role_id point at those anchors.",
    ),
    (
        "platform_settings",
        "Platform-wide flags, key -> text (ip_allowlist_mode, vault_panic_mode, vault_auto_rotation, vault_pii_access_log). Per-tenant settings are in PlatformSettings.",
    ),
)

_COLUMN_COMMENTS = (
    ("AgentTraces", "tool_name", "LangGraph node name, tool/<name> for a tool call, or responder."),
    (
        "Agents",
        "published_version_id",
        "AgentVersions.id currently served. The ORM declares this FK but no migration created it.",
    ),
    (
        "Agents",
        "draft_version_id",
        "AgentVersions.id of the editable draft; draft updates find it by AgentVersions.status instead. The ORM declares this FK but no migration created it.",
    ),
    ("Conversations", "status", "Set to active on creation and never updated."),
    (
        "DataAccessLog",
        "metric_ref",
        "Semantic metric the query used, as metric_key@version (e.g. revenue_net@3); empty when none.",
    ),
    (
        "KBData",
        "role_id",
        "Lowest role that may see the document: one of the RolePermissions anchor rows. A viewer sees documents of their own role and every lower one (PLATFORM_ADMIN > AI_ENGINEER > EXECUTIVE > BUSINESS_OPERATOR).",
    ),
    ("KBData", "added_by", "Display name of the uploader, not a user id."),
    (
        "KBData",
        "abstract",
        "Short description. The upload UI sends the document name here; web crawls write an empty string.",
    ),
    (
        "KBData",
        "current_tier",
        "Furthest medallion tier reached: bronze, silver or gold. Same as the latest KBLifecycleHistory.to_tier.",
    ),
    (
        "KBData",
        "ingestion_mode",
        "document (chunked and embedded) or structured_table (rows imported into kb_structured).",
    ),
    ("KBData", "path", "MinIO object key of the uploaded file. NULL for web-crawled documents."),
    (
        "KBData",
        "row_version",
        "Optimistic-lock token, regenerated on every write; no writer compares it yet.",
    ),
    (
        "KBNeo4jNode",
        "knowledge_base",
        "str(KBData.knowledge_base_id), or the literal 'General Knowledge Base' when the document has none. Part of the node id hash; no FK.",
    ),
    (
        "KBNeo4jRelationship",
        "knowledge_base",
        "str(KBData.knowledge_base_id), or the literal 'General Knowledge Base' when the document has none; no FK.",
    ),
    (
        "KBSourceRun",
        "data_ids",
        "JSON array of the KBData.data_id values this run produced; no FK.",
    ),
    ("KBStructuredImportJob", "owner_id", "KBData.data_id of the document being imported."),
    ("KBTable", "owner_id", "KBData.data_id of the structured-table document."),
    ("KBTextBlock", "owner_id", "KBData.data_id of the document this chunk belongs to."),
    (
        "KBTextBlockVersion",
        "payload",
        "Chunk metadata also held in the Qdrant payload (summary, entities, embedding_model_id, knowledge_base, region); only entities is read back from here.",
    ),
    (
        "KBWarehouse",
        "service",
        "Warehouse engine as entered (Snowflake, Databricks, Postgres; case varies). The schema crawler normalises it.",
    ),
    (
        "MCP",
        "api_key_ref",
        "Despite the name, the bearer token itself: config_loader sends it as auth_token.",
    ),
    ("Members", "role_id", "Never written or read; the role is tenant_role."),
    (
        "Members",
        "tenant_role",
        "admin, editor or viewer. workflow_runtime checks it before creating a conversation.",
    ),
    ("Messages", "content", '{"text": <message text>}.'),
    (
        "Messages",
        "metadata",
        "Assistant turn context: agent_version_id, language, intent, analysis_type, slots, clarifications, responder_model, pending_options, pending_approval, citations.",
    ),
    ("Messages", "tokens_used", "Always 0 so far: no caller passes a token count."),
    (
        "OutboxEvents",
        "producer",
        "Service that wrote the event (workflow-runtime, kb-backend, kb-promote-silver, kb-promote-gold, web-crawler); that service's relay publishes it.",
    ),
    (
        "RolePermissions",
        "role_id",
        "Role name: PLATFORM_ADMIN, AI_ENGINEER, EXECUTIVE or BUSINESS_OPERATOR.",
    ),
    (
        "api_keys",
        "key_hash",
        "bcrypt hash of the issued key. auth_api /internal/api-keys/verify checks presented keys against it, found by key_prefix.",
    ),
    ("compliance_settings", "tenant_id", "Tenants.id as text; no FK."),
    (
        "compliance_settings",
        "pii_types",
        "Comma-separated PII types masked when pii_masking is on, e.g. EMAIL,PHONE,CCCD,CMND,CREDIT_CARD,IP.",
    ),
    (
        "key_rotations",
        "secret_id",
        "secrets_vault row affected: the new row for MANUAL, the same row for SCHEDULED, PANIC and REVEAL.",
    ),
    (
        "key_rotations",
        "triggered_by",
        "SCHEDULED, MANUAL, PANIC or REVEAL. A REVEAL row records a secret being shown, not a rotation.",
    ),
    (
        "secrets_vault",
        "realm",
        "Free-text grouping label shown in the UI (e.g. FINANCE-INFRA); unrelated to Keycloak realms.",
    ),
    (
        "secrets_vault",
        "openbao_path",
        "OpenBao path at write time. Readers recompute the path instead of reading this column.",
    ),
)


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def upgrade() -> None:
    for table, comment in _TABLE_COMMENTS:
        op.execute(f'COMMENT ON TABLE "{table}" IS {_quote(comment)}')
    for table, column, comment in _COLUMN_COMMENTS:
        op.execute(f'COMMENT ON COLUMN "{table}"."{column}" IS {_quote(comment)}')


def downgrade() -> None:
    for table, _ in _TABLE_COMMENTS:
        op.execute(f'COMMENT ON TABLE "{table}" IS NULL')
    for table, column, _ in _COLUMN_COMMENTS:
        op.execute(f'COMMENT ON COLUMN "{table}"."{column}" IS NULL')
