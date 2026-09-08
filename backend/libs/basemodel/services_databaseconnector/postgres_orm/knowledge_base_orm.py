import datetime
import uuid
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from libs.basemodel.services_databaseconnector.postgres_model import (
    register, ConflictStatus, SimilarityMetric, Tier,
)
from libs.basemodel.services_databaseconnector.postgres_orm.base import Base


@register("orm")
class KBModelORM(Base):
    __tablename__ = "KBModel"

    __table_args__ = (
        Index("idx_kbmodel_inserted_at_desc", text("inserted_at DESC")),
    )

    model_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    versions: Mapped[list["KBModelVersionORM"]] = relationship(back_populates="model", cascade="all, delete-orphan")
    text_block_versions: Mapped[list["KBTextBlockVersionORM"]] = relationship(back_populates="embedding_model")
    qdrant_collections: Mapped[list["KBQdrantCollectionORM"]] = relationship(back_populates="embedding_model")
    neo4j_connections: Mapped[list["KBNeo4jConnectionORM"]] = relationship(back_populates="embedding_model")


@register("orm")
class KBModelVersionORM(Base):
    __tablename__ = "KBModelVersion"
    __table_args__ = (
        Index("idx_kbmodelversion_model_version", "model_id", "version_id", postgresql_where=text("is_active = true")),
        Index("idx_kbmodelversion_inserted_at_desc", text("inserted_at DESC")),
    )

    version_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBModel.model_id"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    added_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    added_on: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    model: Mapped["KBModelORM"] = relationship(back_populates="versions")


@register("orm")
class KBKnowledgeBaseORM(Base):
    """A knowledge base = the *kind* of knowledge a document belongs to (e.g. "finance",
    "legal", "engineering"). A tenant can define many knowledge bases; each KBData
    document optionally belongs to one. The knowledge_base_id is stamped onto every
    Qdrant point and Neo4j entity node so retrieval can be scoped to one knowledge base.
    """
    __tablename__ = "KBKnowledgeBase"
    __table_args__ = (
        # A tenant cannot have two knowledge bases with the same name.
        Index("idx_kbknowledgebase_tenant_name", "tenant_id", "name", unique=True),
        Index("idx_kbknowledgebase_inserted_at_desc", text("inserted_at DESC")),
    )

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="kb_knowledge_bases")
    documents: Mapped[list["KBDataORM"]] = relationship(back_populates="knowledge_base")


@register("orm")
class KBDataORM(Base):
    __tablename__ = "KBData"
    __table_args__ = (
        Index("idx_kbdata_tenant_source", "tenant_id", "source_type"),
        Index("idx_kbdata_tenant_role", "tenant_id", "role_id"),
        Index("idx_kbdata_tenant_tier", "tenant_id", "current_tier"),
        Index("idx_kbdata_inserted_at_desc", text("inserted_at DESC")),
        # Keyset pagination support: the docs list orders by (inserted_at, data_id)
        # filtered by tenant (+ current_tier for the layer tabs). These composite
        # indexes keep each page an O(log n + limit) range scan even when many rows
        # share an inserted_at (bulk same-timestamp inserts).
        Index("idx_kbdata_keyset", "tenant_id", "current_tier", "inserted_at", "data_id"),
        Index("idx_kbdata_keyset_all", "tenant_id", "inserted_at", "data_id"),
        # Document name is unique PER TENANT, not globally — two tenants may each
        # have a document with the same name.
        Index("idx_kbdata_tenant_name", "tenant_id", "name", unique=True),
    )

    data_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("RolePermissions.id"), nullable=False)
    # The knowledge base (kind of knowledge) this document belongs to. Nullable so
    # documents ingested before knowledge bases existed remain valid.
    knowledge_base_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("KBKnowledgeBase.knowledge_base_id"), nullable=True, index=True,
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    extension: Mapped[str] = mapped_column(String(32), nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    added_by: Mapped[str] = mapped_column(String(255), nullable=False)
    abstract: Mapped[str] = mapped_column(Text, nullable=False)
    doc_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False)
    current_tier: Mapped[str] = mapped_column(String(16), default=Tier.BRONZE.value, nullable=False)
    # 'document' (default, today's RAG/Qdrant path, unchanged) or
    # 'structured_table' (opt-in: LLM proposes a schema from the file, a
    # human reviews it, and only on approval does a real Postgres table get
    # created — see KBStructuredImportJob). A real column rather than a key
    # inside `doc_metadata`: that column is a strictly-validated discriminated
    # union per source_type (Document/Image/Video/Web/Warehouse in
    # postgres_model.py) and silently drops any key its Pydantic model
    # doesn't declare.
    ingestion_mode: Mapped[str] = mapped_column(String(32), default="document", server_default="document", nullable=False)
    path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    added_on: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    row_version: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), server_default=text("gen_random_uuid()"), default=uuid.uuid4, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="kb_data")
    knowledge_base: Mapped[Optional["KBKnowledgeBaseORM"]] = relationship(back_populates="documents")
    lifecycle_histories: Mapped[list["KBLifecycleHistoryORM"]] = relationship(back_populates="data", cascade="all, delete-orphan")
    text_blocks: Mapped[list["KBTextBlockORM"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    tables: Mapped[list["KBTableORM"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


@register("orm")
class KBLifecycleHistoryORM(Base):
    __tablename__ = "KBLifecycleHistory"

    __table_args__ = (
        Index("idx_kblifecyclehistory_inserted_at_desc", text("inserted_at DESC")),
    )

    history_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    data_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBData.data_id"), nullable=False, index=True)
    to_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    from_tier: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    approved_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    pipeline_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    transitioned_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    data: Mapped["KBDataORM"] = relationship(back_populates="lifecycle_histories")


@register("orm")
class KBFilterPolicyORM(Base):
    __tablename__ = "KBFilterPolicy"
    __table_args__ = (
        Index("idx_kbfilterpolicy_tenant_lang", "tenant_id", "language"),
        Index("idx_kbfilterpolicy_inserted_at_desc", text("inserted_at DESC")),
    )

    policy_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    policy_name: Mapped[str] = mapped_column(String(255), nullable=False)
    configformat: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    row_version: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), server_default=text("gen_random_uuid()"), default=uuid.uuid4, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="kb_filter_policies")


@register("orm")
class KBExtractionPolicyORM(Base):
    __tablename__ = "KBExtractionPolicy"
    __table_args__ = (
        Index("idx_kbextractionpolicy_tenant_lang", "tenant_id", "language"),
        Index("idx_kbextractionpolicy_inserted_at_desc", text("inserted_at DESC")),
    )

    policy_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    policy_name: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_type: Mapped[str] = mapped_column(String(64), nullable=False)
    custom_override: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    row_version: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), server_default=text("gen_random_uuid()"), default=uuid.uuid4, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="kb_extraction_policies")


@register("orm")
class KBConflictBatchORM(Base):
    __tablename__ = "KBConflictBatch"

    __table_args__ = (
        Index("idx_kbconflictbatch_inserted_at_desc", text("inserted_at DESC")),
        # One open batch per block: two chunks conflicting with the same block
        # concurrently must join the same batch, not race into two.
        Index(
            "idx_kbconflictbatch_open_block", "tenant_id", "existing_block_id",
            unique=True,
            postgresql_where=text("status <> 'resolved' AND is_deleted = false"),
        ),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=True, index=True)
    # The one text block this batch is anchored on; every conflict in it targets this block.
    existing_block_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True, index=True)
    batch_title: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=ConflictStatus.PENDING.value, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    tenant: Mapped[Optional["TenantsORM"]] = relationship(back_populates="kb_conflict_batches")
    conflicts: Mapped[list["KBConflictORM"]] = relationship(back_populates="batch", cascade="all, delete-orphan")


@register("orm")
class KBConflictORM(Base):
    __tablename__ = "KBConflict"
    __table_args__ = (
        Index("idx_kbconflict_tenant_status_severity", "tenant_id", "status", "severity"),
        Index("idx_kbconflict_inserted_at_desc", text("inserted_at DESC")),
    )

    conflict_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=True, index=True)
    # The document whose chunk raised this conflict; scopes re-promotion cleanup.
    data_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBData.data_id"), nullable=True, index=True)
    conflict_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBConflictBatch.batch_id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default=ConflictStatus.PENDING.value, nullable=False)
    detailed_explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Set when a merge re-check reopened this conflict, so a re-approved merge forces through.
    from_recheck: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    resolution_instruction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolution_method: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    resolved_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    detected_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    tenant: Mapped[Optional["TenantsORM"]] = relationship(back_populates="kb_conflicts")
    batch: Mapped[Optional["KBConflictBatchORM"]] = relationship(back_populates="conflicts")


@register("orm")
class KBWarehouseORM(Base):
    __tablename__ = "KBWarehouse"

    __table_args__ = (
        Index("idx_kbwarehouse_tenant", "tenant_id"),
        Index("idx_kbwarehouse_inserted_at_desc", text("inserted_at DESC")),
    )

    warehouse_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Warehouses are tenant-scoped like every other asset (a tenant only sees and
    # manages its own warehouse connections).
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    service: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    configs: Mapped[list["KBWarehouseConfigORM"]] = relationship(back_populates="warehouse", cascade="all, delete-orphan")


@register("orm")
class KBWarehouseConfigORM(Base):
    __tablename__ = "KBWarehouseConfig"
    __table_args__ = (
        Index("idx_kbwarehouseconfig_wh_version", "warehouse_id", "version_number"),
        Index("idx_kbwarehouseconfig_wh_active", "warehouse_id", postgresql_where=text("is_active = true")),
        Index("idx_kbwarehouseconfig_inserted_at_desc", text("inserted_at DESC")),
    )

    config_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBWarehouse.warehouse_id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    row_version: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), server_default=text("gen_random_uuid()"), default=uuid.uuid4, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    warehouse: Mapped["KBWarehouseORM"] = relationship(back_populates="configs")


@register("orm")
class KBTableORM(Base):
    __tablename__ = "KBTable"

    __table_args__ = (
        Index("idx_kbtable_inserted_at_desc", text("inserted_at DESC")),
    )

    table_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBData.data_id"), nullable=False, index=True)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    table_schema: Mapped[Optional[dict]] = mapped_column("schema", JSONB, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_on: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    owner: Mapped["KBDataORM"] = relationship(back_populates="tables")


@register("orm")
class KBStructuredImportJobORM(Base):
    """Review lifecycle for the opt-in 'structured table' ingestion mode
    (KBDataORM.ingestion_mode == 'structured_table'): an LLM proposes a
    schema from the uploaded spreadsheet, a human reviews/edits it here, and
    only on approval does a real table get created in the `kb_structured`
    Postgres schema (recorded, at that point, as a KBTableORM row — this
    table is the review/audit trail, KBTableORM is the pointer to the
    finished table)."""
    __tablename__ = "KBStructuredImportJob"
    __table_args__ = (
        Index("idx_kbstructuredimportjob_owner_created", "owner_id", text("created_at DESC")),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBData.data_id", ondelete="CASCADE"), nullable=False, index=True)
    # proposing -> proposed -> approved | rejected, or failed at any point
    # before 'proposed' (worker-side error, e.g. Docling/LLM failure).
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="proposing", server_default="proposing")
    llm_proposed_schema: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    human_edited_schema: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # First ~20 parsed rows only, for the review screen — the full parsed
    # table lives in Mongo (kb_structured_staging), not duplicated here.
    sample_rows: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_table_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)


@register("orm")
class KBTextBlockORM(Base):
    __tablename__ = "KBTextBlock"
    __table_args__ = (
        Index("idx_kbtextblock_owner_block", "owner_id", "block_id"),
        Index("idx_kbtextblock_inserted_at_desc", text("inserted_at DESC")),
    )

    block_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBData.data_id"), nullable=False, index=True)
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    owner: Mapped["KBDataORM"] = relationship(back_populates="text_blocks")
    versions: Mapped[list["KBTextBlockVersionORM"]] = relationship(back_populates="block", cascade="all, delete-orphan")


@register("orm")
class KBTextBlockVersionORM(Base):
    __tablename__ = "KBTextBlockVersion"
    __table_args__ = (
        Index("idx_kbtextblockversion_block_version", "block_id", "version_number", postgresql_where=text("is_active = true")),
        Index("idx_kbtextblockversion_inserted_at_desc", text("inserted_at DESC")),
    )

    version_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    block_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBTextBlock.block_id"), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    table_involved: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    embedding_model_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBModel.model_id"), nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    block: Mapped["KBTextBlockORM"] = relationship(back_populates="versions")
    embedding_model: Mapped[Optional["KBModelORM"]] = relationship(back_populates="text_block_versions")
    text_table: Mapped[Optional["KBTextTableORM"]] = relationship(back_populates="version", uselist=False, cascade="all, delete-orphan")


@register("orm")
class KBTextTableORM(Base):
    __tablename__ = "KBTextTable"

    __table_args__ = (
        Index("idx_kbtexttable_inserted_at_desc", text("inserted_at DESC")),
    )

    version_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBTextBlockVersion.version_id"), primary_key=True)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    row_version: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), server_default=text("gen_random_uuid()"), default=uuid.uuid4, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    version: Mapped["KBTextBlockVersionORM"] = relationship(back_populates="text_table")


@register("orm")
class KBQdrantConnectionORM(Base):
    __tablename__ = "KBQdrantConnection"
    __table_args__ = (
        Index("idx_kbqdrantconn_tenant_active", "tenant_id", postgresql_where=text("is_active = true")),
        Index("idx_kbqdrantconnection_inserted_at_desc", text("inserted_at DESC")),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    total_collection: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="kb_qdrant_connections")
    collections: Mapped[list["KBQdrantCollectionORM"]] = relationship(back_populates="connection", cascade="all, delete-orphan")


@register("orm")
class KBQdrantCollectionORM(Base):
    __tablename__ = "KBQdrantCollection"

    __table_args__ = (
        Index("idx_kbqdrantcollection_inserted_at_desc", text("inserted_at DESC")),
    )

    collection_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBQdrantConnection.connection_id"), nullable=False, index=True)
    collection_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    similarity_metric: Mapped[str] = mapped_column(String(16), default=SimilarityMetric.COSINE.value, nullable=False)
    points_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    vector_dimension: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    embedding_model_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBModel.model_id"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    connection: Mapped["KBQdrantConnectionORM"] = relationship(back_populates="collections")
    embedding_model: Mapped[Optional["KBModelORM"]] = relationship(back_populates="qdrant_collections")


@register("orm")
class KBNeo4jConnectionORM(Base):
    __tablename__ = "KBNeo4jConnection"
    __table_args__ = (
        Index("idx_kbneo4jconn_tenant_connected", "tenant_id", postgresql_where=text("is_connected = true")),
        Index("idx_kbneo4jconnection_inserted_at_desc", text("inserted_at DESC")),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    is_connected: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    total_node: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    total_edge: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    embedding_model_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBModel.model_id"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="kb_neo4j_connections")
    embedding_model: Mapped[Optional["KBModelORM"]] = relationship(back_populates="neo4j_connections")
    nodes: Mapped[list["KBNeo4jNodeORM"]] = relationship(back_populates="connection", cascade="all, delete-orphan")


@register("orm")
class KBNeo4jNodeORM(Base):
    __tablename__ = "KBNeo4jNode"

    __table_args__ = (
        Index("idx_kbneo4jnode_inserted_at_desc", text("inserted_at DESC")),
    )

    node_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBNeo4jConnection.connection_id"), nullable=True, index=True)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True, index=True)
    knowledge_base: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    node_name: Mapped[str] = mapped_column(String(512), nullable=False)
    node_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    connection: Mapped["KBNeo4jConnectionORM"] = relationship(back_populates="nodes")
    outgoing_relationships: Mapped[list["KBNeo4jRelationshipORM"]] = relationship(
        back_populates="from_node_obj",
        foreign_keys="KBNeo4jRelationshipORM.from_node",
        cascade="all, delete-orphan",
    )
    incoming_relationships: Mapped[list["KBNeo4jRelationshipORM"]] = relationship(
        back_populates="to_node_obj",
        foreign_keys="KBNeo4jRelationshipORM.to_node",
        cascade="all, delete-orphan",
    )


@register("orm")
class KBNeo4jRelationshipORM(Base):
    __tablename__ = "KBNeo4jRelationship"

    __table_args__ = (
        Index("idx_kbneo4jrelationship_inserted_at_desc", text("inserted_at DESC")),
        Index("idx_kbneo4jrelationship_pair", "from_node", "to_node"),
        Index("idx_kbneo4jrelationship_block", "block_id"),
        Index("idx_kbneo4jrelationship_data", "data_id"),
    )

    from_node: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBNeo4jNode.node_id"), primary_key=True)
    to_node: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("KBNeo4jNode.node_id"), primary_key=True)
    block_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    data_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    knowledge_base: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    from_node_obj: Mapped["KBNeo4jNodeORM"] = relationship(back_populates="outgoing_relationships", foreign_keys=[from_node])
    to_node_obj: Mapped["KBNeo4jNodeORM"] = relationship(back_populates="incoming_relationships", foreign_keys=[to_node])


@register("orm")
class KBEntityLookupORM(Base):
    """Exact alias → canonical entity resolution, learned during gold promotion.

    alias_name is stored in canonical form (to_canonical output — lowercase,
    underscores, Vietnamese diacritics PRESERVED) so lookup is a hash-index
    equality. tenant_id NULL = global seed alias shared by all tenants.
    """
    __tablename__ = "KBEntityLookup"
    __table_args__ = (
        Index("idx_kbentitylookup_alias", "alias_name", postgresql_using="hash"),
        Index("idx_kbentitylookup_tenant_alias", "tenant_id", "alias_name"),
        Index("idx_kbentitylookup_inserted_at_desc", text("inserted_at DESC")),
    )

    lookup_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[Optional[uuid.UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=True, index=True)
    alias_name: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)


@register("orm")
class KBPublishAPIORM(Base):
    __tablename__ = "KBPublishAPI"
    __table_args__ = (
        Index("idx_kbpublishapi_tenant_published", "tenant_id", postgresql_where=text("is_published = true")),
        Index("idx_kbpublishapi_inserted_at_desc", text("inserted_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    http_method: Mapped[str] = mapped_column(String(8), nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    tenant: Mapped["TenantsORM"] = relationship(back_populates="kb_publish_apis")
