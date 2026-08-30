"""rename secrets_vault value_path to openbao_path

Gap fix: b8c9d0e1f2a3_auth_api_secrets_vault_schema created secrets_vault
with a column named `value_path`, but backend/services/auth-api/routers/secrets.py
has always read/written this column as `openbao_path` (insert/rotate — see
create_secret/rotate_secret). This mismatch meant a real INSERT into
secrets_vault would fail with UndefinedColumn the first time the route ran
against real Postgres (discovered while wiring up DB Connector's
DB_CONNECTOR_PASSWORD secrets — same class of "never run against a real DB"
gap as the table itself). tests/integration/conftest.py's fixture also says
`value_path`, so this specific bug was never caught by integration tests
either. Renaming the column to match the application code (the actual
runtime behavior), not the other way around.

Revision ID: 57abfbeabb56
Revises: c75156d1bd53
Create Date: 2026-07-09 10:02:25.784679

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '57abfbeabb56'
down_revision: Union[str, None] = 'c75156d1bd53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('secrets_vault', 'value_path', new_column_name='openbao_path')


def downgrade() -> None:
    op.alter_column('secrets_vault', 'openbao_path', new_column_name='value_path')
