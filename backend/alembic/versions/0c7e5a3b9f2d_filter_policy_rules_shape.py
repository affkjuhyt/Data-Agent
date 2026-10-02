"""filter_policy_rules_shape

silver/filtering.py reads KBFilterPolicy.config["rules"], and the Policy screen
writes that key — one sentence for natural_language, the words for exact_word.
The kb-seed rows (and any row copied from them) used {"keywords": [...]}, which
nothing reads, so those policies filtered nothing; on the dev cluster "PII
Filter" was active and inert. This rewrites such rows into the rules shape:
the keyword list as-is for exact_word, one sentence naming them for
natural_language. Once active, a natural_language policy costs one LLM call per
chunk at Silver — that is what the seed intended.

Data only; downgrade leaves the rows in the shape the code reads.

Revision ID: 0c7e5a3b9f2d
Revises: 8e5b1d3f7a9c
Create Date: 2026-10-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0c7e5a3b9f2d"
down_revision: str | None = "8e5b1d3f7a9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE "KBFilterPolicy"
        SET config = jsonb_build_object('rules', config->'keywords')
        WHERE configformat = 'exact_word'
          AND config ? 'keywords' AND NOT config ? 'rules'
          AND jsonb_typeof(config->'keywords') = 'array'
        """
    )
    op.execute(
        """
        UPDATE "KBFilterPolicy"
        SET config = jsonb_build_object('rules', jsonb_build_array(
            'Block chunks that mention any of: '
            || (SELECT string_agg(k, ', ') FROM jsonb_array_elements_text(config->'keywords') AS k)
            || '.'))
        WHERE configformat = 'natural_language'
          AND config ? 'keywords' AND NOT config ? 'rules'
          AND jsonb_typeof(config->'keywords') = 'array'
          AND jsonb_array_length(config->'keywords') > 0
        """
    )


def downgrade() -> None:
    pass
