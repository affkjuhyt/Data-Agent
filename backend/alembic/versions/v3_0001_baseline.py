"""Create the V3 dataagent and dataagent_audit databases.

Revision ID: v3_0001
Revises:
"""

from pathlib import Path

from alembic import op

revision = "v3_0001"
down_revision = None
branch_labels = None
depends_on = None


def _apply_sql(filename: str) -> None:
    sql_path = Path(__file__).resolve().parents[1] / "sql" / filename
    op.get_bind().exec_driver_sql(sql_path.read_text(encoding="utf-8"))


def upgrade() -> None:
    _apply_sql("dataagent.sql")
    _apply_sql("dataagent_audit.sql")


def downgrade() -> None:
    raise NotImplementedError("The V3 baseline is not reversible; restore the database instead")
