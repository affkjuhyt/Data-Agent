"""Full-text index on category labels for semantic value retrieval.

sem_value_retrieval matches the normalised label. This index stores those same
tokens (Postgres 'simple', no stemming) so a later lookup can use the table
instead of loading every value of a large Data Space. HNSW stays out of
Postgres: vectors live in Qdrant, and that pass is still the model path.
"""

from alembic import op

revision = "v3_0015"
down_revision = "v3_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS value_index_label_norm_fts_idx
            ON context.value_index USING gin (to_tsvector('simple', label_norm))
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS context.value_index_label_norm_fts_idx")
