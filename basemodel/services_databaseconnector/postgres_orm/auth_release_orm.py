import datetime
import uuid
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, SmallInteger,
    String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from basemodel.services_databaseconnector.postgres_model import register
from basemodel.services_databaseconnector.postgres_orm.base import Base


# ─── Platform Core ────────────────────────────────────────────────────────────

@register("orm")
class PlansORM(Base):
    __tablename__ = "Plans"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    max_users: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_envs: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    max_secrets: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_deploy_daily: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_api_keys: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    features: Mapped[Optional[dict]] = mapped_column(JSONB, default={}, nullable=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenants: Mapped[list["TenantsORM"]] = relationship(back_populates="plan")


@register("orm")
class TenantsORM(Base):
    __tablename__ = "Tenants"
    __table_args__ = (
        Index("idx_tenants_slug", "slug", unique=True),
        Index("idx_tenants_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    plan_id: Mapped[Optional[int]] = mapped_column(SmallInteger, ForeignKey("Plans.id"), nullable=True, index=True)
    data_residency: Mapped[str] = mapped_column(String(20), default="Asia-SE1", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    plan: Mapped[Optional["PlansORM"]] = relationship(back_populates="tenants")
    keycloak_config: Mapped[Optional["KeycloakRealmConfigsORM"]] = relationship(back_populates="tenant", uselist=False)
    ip_allowlists: Mapped[list["IPAllowlistsORM"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    api_keys: Mapped[list["APIKeysORM"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    secrets: Mapped[list["SecretsVaultORM"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    webhooks: Mapped[list["WebhooksORM"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    audit_logs: Mapped[list["AuditLogsORM"]] = relationship(back_populates="tenant")
    pii_access_logs: Mapped[list["PIIAccessLogsORM"]] = relationship(back_populates="tenant")
    # knowledge_base_orm
    kb_knowledge_bases: Mapped[list["KBKnowledgeBaseORM"]] = relationship(back_populates="tenant")
    kb_data: Mapped[list["KBDataORM"]] = relationship(back_populates="tenant")
    kb_filter_policies: Mapped[list["KBFilterPolicyORM"]] = relationship(back_populates="tenant")
    kb_extraction_policies: Mapped[list["KBExtractionPolicyORM"]] = relationship(back_populates="tenant")
    kb_conflict_batches: Mapped[list["KBConflictBatchORM"]] = relationship(back_populates="tenant")
    kb_conflicts: Mapped[list["KBConflictORM"]] = relationship(back_populates="tenant")
    kb_qdrant_connections: Mapped[list["KBQdrantConnectionORM"]] = relationship(back_populates="tenant")
    kb_neo4j_connections: Mapped[list["KBNeo4jConnectionORM"]] = relationship(back_populates="tenant")
    kb_publish_apis: Mapped[list["KBPublishAPIORM"]] = relationship(back_populates="tenant")
    members: Mapped[list["MembersORM"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    # workflow_orm
    kb_connections: Mapped[list["KBConnectionsORM"]] = relationship(back_populates="tenant")
    system_prompts: Mapped[list["SystemPromptsORM"]] = relationship(back_populates="tenant")
    tools: Mapped[list["ToolsORM"]] = relationship(back_populates="tenant")
    mcp_servers: Mapped[list["MCPORM"]] = relationship(back_populates="tenant")
    agents: Mapped[list["AgentsORM"]] = relationship(back_populates="tenant")
    agent_memories: Mapped[list["AgentMemoriesORM"]] = relationship(back_populates="tenant")
    episodic_memories: Mapped[list["EpisodicMemoryORM"]] = relationship(back_populates="tenant")
    future_memories: Mapped[list["FutureMemoryORM"]] = relationship(back_populates="tenant")
    memory_retention_configs: Mapped[list["MemoryRetentionConfigORM"]] = relationship(back_populates="tenant")
    workflow_memories: Mapped[list["WorkflowMemoryORM"]] = relationship(back_populates="tenant")
    schema_memories: Mapped[list["SchemaMemoryORM"]] = relationship(back_populates="tenant")
    schema_catalog_entries: Mapped[list["SchemaCatalogORM"]] = relationship(back_populates="tenant")
    conversations: Mapped[list["ConversationsORM"]] = relationship(back_populates="tenant")


@register("orm")
class RolePermissionsORM(Base):
    __tablename__ = "RolePermissions"
    __table_args__ = (
        UniqueConstraint("role_id", "resource", "action", name="uq_role_permissions"),
        Index("idx_role_permissions_role", "role_id"),
        Index("idx_role_permissions_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_id: Mapped[str] = mapped_column(String(50), nullable=False)
    resource: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


@register("orm")
class KeycloakRealmConfigsORM(Base):
    __tablename__ = "KeycloakRealmConfigs"
    __table_args__ = (
        Index("idx_keycloakrealmconfigs_inserted_at_desc", text("inserted_at DESC")),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), primary_key=True)
    realm_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    keycloak_base_url: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str] = mapped_column(String(100), nullable=False)
    client_secret_ref: Mapped[str] = mapped_column(Text, nullable=False)
    jwks_url: Mapped[str] = mapped_column(Text, nullable=False)
    token_endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    token_ttl_seconds: Mapped[int] = mapped_column(Integer, default=900, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="keycloak_config")


# ─── Auth & Security ──────────────────────────────────────────────────────────

@register("orm")
class IPAllowlistsORM(Base):
    __tablename__ = "IPAllowlists"
    __table_args__ = (
        Index("idx_ip_allowlists_tenant", "tenant_id"),
        Index("idx_ip_allowlists_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    cidr: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="ip_allowlists")


@register("orm")
class APIKeysORM(Base):
    __tablename__ = "APIKeys"
    __table_args__ = (
        Index("idx_api_keys_tenant", "tenant_id"),
        Index("idx_api_keys_key_hash", "key_hash"),
        UniqueConstraint("tenant_id", "key_prefix", name="uq_api_keys_tenant_prefix"),
        Index("idx_api_keys_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[str] = mapped_column(String(30), default="read_only", nullable=False)
    last_used_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    rotated_from: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("APIKeys.id"), nullable=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="api_keys")


@register("orm")
class SecretsVaultORM(Base):
    __tablename__ = "SecretsVault"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key_name", "version", name="uq_secrets_vault_tenant_name"),
        Index("idx_secrets_vault_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    key_name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_type: Mapped[str] = mapped_column(String(50), nullable=False)
    algorithm: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    realm: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    openbao_path: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    rotation_due_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_rotated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="secrets")
    rotations: Mapped[list["KeyRotationsORM"]] = relationship(back_populates="secret", cascade="all, delete-orphan")


@register("orm")
class KeyRotationsORM(Base):
    __tablename__ = "KeyRotations"
    __table_args__ = (
        Index("idx_key_rotations_secret", "secret_id"),
        Index("idx_key_rotations_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    secret_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("SecretsVault.id"), nullable=False, index=True)
    triggered_by: Mapped[str] = mapped_column(String(20), default="MANUAL", nullable=False)
    actor_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    old_version: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    new_version: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="SUCCESS", nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rotated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    secret: Mapped["SecretsVaultORM"] = relationship(back_populates="rotations")


@register("orm")
class WebhooksORM(Base):
    __tablename__ = "Webhooks"
    __table_args__ = (
        Index("idx_webhooks_tenant", "tenant_id"),
        Index("idx_webhooks_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    secret_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    events: Mapped[dict] = mapped_column(JSONB, default=[], nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    failure_count: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="webhooks")


@register("orm")
class AuditLogsORM(Base):
    __tablename__ = "AuditLogs"
    __table_args__ = (
        Index("idx_audit_logs_tenant", "tenant_id", "created_at"),
        Index("idx_audit_logs_source_event", "source_event_id",postgresql_where=text("source_event_id IS NOT NULL")),
        Index("idx_audit_logs_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(20), default="USER", nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="SUCCESS", nullable=False)
    event_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, default={}, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    source_event_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="audit_logs")

# AuditLogsORM.event_metadata -> column "metadata"

@register("orm")
class PIIAccessLogsORM(Base):
    __tablename__ = "PIIAccessLogs"
    __table_args__ = (
        Index("idx_pii_access_logs_tenant", "tenant_id", "created_at"),
        Index("idx_pii_access_logs_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)
    accessor_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    accessor_type: Mapped[str] = mapped_column(String(20), default="USER", nullable=False)
    data_subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    field_accessed: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    access_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scrubbed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    status: Mapped[str] = mapped_column(String(10), default="GRANTED", nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="pii_access_logs")


# ─── Release Management ───────────────────────────────────────────────────────

@register("orm")
class PipelinesORM(Base):
    __tablename__ = "Pipelines"
    __table_args__ = (
        Index("idx_pipelines_status", "status", "created_at"),
        Index("idx_pipelines_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    pipeline_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger_type: Mapped[str] = mapped_column(Text, default="MANUAL", nullable=False)
    commit_sha: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    branch: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    package_version: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_env: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="PENDING", nullable=False)
    risk_score: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    steps: Mapped[list["PipelineStepsORM"]] = relationship(back_populates="pipeline", cascade="all, delete-orphan")
    packages: Mapped[list["ReleasePackagesORM"]] = relationship(back_populates="pipeline")
    history: Mapped[list["ReleaseHistoryORM"]] = relationship(back_populates="pipeline")
    rollbacks: Mapped[list["RollbackOperationsORM"]] = relationship(back_populates="pipeline")


@register("orm")
class PipelineStepsORM(Base):
    __tablename__ = "PipelineSteps"
    __table_args__ = (
        Index("idx_pipeline_steps_pipeline", "pipeline_id"),
        Index("idx_pipeline_steps_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_id: Mapped[str] = mapped_column(Text, ForeignKey("Pipelines.id"), nullable=False, index=True)
    step_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="PENDING", nullable=False)
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output: Mapped[Optional[dict]] = mapped_column(JSONB, default={}, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    pipeline: Mapped["PipelinesORM"] = relationship(back_populates="steps")


@register("orm")
class ReleasePackagesORM(Base):
    __tablename__ = "ReleasePackages"
    __table_args__ = (
        Index("idx_release_packages_status", "status", "created_at"),
        Index("idx_release_packages_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    pipeline_id: Mapped[Optional[str]] = mapped_column(Text, ForeignKey("Pipelines.id"), nullable=True, index=True)
    artifact_paths: Mapped[dict] = mapped_column(JSONB, default=[], nullable=False)
    validation_score: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="BUILDING", nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    environment_targets: Mapped[dict] = mapped_column(JSONB, default=[], nullable=False)
    scan_result: Mapped[dict] = mapped_column(JSONB, default={}, nullable=False)
    promoted_to_s3_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    pipeline: Mapped[Optional["PipelinesORM"]] = relationship(back_populates="packages")
    approvals: Mapped[list["ReleaseApprovalsORM"]] = relationship(back_populates="package", cascade="all, delete-orphan")
    history: Mapped[list["ReleaseHistoryORM"]] = relationship(back_populates="package")


@register("orm")
class ReleaseApprovalsORM(Base):
    __tablename__ = "ReleaseApprovals"
    __table_args__ = (
        UniqueConstraint("package_id", "environment", "approved_by", name="uq_release_approvals"),
        Index("idx_release_approvals_package", "package_id"),
        Index("idx_release_approvals_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    package_id: Mapped[str] = mapped_column(Text, ForeignKey("ReleasePackages.id"), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    package: Mapped["ReleasePackagesORM"] = relationship(back_populates="approvals")


@register("orm")
class EnvironmentConfigsORM(Base):
    __tablename__ = "EnvironmentConfigs"
    __table_args__ = (
        UniqueConstraint("environment", "key", "version", name="uq_env_configs"),
        Index("idx_env_configs_env", "environment", "is_active"),
        Index("idx_env_configs_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    version: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    updated_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


@register("orm")
class ReleaseHistoryORM(Base):
    __tablename__ = "ReleaseHistory"
    __table_args__ = (
        Index("idx_release_history_env", "environment", "deployed_at"),
        Index("idx_release_history_pipeline", "pipeline_id"),
        Index("idx_release_history_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_id: Mapped[str] = mapped_column(Text, ForeignKey("Pipelines.id"), nullable=False, index=True)
    package_id: Mapped[Optional[str]] = mapped_column(Text, ForeignKey("ReleasePackages.id"), nullable=True)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(255), nullable=False)
    deployed_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deploy_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default={}, nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    pipeline: Mapped["PipelinesORM"] = relationship(back_populates="history")
    package: Mapped[Optional["ReleasePackagesORM"]] = relationship(back_populates="history")


@register("orm")
class RollbackOperationsORM(Base):
    __tablename__ = "RollbackOperations"
    __table_args__ = (
        Index("idx_rollback_ops_env", "environment", "started_at"),
        Index("idx_rollback_ops_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    pipeline_id: Mapped[Optional[str]] = mapped_column(Text, ForeignKey("Pipelines.id"), nullable=True, index=True)
    from_version: Mapped[str] = mapped_column(Text, nullable=False)
    to_version: Mapped[str] = mapped_column(Text, nullable=False)
    environment: Mapped[str] = mapped_column(Text, nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="INITIATED", nullable=False)
    current_step: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    steps_result: Mapped[dict] = mapped_column(JSONB, default=[], nullable=False)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    alert_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    pipeline: Mapped[Optional["PipelinesORM"]] = relationship(back_populates="rollbacks")


@register("orm")
class DriftEventsORM(Base):
    __tablename__ = "DriftEvents"
    __table_args__ = (
        Index("idx_drift_events_resolved", "resolved", "detected_at"),
        Index("idx_drift_events_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    detected_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    env_pair: Mapped[str] = mapped_column(Text, nullable=False)
    drift_keys: Mapped[dict] = mapped_column(JSONB, default=[], nullable=False)
    severity: Mapped[str] = mapped_column(Text, default="CRITICAL_DRIFT", nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    resolved_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)



@register("orm")
class MembersORM(Base):
    __tablename__ = "Members"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_members_tenant_user"),
        Index("idx_members_tenant", "tenant_id"),
        Index("idx_members_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    role_id: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True)
    tenant_role: Mapped[str] = mapped_column(String(50), default="viewer", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    joined_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="members")
