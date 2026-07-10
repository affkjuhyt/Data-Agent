"""merge secrets_vault and schema_catalog branches

Revision ID: c75156d1bd53
Revises: b8c9d0e1f2a3, d4e5f6a7b8c9
Create Date: 2026-07-09 09:42:35.151621

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c75156d1bd53'
down_revision: Union[str, None] = ('b8c9d0e1f2a3', 'd4e5f6a7b8c9')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
