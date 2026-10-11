"""Create the V3 `dataagent` database: 23 schemas, roles, row-level security, routines.

The audit database is v3_0002's (it lives apart, D1 item 1). Clusters that ran an earlier
version of this revision also got the audit schema inside `dataagent`; v3_0002 moves it.

Revision ID: v3_0001
Revises:
"""

from pathlib import Path

from alembic import op
from psycopg import ClientCursor

revision = "v3_0001"
down_revision = None
branch_labels = None
depends_on = None


def _apply_sql(filename: str) -> None:
    sql_path = Path(__file__).resolve().parents[1] / "sql" / filename
    # The generated files contain PL/pgSQL format() directives such as %I and
    # %L.  Psycopg's normal cursor treats those as Python placeholders; the
    # client cursor sends the script as literal SQL while retaining Alembic's
    # transaction on the same DB connection.
    connection = op.get_bind().connection.driver_connection
    with ClientCursor(connection) as cursor:
        cursor.execute(sql_path.read_text(encoding="utf-8"))


def upgrade() -> None:
    _apply_sql("dataagent.sql")


def downgrade() -> None:
    raise NotImplementedError("The V3 baseline is not reversible; restore the database instead")
