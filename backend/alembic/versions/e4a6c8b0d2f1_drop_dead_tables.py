"""drop_dead_tables

Thirteen tables from the initial schema that nothing uses, all 0 rows on the
dev cluster on 28/09/2026 except the seed rows noted below:

  * release management — "Pipelines", "PipelineSteps", "ReleasePackages",
    "ReleaseApprovals", "EnvironmentConfigs", "ReleaseHistory",
    "RollbackOperations", "DriftEvents". Only release_worker read or wrote
    them, and it is removed in the same change.
  * "Webhooks" and "PIIAccessLogs" — an ORM class each, no reader or writer.
    (The Settings webhooks screen keeps its list in platform_settings.)
  * "Plans" — no API or screen. Its one row came from the dev seed, and
    "Tenants".plan_id (the only reference, NULL on every tenant) goes with it.
  * "KBPublishAPI" — ORM class plus Insert/Delete models, never called.
  * "KBNeo4jConnection" — ORM class plus a kb-seed row. "KBNeo4jNode".
    connection_id pointed only at it and is NULL on every node (the graph
    mirror writes tenant_id/knowledge_base instead, a1d0bc953734).

"AuditLogs".id also gets DEFAULT gen_random_uuid(): the initial schema gave
it none (the ORM's uuid4 default is Python-side), so the audit worker's raw
INSERT failed on every event with a NOT NULL violation.

Downgrade recreates the tables as ff9ae8dcd7f7 did (connection_id as
a1d0bc953734 left it, nullable), including the tenant_isolation policy
a1b2c3d4e5f6 applied to the tenant_id tables. Seed rows are not restored.

Revision ID: e4a6c8b0d2f1
Revises: b3d9ac7ba0a4
Create Date: 2026-09-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e4a6c8b0d2f1"
down_revision: str | None = "b3d9ac7ba0a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Children before parents.
_DROPPED = (
    "ReleaseApprovals",
    "ReleaseHistory",
    "PipelineSteps",
    "RollbackOperations",
    "ReleasePackages",
    "Pipelines",
    "EnvironmentConfigs",
    "DriftEvents",
    "Webhooks",
    "PIIAccessLogs",
    "KBPublishAPI",
    "KBNeo4jConnection",
    "Plans",
)


def upgrade() -> None:
    op.execute('ALTER TABLE "AuditLogs" ALTER COLUMN id SET DEFAULT gen_random_uuid()')
    # Dropping the column drops its FK constraint and index with it.
    op.execute('ALTER TABLE "KBNeo4jNode" DROP COLUMN IF EXISTS connection_id')
    op.execute('ALTER TABLE "Tenants" DROP COLUMN IF EXISTS plan_id')
    for table in _DROPPED:
        op.execute(f'DROP TABLE IF EXISTS "{table}"')


# One statement per op.execute: asyncpg rejects multi-statement strings.
_TENANT_RLS = (
    'ALTER TABLE "{t}" ENABLE ROW LEVEL SECURITY',
    'ALTER TABLE "{t}" FORCE ROW LEVEL SECURITY',
    'GRANT SELECT, INSERT, UPDATE, DELETE ON "{t}" TO app_user',
    """CREATE POLICY tenant_isolation ON "{t}" AS PERMISSIVE FOR ALL TO PUBLIC
  USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)""",
)


def _now() -> sa.TextClause:
    return sa.text("now()")


def _inserted_at() -> sa.Column:
    return sa.Column(
        "inserted_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False
    )


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def _downgrade_plans() -> None:
    op.create_table(
        "Plans",
        sa.Column("id", sa.SmallInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("max_users", sa.Integer(), nullable=True),
        sa.Column("max_envs", sa.SmallInteger(), nullable=True),
        sa.Column("max_secrets", sa.Integer(), nullable=True),
        sa.Column("max_deploy_daily", sa.Integer(), nullable=True),
        sa.Column("max_api_keys", sa.Integer(), nullable=True),
        sa.Column("features", _jsonb(), nullable=True),
        _inserted_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.add_column("Tenants", sa.Column("plan_id", sa.SmallInteger(), nullable=True))
    op.create_foreign_key("Tenants_plan_id_fkey", "Tenants", "Plans", ["plan_id"], ["id"])
    op.create_index(op.f("ix_Tenants_plan_id"), "Tenants", ["plan_id"], unique=False)


def _downgrade_release() -> None:
    op.create_table(
        "DriftEvents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("env_pair", sa.Text(), nullable=False),
        sa.Column("drift_keys", _jsonb(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("resolved_by", sa.String(length=255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        _inserted_at(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_drift_events_inserted_at_desc",
        "DriftEvents",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_drift_events_resolved", "DriftEvents", ["resolved", "detected_at"], unique=False
    )

    op.create_table(
        "EnvironmentConfigs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("is_secret", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("updated_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        _inserted_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("environment", "key", "version", name="uq_env_configs"),
    )
    op.create_index(
        "idx_env_configs_env", "EnvironmentConfigs", ["environment", "is_active"], unique=False
    )
    op.create_index(
        "idx_env_configs_inserted_at_desc",
        "EnvironmentConfigs",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )

    op.create_table(
        "Pipelines",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("pipeline_name", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.String(length=255), nullable=False),
        sa.Column("trigger_type", sa.Text(), nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=True),
        sa.Column("branch", sa.Text(), nullable=True),
        sa.Column("package_version", sa.Text(), nullable=True),
        sa.Column("target_env", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("risk_score", sa.SmallInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        _inserted_at(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_pipelines_inserted_at_desc",
        "Pipelines",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index("idx_pipelines_status", "Pipelines", ["status", "created_at"], unique=False)

    op.create_table(
        "PipelineSteps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pipeline_id", sa.Text(), nullable=False),
        sa.Column("step_name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("output", _jsonb(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _inserted_at(),
        sa.ForeignKeyConstraint(["pipeline_id"], ["Pipelines.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_pipeline_steps_inserted_at_desc",
        "PipelineSteps",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index("idx_pipeline_steps_pipeline", "PipelineSteps", ["pipeline_id"], unique=False)
    op.create_index(
        op.f("ix_PipelineSteps_pipeline_id"), "PipelineSteps", ["pipeline_id"], unique=False
    )

    op.create_table(
        "ReleasePackages",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("pipeline_id", sa.Text(), nullable=True),
        sa.Column("artifact_paths", _jsonb(), nullable=False),
        sa.Column("validation_score", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("environment_targets", _jsonb(), nullable=False),
        sa.Column("scan_result", _jsonb(), nullable=False),
        sa.Column("promoted_to_s3_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        _inserted_at(),
        sa.ForeignKeyConstraint(["pipeline_id"], ["Pipelines.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_release_packages_inserted_at_desc",
        "ReleasePackages",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_release_packages_status", "ReleasePackages", ["status", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_ReleasePackages_pipeline_id"), "ReleasePackages", ["pipeline_id"], unique=False
    )

    op.create_table(
        "RollbackOperations",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("pipeline_id", sa.Text(), nullable=True),
        sa.Column("from_version", sa.Text(), nullable=False),
        sa.Column("to_version", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("triggered_by", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_step", sa.SmallInteger(), nullable=False),
        sa.Column("steps_result", _jsonb(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alert_sent", sa.Boolean(), server_default="false", nullable=False),
        _inserted_at(),
        sa.ForeignKeyConstraint(["pipeline_id"], ["Pipelines.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_rollback_ops_env", "RollbackOperations", ["environment", "started_at"], unique=False
    )
    op.create_index(
        "idx_rollback_ops_inserted_at_desc",
        "RollbackOperations",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index(
        op.f("ix_RollbackOperations_pipeline_id"),
        "RollbackOperations",
        ["pipeline_id"],
        unique=False,
    )

    op.create_table(
        "ReleaseApprovals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("package_id", sa.Text(), nullable=False),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(length=255), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        _inserted_at(),
        sa.ForeignKeyConstraint(["package_id"], ["ReleasePackages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "package_id", "environment", "approved_by", name="uq_release_approvals"
        ),
    )
    op.create_index(
        "idx_release_approvals_inserted_at_desc",
        "ReleaseApprovals",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_release_approvals_package", "ReleaseApprovals", ["package_id"], unique=False
    )
    op.create_index(
        op.f("ix_ReleaseApprovals_package_id"), "ReleaseApprovals", ["package_id"], unique=False
    )

    op.create_table(
        "ReleaseHistory",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pipeline_id", sa.Text(), nullable=False),
        sa.Column("package_id", sa.Text(), nullable=True),
        sa.Column("environment", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("triggered_by", sa.String(length=255), nullable=False),
        sa.Column("deployed_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("metadata", _jsonb(), nullable=False),
        _inserted_at(),
        sa.ForeignKeyConstraint(["package_id"], ["ReleasePackages.id"]),
        sa.ForeignKeyConstraint(["pipeline_id"], ["Pipelines.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_release_history_env", "ReleaseHistory", ["environment", "deployed_at"], unique=False
    )
    op.create_index(
        "idx_release_history_inserted_at_desc",
        "ReleaseHistory",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index("idx_release_history_pipeline", "ReleaseHistory", ["pipeline_id"], unique=False)
    op.create_index(
        op.f("ix_ReleaseHistory_deployed_at"), "ReleaseHistory", ["deployed_at"], unique=False
    )
    op.create_index(
        op.f("ix_ReleaseHistory_pipeline_id"), "ReleaseHistory", ["pipeline_id"], unique=False
    )


def _downgrade_tenant_tables() -> None:
    op.create_table(
        "Webhooks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("secret_ref", sa.String(length=255), nullable=True),
        sa.Column("events", _jsonb(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("failure_count", sa.SmallInteger(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        _inserted_at(),
        sa.ForeignKeyConstraint(["tenant_id"], ["Tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_webhooks_inserted_at_desc",
        "Webhooks",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index("idx_webhooks_tenant", "Webhooks", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_Webhooks_tenant_id"), "Webhooks", ["tenant_id"], unique=False)

    op.create_table(
        "PIIAccessLogs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("accessor_id", sa.String(length=255), nullable=True),
        sa.Column("accessor_type", sa.String(length=20), nullable=False),
        sa.Column("data_subject", sa.String(length=255), nullable=True),
        sa.Column("field_accessed", sa.String(length=100), nullable=True),
        sa.Column("access_reason", sa.Text(), nullable=True),
        sa.Column("scrubbed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        _inserted_at(),
        sa.ForeignKeyConstraint(["tenant_id"], ["Tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_pii_access_logs_inserted_at_desc",
        "PIIAccessLogs",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_pii_access_logs_tenant", "PIIAccessLogs", ["tenant_id", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_PIIAccessLogs_created_at"), "PIIAccessLogs", ["created_at"], unique=False
    )

    op.create_table(
        "KBPublishAPI",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("endpoint_url", sa.String(length=1024), nullable=False),
        sa.Column("http_method", sa.String(length=8), nullable=False),
        sa.Column("is_published", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        _inserted_at(),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["Tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_kbpublishapi_inserted_at_desc",
        "KBPublishAPI",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_kbpublishapi_tenant_published",
        "KBPublishAPI",
        ["tenant_id"],
        unique=False,
        postgresql_where=sa.text("is_published = true"),
    )
    op.create_index(
        op.f("ix_KBPublishAPI_created_at"), "KBPublishAPI", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_KBPublishAPI_tenant_id"), "KBPublishAPI", ["tenant_id"], unique=False)

    op.create_table(
        "KBNeo4jConnection",
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("is_connected", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("total_node", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_edge", sa.Integer(), server_default="0", nullable=False),
        sa.Column("embedding_model_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        _inserted_at(),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(["embedding_model_id"], ["KBModel.model_id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["Tenants.id"]),
        sa.PrimaryKeyConstraint("connection_id"),
    )
    op.create_index(
        "idx_kbneo4jconn_tenant_connected",
        "KBNeo4jConnection",
        ["tenant_id"],
        unique=False,
        postgresql_where=sa.text("is_connected = true"),
    )
    op.create_index(
        "idx_kbneo4jconnection_inserted_at_desc",
        "KBNeo4jConnection",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index(
        op.f("ix_KBNeo4jConnection_created_at"), "KBNeo4jConnection", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_KBNeo4jConnection_tenant_id"), "KBNeo4jConnection", ["tenant_id"], unique=False
    )

    op.add_column("KBNeo4jNode", sa.Column("connection_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "KBNeo4jNode_connection_id_fkey",
        "KBNeo4jNode",
        "KBNeo4jConnection",
        ["connection_id"],
        ["connection_id"],
    )
    op.create_index(
        op.f("ix_KBNeo4jNode_connection_id"), "KBNeo4jNode", ["connection_id"], unique=False
    )

    for table in ("Webhooks", "PIIAccessLogs", "KBPublishAPI", "KBNeo4jConnection"):
        for statement in _TENANT_RLS:
            op.execute(statement.format(t=table))


def downgrade() -> None:
    _downgrade_plans()
    _downgrade_release()
    _downgrade_tenant_tables()
    op.execute('ALTER TABLE "AuditLogs" ALTER COLUMN id DROP DEFAULT')
