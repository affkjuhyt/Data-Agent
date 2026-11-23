"""A question typed without diacritics finds its passage (DI-11, DOCUMENT_INTELLIGENCE_SPEC §6.2).

unaccent (a trusted contrib extension) and public.da_fold, its IMMUTABLE wrapper (lower case, the
marks taken off, đ as d), and a GIN index on document.chunk over the folded header and text, which
the keyword half of retrieval matches on a second branch (worker_agent documents/retrieve.py). An
expression index rather than the stored text_folded column the spec drew: the same match, no column.

Fresh installs get the same from the generated dataagent.sql; every statement is idempotent.

Revision ID: v3_0060
Revises: v3_0059
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0060"
down_revision = "v3_0059"
branch_labels = None
depends_on = None

SQL = r"""
CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public;
SET LOCAL ROLE da_owner;

CREATE OR REPLACE FUNCTION public.da_fold(t text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $$
  SELECT lower(public.unaccent('public.unaccent'::regdictionary,
                               replace(replace(t, 'đ', 'd'), 'Đ', 'D')))
$$;
CREATE INDEX IF NOT EXISTS chunk_text_folded_idx ON document.chunk
  USING gin (to_tsvector('simple', public.da_fold(coalesce(context_header, '') || ' ' || text)));

RESET ROLE;
"""

DOWN = """
SET LOCAL ROLE da_owner;
DROP INDEX IF EXISTS document.chunk_text_folded_idx;
DROP FUNCTION IF EXISTS public.da_fold(text);
RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL))


def downgrade() -> None:
    op.execute(sa.text(DOWN))
