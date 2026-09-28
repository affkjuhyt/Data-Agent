import datetime
import uuid
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, SmallInteger,
    String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.basemodel.services_databaseconnector.postgres_model import register
from libs.basemodel.services_databaseconnector.postgres_orm.base import Base


# ─── Platform Core ────────────────────────────────────────────────────────────

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
    data_residency: Mapped[str] = mapped_column(String(20), default="Asia-SE1", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    keycloak_config: Mapped[Optional["KeycloakRealmConfigsORM"]] = relationship(back_populates="tenant", uselist=False)
    audit_logs: Mapped[list["AuditLogsORM"]] = relationship(back_populates="tenant")
    # knowledge_base_orm
    kb_knowledge_bases: Mapped[list["KBKnowledgeBaseORM"]] = relationship(back_populates="tenant")
    kb_data: Mapped[list["KBDataORM"]] = relationship(back_populates="tenant")
    kb_filter_policies: Mapped[list["KBFilterPolicyORM"]] = relationship(back_populates="tenant")
    kb_extraction_policies: Mapped[list["KBExtractionPolicyORM"]] = relationship(back_populates="tenant")
    kb_conflict_batches: Mapped[list["KBConflictBatchORM"]] = relationship(back_populates="tenant")
    kb_conflicts: Mapped[list["KBConflictORM"]] = relationship(back_populates="tenant")
    kb_qdrant_connections: Mapped[list["KBQdrantConnectionORM"]] = relationship(back_populates="tenant")
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
class AuditLogsORM(Base):
    __tablename__ = "AuditLogs"
    __table_args__ = (
        Index("idx_audit_logs_tenant", "tenant_id", "created_at"),
        Index("idx_audit_logs_source_event", "source_event_id",postgresql_where=text("source_event_id IS NOT NULL")),
        Index("idx_audit_logs_inserted_at_desc", text("inserted_at DESC")),
    )

    # server_default too: the audit worker writes this table with raw SQL,
    # which never sees the Python-side uuid4 default.
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()"),
    )
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
