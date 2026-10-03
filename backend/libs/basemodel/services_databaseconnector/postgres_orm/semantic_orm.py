"""Semantic Layer storage — versioned metric contracts.

Distinct from the schema system already here, and the distinction is the
point:

  * SchemaCatalogORM is crawled *physical* structure — which tables and columns
    exist — enriched with a description.
  * These tables hold *business* meaning: what "doanh thu thuần" means, which
    aggregation computes it, which filters are always part of it, and which
    date column counts as when it happened. Physical structure cannot answer
    any of that, which is why a crawler could never remove the ambiguity.

Versioned rather than mutable: a metric's formula changing is a new row, so an
answer given last quarter stays reproducible and "why did this number move?"
has an auditable answer.
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.basemodel.services_databaseconnector.postgres_model import register
from libs.basemodel.services_databaseconnector.postgres_orm.base import Base


@register("orm")
class SemanticMetricORM(Base):
    """Stable identity of a metric. Never carries a formula — that is the
    version's job, so the id a question resolves to survives every redefinition."""

    __tablename__ = "SemanticMetric"
    __table_args__ = (
        UniqueConstraint("tenant_id", "metric_key", name="uq_semantic_metric_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)
    # Slug the agent and the API refer to, e.g. "revenue_net".
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # A metric with no accountable owner is not an operational metric — the
    # column is NOT NULL so the registry cannot fill up with orphans.
    owner: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


@register("orm")
class SemanticMetricVersionORM(Base):
    """One immutable formula. Exactly one version per metric may be `approved`;
    that is the one the agent will use."""

    __tablename__ = "SemanticMetricVersion"
    __table_args__ = (
        Index(
            "uq_semantic_one_approved_version", "metric_id", unique=True,
            postgresql_where=text("status = 'approved'"),
        ),
        UniqueConstraint("metric_id", "version", name="uq_semantic_metric_version"),
        Index("idx_semantic_version_lookup", "tenant_id", "metric_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)
    metric_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("SemanticMetric.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # draft | approved | deprecated — see libs.semantic.models.MetricStatus.
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="draft")

    # ── the formula ────────────────────────────────────────────────────────
    source_table: Mapped[str] = mapped_column(String(256), nullable=False)
    aggregation: Mapped[str] = mapped_column(String(32), nullable=False)
    expression: Mapped[str] = mapped_column(String(256), nullable=False)
    # The BUSINESS event date. Nullable because not every metric is time-bound
    # (a stock level is not), but when a metric IS time-bound this must name the
    # transaction date rather than defaulting to a row's created_at — booking
    # date and event date drifting apart is a silently wrong total, not an error.
    time_column: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    default_grain: Mapped[str] = mapped_column(String(16), nullable=False, server_default="none")
    unit: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    # [{key, column, label}] — versioned with the formula, since a renamed
    # column is a new contract, not an edit to the old one.
    dimensions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")
    # [{column, op, value}] — always applied. This is where net-vs-gross lives.
    default_filters: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")

    # ── governance ─────────────────────────────────────────────────────────
    approved_by: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    approved_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    change_note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


@register("orm")
class SemanticSynonymORM(Base):
    """A phrasing that reaches a metric.

    Unique per (tenant, term): one phrase resolving to two metrics would pick a
    winner by row order, i.e. differently on different replicas. The constraint
    turns that into an insert failure a human sees.
    """

    __tablename__ = "SemanticSynonym"
    __table_args__ = (
        UniqueConstraint("tenant_id", "term_normalized", name="uq_semantic_synonym_term"),
        Index("idx_semantic_synonym_metric", "tenant_id", "metric_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)
    metric_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("SemanticMetric.id", ondelete="CASCADE"), nullable=False)
    # As typed, for display.
    term: Mapped[str] = mapped_column(String(256), nullable=False)
    # libs.semantic.resolver.normalize(term) — matching is done on this.
    term_normalized: Mapped[str] = mapped_column(String(256), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False, server_default="vi")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
