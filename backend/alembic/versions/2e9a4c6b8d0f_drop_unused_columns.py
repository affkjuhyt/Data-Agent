"""drop_unused_columns

Columns no code writes (or writes and never reads), checked against every
reader and against the dev cluster's rows on 02/10/2026:

  * "KBNeo4jRelationship".score, .description — never written; the graph
    view's Postgres fallback returned them as null. Neo4j keeps its own.
  * "KBTextBlockVersion".embedding_model_id — never written (always NULL); the
    chunk view now reads the model id from payload, where Gold puts it.
  * "EpisodicMemory".embedding_id — never written or read.
  * "Conversations".started_at, .ended_at — never written; the API already fell
    back to inserted_at, and ended_at was always null.
  * "Agents".language — never written or read.
  * "AgentVersions".retrieval_config — copied on publish, selected, never used
    (retrieval settings live on the canvas nodes).
  * "AgentVersions".guardrail_id — settable, never read: the guardrail is the
    platform-wide top-priority row (agent_catalog.fetch_guardrail_config).
  * secrets_vault.openbao_path — written, never read; readers rebuild the path.
  * "KBQdrantCollection".points_count, .vector_dimension, .similarity_metric and
    "KBQdrantConnection".total_collection — seed values never updated; the UI
    reads them live from Qdrant.
  * "KBTable".created_on — never written or read.
  * "Tenants".data_residency — never read; compliance_settings.residency_region
    is the setting the Compliance screen edits.

And the "KBModelVersion" table: model_service wrote a v1 row per new model with
config {"active": true} and nothing ever read it.

Downgrade restores the columns and the table empty or at their defaults;
values are not restored.

Revision ID: 2e9a4c6b8d0f
Revises: 1d8f3b5a7c9e
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "2e9a4c6b8d0f"
down_revision: str | None = "1d8f3b5a7c9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DROPPED = (
    ("KBNeo4jRelationship", "score"),
    ("KBNeo4jRelationship", "description"),
    ("KBTextBlockVersion", "embedding_model_id"),
    ("EpisodicMemory", "embedding_id"),
    ("Conversations", "started_at"),
    ("Conversations", "ended_at"),
    ("Agents", "language"),
    ("AgentVersions", "retrieval_config"),
    ("AgentVersions", "guardrail_id"),
    ("secrets_vault", "openbao_path"),
    ("KBQdrantCollection", "points_count"),
    ("KBQdrantCollection", "vector_dimension"),
    ("KBQdrantCollection", "similarity_metric"),
    ("KBQdrantConnection", "total_collection"),
    ("KBTable", "created_on"),
    ("Tenants", "data_residency"),
)


_QDRANT_COLLECTION_COMMENT = (
    "Ties a tenant''s Qdrant collections to their embedding model. Dimension, distance "
    "and point count are read live from Qdrant."
)
# As 4e8a1c6d2b9f set it, for the downgrade.
_QDRANT_COLLECTION_COMMENT_BEFORE = (
    "Seeded metadata for Qdrant collections. points_count, vector_dimension and "
    "similarity_metric are not kept in sync; the UI reads them live from Qdrant."
)


def upgrade() -> None:
    # Dropping a column drops its FK constraint with it.
    for table, column in _DROPPED:
        op.drop_column(table, column)
    op.drop_table("KBModelVersion")
    op.execute(f"COMMENT ON TABLE \"KBQdrantCollection\" IS '{_QDRANT_COLLECTION_COMMENT}'")


def _now() -> sa.TextClause:
    return sa.text("now()")


def _not_null_with_fill(table: str, column: sa.Column, fill: str) -> None:
    """Re-add a NOT NULL column that had no default: fill existing rows, then
    drop the temporary default so the column matches what it was."""
    column.server_default = sa.DefaultClause(sa.text(fill))
    op.add_column(table, column)
    op.alter_column(table, column.name, server_default=None)


def downgrade() -> None:
    op.create_table(
        "KBModelVersion",
        sa.Column("version_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("model_id", sa.UUID(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("added_by", sa.String(length=255), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("added_on", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("inserted_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["KBModel.model_id"]),
        sa.PrimaryKeyConstraint("version_id"),
    )
    op.create_index(
        "idx_kbmodelversion_inserted_at_desc",
        "KBModelVersion",
        [sa.literal_column("inserted_at DESC")],
        unique=False,
    )
    op.create_index(
        "idx_kbmodelversion_model_version",
        "KBModelVersion",
        ["model_id", "version_id"],
        unique=False,
        postgresql_where=sa.text("is_active = true"),
    )
    op.create_index("ix_KBModelVersion_created_at", "KBModelVersion", ["created_at"], unique=False)
    op.execute('GRANT SELECT, USAGE ON SEQUENCE "KBModelVersion_version_id_seq" TO app_user')

    _not_null_with_fill(
        "Tenants", sa.Column("data_residency", sa.String(length=20), nullable=False), "'Asia-SE1'"
    )
    op.add_column(
        "KBTable",
        sa.Column("created_on", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
    )
    op.add_column(
        "KBQdrantConnection",
        sa.Column("total_collection", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    _not_null_with_fill(
        "KBQdrantCollection",
        sa.Column("similarity_metric", sa.String(length=16), nullable=False),
        "'cosine'",
    )
    op.add_column("KBQdrantCollection", sa.Column("vector_dimension", sa.Integer(), nullable=True))
    op.add_column(
        "KBQdrantCollection",
        sa.Column("points_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "secrets_vault",
        sa.Column(
            "openbao_path",
            sa.Text(),
            nullable=True,
            comment="OpenBao path at write time. Readers recompute the path instead of reading this column.",
        ),
    )
    op.add_column("AgentVersions", sa.Column("guardrail_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "AgentVersions_guardrail_id_fkey", "AgentVersions", "Guardrails", ["guardrail_id"], ["id"]
    )
    op.add_column(
        "AgentVersions",
        sa.Column("retrieval_config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("Agents", sa.Column("language", sa.String(length=10), nullable=True))
    op.add_column("Conversations", sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "Conversations", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("EpisodicMemory", sa.Column("embedding_id", sa.String(length=128), nullable=True))
    op.add_column("KBTextBlockVersion", sa.Column("embedding_model_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "KBTextBlockVersion_embedding_model_id_fkey",
        "KBTextBlockVersion",
        "KBModel",
        ["embedding_model_id"],
        ["model_id"],
    )
    op.add_column("KBNeo4jRelationship", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("KBNeo4jRelationship", sa.Column("score", sa.Float(), nullable=True))
    op.execute(f"COMMENT ON TABLE \"KBQdrantCollection\" IS '{_QDRANT_COLLECTION_COMMENT_BEFORE}'")
