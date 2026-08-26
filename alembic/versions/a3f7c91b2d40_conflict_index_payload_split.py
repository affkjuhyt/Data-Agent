"""Split the conflict record from its chunk payload

Postgres keeps the queryable index; the incoming chunk's content, embedding, entities
and relationships move to Mongo (`kb_conflict_payload`, keyed by tenant_id +
conflict_id). `incoming_snapshot` alone carried a 1024-float embedding per conflict
that only the resolver read — and it read it from Mongo.

  KBConflictBatch  + existing_block_id  — the block a batch is anchored on, plus a
                                          unique partial index so two chunks racing
                                          on one block join one batch instead of two.
  KBConflict       + data_id            — which document raised it (re-promotion cleanup)
                   + from_recheck       — was a merge re-check; previously Mongo-only
                   - existing_snapshot  — {block_id, summary}: block_id moves to the
                                          batch, summary already exists in Mongo
                   - incoming_snapshot  — the payload, now Mongo-only

Backfill: existing_block_id / data_id are recovered from the dropped JSONB before the
columns are removed, so no Mongo read is needed to upgrade.

Revision ID: a3f7c91b2d40
Revises: f2b3c4d5e6f7
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3f7c91b2d40"
down_revision: Union[str, None] = "f2b3c4d5e6f7"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column(
        "KBConflictBatch",
        sa.Column("existing_block_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "KBConflict",
        sa.Column("data_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "KBConflict",
        sa.Column("from_recheck", sa.Boolean(), server_default="false", nullable=False),
    )

    # Recover both ids from the snapshots before they are dropped. Every conflict in a
    # batch carries the same existing block, so any one row settles the batch's anchor.
    op.execute("""
        UPDATE "KBConflictBatch" AS b
           SET existing_block_id = (
            SELECT (c.existing_snapshot ->> 'block_id')::uuid
            FROM "KBConflict" AS c
            WHERE c.batch_id = b.batch_id
              AND c.existing_snapshot ->> 'block_id' IS NOT NULL
            LIMIT 1
        )
    """)
    op.execute("""
        UPDATE "KBConflict"
           SET data_id = (incoming_snapshot ->> 'data_id')::uuid
         WHERE incoming_snapshot ->> 'data_id' IS NOT NULL
    """)

    op.create_index("idx_kbconflict_data_id", "KBConflict", ["data_id"])
    op.create_index(
        "idx_kbconflictbatch_open_block",
        "KBConflictBatch",
        ["tenant_id", "existing_block_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'resolved' AND is_deleted = false"),
    )
    op.create_foreign_key(
        "fk_kbconflict_data_id", "KBConflict", "KBData", ["data_id"], ["data_id"],
    )

    op.drop_column("KBConflict", "incoming_snapshot")
    op.drop_column("KBConflict", "existing_snapshot")


def downgrade() -> None:
    # The dropped payload is not recoverable from Postgres — the columns come back
    # empty and the resolver reads the chunk from Mongo either way.
    op.add_column(
        "KBConflict",
        sa.Column("existing_snapshot", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "KBConflict",
        sa.Column("incoming_snapshot", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    op.execute("""
        UPDATE "KBConflict" AS c
           SET existing_snapshot = jsonb_build_object('block_id', b.existing_block_id::text)
          FROM "KBConflictBatch" AS b
         WHERE c.batch_id = b.batch_id
           AND b.existing_block_id IS NOT NULL
    """)

    op.drop_constraint("fk_kbconflict_data_id", "KBConflict", type_="foreignkey")
    op.drop_index("idx_kbconflictbatch_open_block", table_name="KBConflictBatch")
    op.drop_index("idx_kbconflict_data_id", table_name="KBConflict")
    op.drop_column("KBConflict", "from_recheck")
    op.drop_column("KBConflict", "data_id")
    op.drop_column("KBConflictBatch", "existing_block_id")
