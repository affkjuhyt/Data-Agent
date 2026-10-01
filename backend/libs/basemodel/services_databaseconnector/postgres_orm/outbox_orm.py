"""Transactional outbox for Kafka events (ADR-V2-34 direction: no publish-then-hope).

A producer writes the event into this table (in the same Postgres transaction as its
own change when it has one) and a relay publishes it afterwards. The broker being
down, or the process dying between "commit" and "publish", then delays an event
instead of losing it. Delivery is at-least-once: consumers must be idempotent, as
the silver/gold/memory/audit consumers already are.
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from libs.basemodel.services_databaseconnector.postgres_orm.base import Base


class OutboxEventORM(Base):
    __tablename__ = "OutboxEvents"
    __table_args__ = (
        # What the relay scans: one producer's unpublished, not-dead rows in order.
        Index(
            "idx_outboxevents_pending",
            "producer",
            "id",
            postgresql_where=text("published_at IS NULL AND dead_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Which service's relay owns the row: each relay publishes with its own Kafka
    # principal, so it may only drain the topics that principal is allowed to write.
    producer: Mapped[str] = mapped_column(String(50), nullable=False, comment="Service that wrote the event (workflow-runtime, kb-backend, kb-promote-silver, kb-promote-gold, web-crawler); that service's relay publishes it.")
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    event_key: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    published_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Set after too many failed attempts so one poison event cannot block the queue
    # behind it; an operator decides what to do with dead rows.
    dead_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
