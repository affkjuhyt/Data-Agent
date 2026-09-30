"""outbox_events

Transactional outbox for Kafka events: producers write the event row (in their own
transaction where they have one) and a per-service relay publishes it, so a broker
outage or a crash between commit and publish delays an event instead of losing it.
See libs/shared/outbox.py.

Revision ID: a9c2e4f6b8d1
Revises: f7a1c3e5b9d2
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a9c2e4f6b8d1"
down_revision: str | None = "f7a1c3e5b9d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS "OutboxEvents" (
            id           BIGSERIAL PRIMARY KEY,
            producer     VARCHAR(50)  NOT NULL,
            topic        VARCHAR(200) NOT NULL,
            event_key    VARCHAR(200),
            payload      JSONB        NOT NULL,
            created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
            published_at TIMESTAMPTZ,
            attempts     INTEGER      NOT NULL DEFAULT 0,
            last_error   TEXT,
            dead_at      TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_outboxevents_pending ON \"OutboxEvents\" (producer, id) "
        "WHERE published_at IS NULL AND dead_at IS NULL"
    )


def downgrade() -> None:
    op.execute('DROP TABLE IF EXISTS "OutboxEvents"')
