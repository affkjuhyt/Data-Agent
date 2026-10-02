import datetime
import uuid
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.basemodel.services_databaseconnector.postgres_model import register
from libs.basemodel.services_databaseconnector.postgres_orm.base import Base

# ─── Agent Platform ───────────────────────────────────────────────────────────

@register("orm")
class KBConnectionsORM(Base):
    __tablename__ = "KBConnections"
    __table_args__ = (
        Index("idx_kbconnections_tenant", "tenant_id"),
        Index("idx_kbconnections_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    endpoint_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="disconnected", nullable=False)
    last_synced_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="kb_connections")
    agent_kb: Mapped[list["AgentKBORM"]] = relationship(back_populates="kb_connection")


@register("orm")
class SystemPromptsORM(Base):
    __tablename__ = "SystemPrompts"
    __table_args__ = (
        Index("idx_systemprompts_tenant", "tenant_id"),
        Index("idx_systemprompts_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="system_prompts")
    agent_versions: Mapped[list["AgentVersionsORM"]] = relationship(back_populates="system_prompt")


@register("orm")
class GuardrailsORM(Base):
    __tablename__ = "Guardrails"
    __table_args__ = (
        Index("idx_guardrails_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    conditions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    guardrail_model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="LiteLLM model alias the guardrail LLM check calls; NULL = guardrail.")
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)



@register("orm")
class MCPORM(Base):
    __tablename__ = "MCP"
    __table_args__ = (
        Index("idx_mcp_tenant", "tenant_id"),
        Index("idx_mcp_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    endpoint_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="secrets_vault.id of an MCP_TOKEN secret; config_loader reads the bearer token from OpenBao. Never the token itself.")
    status: Mapped[str] = mapped_column(String(20), default="disconnected", nullable=False)
    capabilities: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="mcp_servers")
    agent_mcp: Mapped[list["AgentMCPORM"]] = relationship(back_populates="mcp")


@register("orm")
class AgentsORM(Base):
    __tablename__ = "Agents"
    __table_args__ = (
        Index("idx_agents_tenant", "tenant_id"),
        Index("idx_agents_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    published_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("AgentVersions.id", use_alter=True, name="fk_agents_published_version"),
        nullable=True,
        comment="AgentVersions.id currently served. The ORM declares this FK but no migration created it.",
    )
    draft_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("AgentVersions.id", use_alter=True, name="fk_agents_draft_version"),
        nullable=True,
        comment="AgentVersions.id of the editable draft; draft updates find it by AgentVersions.status instead. The ORM declares this FK but no migration created it.",
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="agents")
    versions: Mapped[list["AgentVersionsORM"]] = relationship(
        back_populates="agent",
        foreign_keys="AgentVersionsORM.agent_id",
        cascade="all, delete-orphan",
    )
    published_version: Mapped[Optional["AgentVersionsORM"]] = relationship(
        foreign_keys=[published_version_id],
        post_update=True,
    )
    draft_version: Mapped[Optional["AgentVersionsORM"]] = relationship(
        foreign_keys=[draft_version_id],
        post_update=True,
    )
    workflows: Mapped[list["WorkflowsORM"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    memories: Mapped[list["AgentMemoriesORM"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    episodic_memories: Mapped[list["EpisodicMemoryORM"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    future_memories: Mapped[list["FutureMemoryORM"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    workflow_memories: Mapped[list["WorkflowMemoryORM"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


@register("orm")
class WorkflowsORM(Base):
    __tablename__ = "Workflows"
    __table_args__ = (
        Index("idx_workflows_agent", "agent_id"),
        Index("idx_workflows_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Agents.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    agent: Mapped["AgentsORM"] = relationship(back_populates="workflows")
    workflow_versions: Mapped[list["WorkflowVersionsORM"]] = relationship(back_populates="workflow", cascade="all, delete-orphan")


@register("orm")
class WorkflowVersionsORM(Base):
    __tablename__ = "WorkflowVersions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "version", name="uq_workflow_versions_workflow_version"),
        Index("idx_workflowversions_workflow_status", "workflow_id", "status"),
        Index("uq_workflowversions_one_published", "workflow_id", unique=True, postgresql_where=text("status = 'published'")),
        Index("uq_workflowversions_one_draft", "workflow_id", unique=True, postgresql_where=text("status = 'draft'")),
        Index("idx_workflowversions_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Workflows.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    changelog: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    published_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    workflow: Mapped["WorkflowsORM"] = relationship(back_populates="workflow_versions")
    agent_versions: Mapped[list["AgentVersionsORM"]] = relationship(back_populates="workflow_version")


@register("orm")
class AgentVersionsORM(Base):
    __tablename__ = "AgentVersions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
        Index("idx_agentversions_agent_status", "agent_id", "status"),
        Index("idx_agentversions_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Agents.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    workflow_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("WorkflowVersions.id"), nullable=True)
    responder_model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="LiteLLM model alias (as GET /api/llm-providers lists it, e.g. responder) the agent answers with; NULL = the deployment default.")
    llm_config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    system_prompt_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("SystemPrompts.id"), nullable=True)
    memory_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    changelog: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    published_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    agent: Mapped["AgentsORM"] = relationship(back_populates="versions", foreign_keys=[agent_id])
    workflow_version: Mapped[Optional["WorkflowVersionsORM"]] = relationship(back_populates="agent_versions")
    system_prompt: Mapped[Optional["SystemPromptsORM"]] = relationship(back_populates="agent_versions")
    agent_kb: Mapped[list["AgentKBORM"]] = relationship(back_populates="version", cascade="all, delete-orphan")
    agent_mcp: Mapped[list["AgentMCPORM"]] = relationship(back_populates="version", cascade="all, delete-orphan")


# ─── Junction Tables ──────────────────────────────────────────────────────────

@register("orm")
class AgentKBORM(Base):
    __tablename__ = "AgentKB"

    version_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("AgentVersions.id"), primary_key=True)
    kb_connection_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBConnections.id"), primary_key=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    version: Mapped["AgentVersionsORM"] = relationship(back_populates="agent_kb")
    kb_connection: Mapped["KBConnectionsORM"] = relationship(back_populates="agent_kb")


@register("orm")
class AgentMCPORM(Base):
    __tablename__ = "AgentMCP"

    version_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("AgentVersions.id"), primary_key=True)
    mcp_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("MCP.id"), primary_key=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    version: Mapped["AgentVersionsORM"] = relationship(back_populates="agent_mcp")
    mcp: Mapped["MCPORM"] = relationship(back_populates="agent_mcp")


# ─── Memory ───────────────────────────────────────────────────────────────────

@register("orm")
class AgentMemoriesORM(Base):
    __tablename__ = "AgentMemories"
    __table_args__ = (
        Index("idx_agentmemories_agent_scope", "agent_id", "scope"),
        Index("idx_agentmemories_agent_user_ref", "agent_id", "user_ref"),
        Index("idx_agentmemories_user_domain", "tenant_id", "agent_id", "user_ref", "domain"),
        Index("idx_agentmemories_deleted_at", "deleted_at",postgresql_where=text("deleted_at IS NULL")),
        Index("idx_agentmemories_inserted_at_desc", text("inserted_at DESC")),
        UniqueConstraint(
            "tenant_id", "agent_id", "scope", "user_ref", "domain", "fact_key",
            name="uq_agentmemories_fact", postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)
    agent_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Agents.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    user_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    memory_type: Mapped[str] = mapped_column(String(20), nullable=False)
    domain: Mapped[str] = mapped_column(String(64), default="general", server_default="general", nullable=False)
    fact_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    importance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="agent_memories")
    agent: Mapped["AgentsORM"] = relationship(back_populates="memories")


@register("orm")
class EpisodicMemoryORM(Base):
    """LTM Episodic Past — timestamped events (completed tasks, user corrections)."""
    __tablename__ = "EpisodicMemory"
    __table_args__ = (
        Index("idx_episodicmemory_tenant", "tenant_id"),
        Index("idx_episodicmemory_user_time", "user_ref", text("created_at DESC")),
        Index("idx_episodicmemory_expires", "expires_at", postgresql_where=text("expires_at IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Agents.id"), nullable=False, index=True)
    user_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    domain: Mapped[str] = mapped_column(String(64), default="general", server_default="general", nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance_score: Mapped[float] = mapped_column(Float, default=0.5, server_default="0.5", nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active", nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="episodic_memories")
    agent: Mapped["AgentsORM"] = relationship(back_populates="episodic_memories")


@register("orm")
class FutureMemoryORM(Base):
    """LTM Episodic Future — prospective memory (reminders, scheduled intents)."""
    __tablename__ = "FutureMemory"
    __table_args__ = (
        Index("idx_futurememory_tenant", "tenant_id"),
        Index("idx_futurememory_user_status_time", "user_ref", "status", "execution_time"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Agents.id"), nullable=False, index=True)
    user_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    execution_time: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    recurrence: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending", nullable=False)
    executed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="future_memories")
    agent: Mapped["AgentsORM"] = relationship(back_populates="future_memories")


@register("orm")
class MemoryRetentionConfigORM(Base):
    """Per tenant+domain retention/cap config — doc §3.3, replaces the global MEMORY_RETENTION_DAYS env var."""
    __tablename__ = "MemoryRetentionConfig"

    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), primary_key=True)
    domain: Mapped[str] = mapped_column(String(64), primary_key=True)
    retention_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # NULL = keep forever
    max_records_per_user: Mapped[int] = mapped_column(Integer, default=500, server_default="500", nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="memory_retention_configs")


@register("orm")
class WorkflowMemoryORM(Base):
    """LTM Workflow Memory (doc §3.7) — Episodic Memory specific to data
    execution context: which task_type/pipeline ran, with what params, and
    whether it succeeded. Lets the agent retrieve a similar past run instead
    of re-planning from scratch."""
    __tablename__ = "WorkflowMemory"
    __table_args__ = (
        Index("idx_workflowmemory_task_type", "tenant_id", "agent_id", "task_type"),
        Index("idx_workflowmemory_created_at_desc", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Agents.id"), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    task_description: Mapped[str] = mapped_column(Text, nullable=False)
    param_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    qdrant_point_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="workflow_memories")
    agent: Mapped["AgentsORM"] = relationship(back_populates="workflow_memories")


@register("orm")
class SchemaCatalogORM(Base):
    """Schema Catalog (sandbox_schema_docs.docx §II) — auto-crawled, LLM-
    enriched table catalog, written by the schema-crawler-worker background
    job from Snowflake INFORMATION_SCHEMA + an LLM-generated business
    description, embedded into the Qdrant "schema_catalog" collection for
    hybrid (vector + metadata-filter) retrieval before SQL generation.

    warehouse_id points at KBWarehouse (basemodel...knowledge_base_orm) —
    the existing per-tenant warehouse-connection record — but is stored as a
    plain FK column (no ORM relationship) to avoid a cross-module
    dependency; the crawler worker queries KBWarehouse/KBWarehouseConfig
    directly instead of through a SQLAlchemy relationship here.
    """
    __tablename__ = "SchemaCatalog"
    __table_args__ = (
        Index("idx_schemacatalog_lookup", "tenant_id", "warehouse_id"),
        UniqueConstraint("tenant_id", "warehouse_id", "schema_name", "table_name", name="uq_schemacatalog_table"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBWarehouse.warehouse_id"), nullable=False, index=True)
    schema_name: Mapped[str] = mapped_column(String(128), nullable=False)
    table_name: Mapped[str] = mapped_column(String(128), nullable=False)
    columns: Mapped[list] = mapped_column(JSONB, nullable=False)
    # BigInteger — Snowflake tables routinely exceed Integer's ~2.1B row cap.
    row_count: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_altered: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Governance: lineage / owner / freshness / classification ──────────
    # Three timestamps live on this row and they are NOT interchangeable:
    #   last_altered        — when the table's DDL last changed
    #   last_crawled_at     — when this metadata was last read
    #   data_as_of          — how recent the DATA is (MAX of freshness_column)
    # Only the third can support an "as of" in an answer. The first two were
    # all this table had, which is why an answer built off a pipeline that
    # broke days ago read exactly like one built off this morning's load.
    #
    # `owner`, `classification` and `upstream` are declared by an operator in
    # the warehouse config and copied here by the crawler; they are never
    # inferred from the data. `data_as_of` is the only one measured.
    # Semantics and the FRESH/STALE/UNKNOWN rule live in backend/libs/shared/freshness.py.
    owner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    classification: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    upstream: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True)
    freshness_column: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    data_as_of: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    freshness_checked_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Nullable on purpose: NULL means "nobody has said how old is too old", and
    # freshness.status() reports UNKNOWN for it rather than defaulting to fresh.
    freshness_sla_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    text_description: Mapped[str] = mapped_column(Text, nullable=False)
    join_hints: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    schema_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    qdrant_point_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    last_crawled_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="schema_catalog_entries")


# ─── Conversation ─────────────────────────────────────────────────────────────

@register("orm")
class ConversationsORM(Base):
    __tablename__ = "Conversations"
    __table_args__ = (
        Index("idx_conversations_tenant_user_ref", "tenant_id", "user_ref"),
        Index("idx_conversations_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)
    user_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    channel: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, comment="Set to active on creation and never updated.")
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="conversations")
    messages: Mapped[list["MessagesORM"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


@register("orm")
class MessagesORM(Base):
    __tablename__ = "Messages"
    __table_args__ = (
        Index("idx_messages_conversation_created", "conversation_id", "created_at"),
        Index("idx_messages_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Conversations.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, comment="{\"text\": <message text>}.")
    msg_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True, comment="Assistant turn context: agent_version_id, language, intent, analysis_type, slots, clarifications, responder_model, pending_options, pending_approval, citations.")
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="Always 0 so far: no caller passes a token count.")
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    conversation: Mapped["ConversationsORM"] = relationship(back_populates="messages")
    traces: Mapped[list["AgentTracesORM"]] = relationship(back_populates="message", cascade="all, delete-orphan")

# MessagesORM.msg_metadata -> column "metadata"

@register("orm")
class AgentTracesORM(Base):
    __tablename__ = "AgentTraces"
    __table_args__ = (
        UniqueConstraint("message_id", "trace_index", name="uq_agent_traces_message_index"),
        Index("idx_agenttraces_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Messages.id"), nullable=False)
    trace_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="LangGraph node name, tool/<name> for a tool call, or responder.")
    input: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    output: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    message: Mapped["MessagesORM"] = relationship(back_populates="traces")


# ─── Platform settings ────────────────────────────────────────────────────────

@register("orm")
class PlatformSettingsORM(Base):
    """Tenant-scoped JSON key/value store for admin-UI settings.

    One row per (tenant, key); the value is opaque JSON so a new settings
    screen never needs a schema migration. Written/read only through
    workflow_runtime GET/PUT /api/platform/settings/{key}.
    """
    __tablename__ = "PlatformSettings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key", name="uq_platform_settings_tenant_key"),
        {"comment": "Per-tenant settings, key -> jsonb (org_profile, schedules, delivery_channels, delivery_outbox, agent_templates). The platform-wide flags are in platform_settings."},
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
