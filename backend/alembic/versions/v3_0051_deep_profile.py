"""Deeper column profiles and the source's own freshness (DF-10).

A schema scan's profile kept counts, bounds and the most frequent values only: the catalogue had
no median, no distribution of a number and no shape of a code. column_profile gains quantiles, a
20-bin histogram, length statistics and the value patterns of a column; dataset gains what
PostgreSQL's pg_stat_user_tables says about the table's writes and last analyze. All nullable:
the profiles written before keep what they had.

Revision ID: v3_0051
Revises: v3_0050
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0051"
down_revision = "v3_0050"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

ALTER TABLE catalog.dataset ADD COLUMN IF NOT EXISTS source_activity jsonb;
COMMENT ON COLUMN catalog.dataset.source_activity IS 'DF-10: độ tươi ở nguồn {n_tup_ins, n_tup_upd, n_tup_del, n_live_tup, last_analyze, last_autoanalyze, observed_at} từ pg_stat_user_tables; null với nguồn khác PostgreSQL';

ALTER TABLE catalog.column_profile ADD COLUMN IF NOT EXISTS quantiles jsonb;
ALTER TABLE catalog.column_profile ADD COLUMN IF NOT EXISTS histogram jsonb;
ALTER TABLE catalog.column_profile ADD COLUMN IF NOT EXISTS length_stats jsonb;
ALTER TABLE catalog.column_profile ADD COLUMN IF NOT EXISTS value_patterns jsonb;
COMMENT ON COLUMN catalog.column_profile.quantiles IS 'DF-10: {p05, p25, p50, p75, p95} của cột số hoặc ngày trên mẫu đã quét';
COMMENT ON COLUMN catalog.column_profile.histogram IS 'DF-10: {lo, hi, bins} — 20 khoảng đều của cột số trên mẫu';
COMMENT ON COLUMN catalog.column_profile.length_stats IS 'DF-10: {min, max, avg} độ dài của cột chuỗi';
COMMENT ON COLUMN catalog.column_profile.value_patterns IS 'DF-10: [{pattern, share}], tối đa 3 mẫu giá trị dạng regex sinh từ chữ số và chữ cái của mẫu';

-- alembic writes alembic_version after this, as the migrating user: da_owner has no right on it.
RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            SET LOCAL ROLE da_owner;
            ALTER TABLE catalog.column_profile DROP COLUMN IF EXISTS value_patterns;
            ALTER TABLE catalog.column_profile DROP COLUMN IF EXISTS length_stats;
            ALTER TABLE catalog.column_profile DROP COLUMN IF EXISTS histogram;
            ALTER TABLE catalog.column_profile DROP COLUMN IF EXISTS quantiles;
            ALTER TABLE catalog.dataset DROP COLUMN IF EXISTS source_activity;
            RESET ROLE;
            """
        )
    )
