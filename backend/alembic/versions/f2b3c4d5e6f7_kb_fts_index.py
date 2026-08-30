"""GIN full-text index backing the hybrid lexical (BM25) retrieval branch

Revision ID: f2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-07-11

The LightRAG retrieval pipeline gained a sparse lexical branch (config flag
HYBRID_LEXICAL) that recovers exact keyword / proper-noun matches dense vectors
under-rank — the strongest single 2025 recommendation for keyword queries. It
runs a Postgres full-text search over the active chunk text:

    to_tsvector('simple', "KBTextBlockVersion".content) @@ websearch_to_tsquery(...)
    ORDER BY ts_rank_cd(...)

Without a matching expression index that predicate degrades to a sequential scan
as the corpus grows. This migration adds the GIN index over the SAME expression
(text-search config 'simple' — no stemming, keeps Vietnamese diacritics) so the
lexical pass stays fast.

The default config is 'simple' to match cfg.LEXICAL_TS_CONFIG; if you change that
env var to a stemming config, add a matching index for it too.
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = "f2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


_INDEX = "idx_kbtextblockversion_content_fts"
# Must match lexical_search()'s to_tsvector(config, content) expression exactly for
# the planner to use it.
_TS_CONFIG = "simple"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {_INDEX} "
        f'ON "KBTextBlockVersion" '
        f"USING gin (to_tsvector('{_TS_CONFIG}', content))"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
