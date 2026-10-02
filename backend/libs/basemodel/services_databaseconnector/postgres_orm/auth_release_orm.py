import datetime
import uuid
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index,
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
        Index("idx_tenants_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    audit_logs: Mapped[list["AuditLogsORM"]] = relationship(back_populates="tenant")
    # knowledge_base_orm
    kb_knowledge_bases: Mapped[list["KBKnowledgeBaseORM"]] = relationship(back_populates="tenant")
    kb_data: Mapped[list["KBDataORM"]] = relationship(back_populates="tenant")
    kb_filter_policies: Mapped[list["KBFilterPolicyORM"]] = relationship(back_populates="tenant")
    kb_extraction_policies: Mapped[list["KBExtractionPolicyORM"]] = relationship(back_populates="tenant")
    kb_conflict_batches: Mapped[list["KBConflictBatchORM"]] = relationship(back_populates="tenant")
    kb_conflicts: Mapped[list["KBConflictORM"]] = relationship(back_populates="tenant")
    kb_qdrant_connections: Mapped[list["KBQdrantConnectionORM"]] = relationship(back_populates="tenant")
    # workflow_orm
    kb_connections: Mapped[list["KBConnectionsORM"]] = relationship(back_populates="tenant")
    system_prompts: Mapped[list["SystemPromptsORM"]] = relationship(back_populates="tenant")
    mcp_servers: Mapped[list["MCPORM"]] = relationship(back_populates="tenant")
    agents: Mapped[list["AgentsORM"]] = relationship(back_populates="tenant")
    agent_memories: Mapped[list["AgentMemoriesORM"]] = relationship(back_populates="tenant")
    episodic_memories: Mapped[list["EpisodicMemoryORM"]] = relationship(back_populates="tenant")
    future_memories: Mapped[list["FutureMemoryORM"]] = relationship(back_populates="tenant")
    memory_retention_configs: Mapped[list["MemoryRetentionConfigORM"]] = relationship(back_populates="tenant")
    workflow_memories: Mapped[list["WorkflowMemoryORM"]] = relationship(back_populates="tenant")
    schema_catalog_entries: Mapped[list["SchemaCatalogORM"]] = relationship(back_populates="tenant")
    conversations: Mapped[list["ConversationsORM"]] = relationship(back_populates="tenant")


@register("orm")
class RolePermissionsORM(Base):
    __tablename__ = "RolePermissions"
    __table_args__ = (
        UniqueConstraint("role_id", "resource", "action", name="uq_role_permissions"),
        Index("idx_role_permissions_role", "role_id"),
        Index("idx_role_permissions_inserted_at_desc", text("inserted_at DESC")),
        {"comment": "Permission grants per role (action is a name from kb_backend's permission CATALOG), plus one anchor row per role with resource '_role_anchor': KBData.role_id and KBSource.role_id point at those anchors."},
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_id: Mapped[str] = mapped_column(String(50), nullable=False, comment="Role name: PLATFORM_ADMIN, AI_ENGINEER, EXECUTIVE or BUSINESS_OPERATOR.")
    resource: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


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
