"""Data-access audit log — who read what.

Separate from AuditLogsORM on purpose. That table records authentication events
(a login, a token refresh): low volume, one row per session, written by
audit-consumer from Keycloak. This one records every query an agent runs:
orders of magnitude more rows, a completely different shape (statement, row
count, latency, target objects), and a different retention answer. Folding them
together would make both harder to query and force the shorter retention on
both.

Append-only by intent — nothing in the platform updates or deletes a row here.
That is not yet enforced by a grant; see the migration's note.
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from libs.basemodel.services_databaseconnector.postgres_model import register
from libs.basemodel.services_databaseconnector.postgres_orm.base import Base


@register("orm")
class DataAccessLogORM(Base):
    __tablename__ = "DataAccessLog"
    __table_args__ = (
        # The three questions an investigation actually asks, in order:
        # "everything this tenant did", "everything this user did",
        # "everything that touched this table".
        Index("idx_data_access_tenant_time", "tenant_id", text("occurred_at DESC")),
        Index("idx_data_access_user_time", "tenant_id", "user_id", text("occurred_at DESC")),
        Index("idx_data_access_objects", "target_objects", postgresql_using="gin"),
        Index("idx_data_access_statement", "statement_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Producer-generated, carried through Kafka so an at-least-once redelivery
    # is deduplicated rather than double-counted in a report.
    event_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("Tenants.id"), nullable=False)

    # ── who ────────────────────────────────────────────────────────────────
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_roles: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    # "gateway" | "service:<name>" — distinguishes a request the user made from
    # one a service made on their behalf.
    auth_source: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")

    # ── what ───────────────────────────────────────────────────────────────
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    target: Mapped[str] = mapped_column(String(256), nullable=False, server_default="")
    target_objects: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    # Literals replaced by `?`. The raw statement is deliberately NOT stored —
    # a WHERE clause is often the personal data this log exists to protect.
    statement_redacted: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # SHA-256 of the original, so identical runs correlate and a specific
    # statement can be proven to have run without keeping its arguments.
    statement_sha256: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    metric_ref: Mapped[str] = mapped_column(String(160), nullable=False, server_default="", comment="Semantic metric the query used, as metric_key@version (e.g. revenue_net@3); empty when none.")

    # ── outcome ────────────────────────────────────────────────────────────
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    # ── correlation ────────────────────────────────────────────────────────
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)

    # When the access happened (producer clock) vs when the row landed
    # (consumer clock). Kept separate so a broker backlog is visible rather
    # than silently rewriting history.
    occurred_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    inserted_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
