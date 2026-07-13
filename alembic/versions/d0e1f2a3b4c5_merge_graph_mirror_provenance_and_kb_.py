"""merge graph_mirror_provenance and kb_fts_index branches

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4, f2b3c4d5e6f7
Create Date: 2026-07-13

c9d0e1f2a3b4 (graph_mirror_provenance, forked off c3d4e5f6a7b8) was left as a
dangling head after being renamed away from its original id d4e5f6a7b8c9
(collided with d4e5f6a7b8c9_schema_catalog.py — see that migration's
docstring). This merge joins it back into the main line at the other current
head, f2b3c4d5e6f7 — same no-op merge pattern as c75156d1bd53.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, None] = ('c9d0e1f2a3b4', 'f2b3c4d5e6f7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
