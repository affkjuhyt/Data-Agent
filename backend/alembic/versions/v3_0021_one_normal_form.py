"""Stored phrases in the form a question is matched in (GEN-31).

The api wrote context.synonym.phrase_norm and context.glossary_term.term_norm with punctuation kept
("doanh-thu", "tỷ lệ (%)" → "ty le (%)"); the worker matches a question with punctuation turned to
spaces, so such a synonym never matched. Both now use libs/language.norm; this revision writes the
stored rows again in that form. The function is copied here, as a migration is frozen: a later
change to libs/language does not rewrite history.

A row whose new form another row of its Data Space already holds is left as it was (the unique
keys would refuse it, and which of the two a steward meant is theirs to say); the count is logged.
Idempotent: a row already in the form is not touched.

Revision ID: v3_0021
Revises: v3_0020
"""

import logging
import re
import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "v3_0021"
down_revision = "v3_0020"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text).lower().replace("đ", "d")
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )
    return " ".join(re.sub(r"[^\w%/]+", " ", stripped).split())


# (table, source column, normal-form column, the columns the form is unique within)
TABLES = (
    ("context.synonym", "phrase", "phrase_norm", ("data_space_id", "target_kind", "target_id")),
    ("context.glossary_term", "term", "term_norm", ("data_space_id",)),
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("SET LOCAL ROLE da_owner"))
    for table, source, column, scope in TABLES:
        keys = ", ".join(scope)
        rows = bind.execute(sa.text(f"SELECT id, {source}, {column}, {keys} FROM {table}")).all()
        taken = {(tuple(r[3:]), r[2]) for r in rows}
        kept = 0
        for row in rows:
            new = _norm(row[1])
            if new == row[2]:
                continue
            if (tuple(row[3:]), new) in taken:
                kept += 1
                continue
            bind.execute(
                sa.text(f"UPDATE {table} SET {column} = :new WHERE id = :id"),
                {"new": new, "id": row[0]},
            )
            taken.discard((tuple(row[3:]), row[2]))
            taken.add((tuple(row[3:]), new))
        if kept:
            logger.warning("v3_0021: %d %s rows kept their form: another row holds it", kept, table)
    bind.execute(sa.text("RESET ROLE"))


def downgrade() -> None:
    raise NotImplementedError("v3_0021 is not reversed: the earlier form never matched")
