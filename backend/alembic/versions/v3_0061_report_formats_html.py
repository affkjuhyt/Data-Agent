"""A report is rendered as html by default, no longer as a pdf it never made (AL-13).

monitor.report_schedule.formats defaulted to {pdf}, which the worker does not render (a pdf
needs a headless browser and Vietnamese fonts in its image, ANALYTICS_SPEC §7): every schedule
saved with the default produced a `not_supported` output and a partially sent run. The default
becomes {html}; a schedule asking for pdf asks for html instead, without listing it twice.

Revision ID: v3_0061
Revises: v3_0060
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0061"
down_revision = "v3_0060"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

ALTER TABLE monitor.report_schedule ALTER COLUMN formats SET DEFAULT '{html}';
UPDATE monitor.report_schedule
   SET formats = ARRAY(
         SELECT f FROM (
           SELECT DISTINCT ON (f) f, i
             FROM unnest(array_replace(formats, 'pdf', 'html')) WITH ORDINALITY AS t(f, i)
            ORDER BY f, i
         ) kept ORDER BY i
       )
 WHERE 'pdf' = ANY(formats);
COMMENT ON COLUMN monitor.report_schedule.formats IS 'html, xlsx; pdf chưa render (AL-13, mặc định đổi từ pdf ngày 23/11/2026)';

-- alembic writes alembic_version after this, as the migrating user: da_owner has no right on it.
RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    # The schedules that asked for pdf are not told apart from those that asked for html.
    op.execute(
        sa.text(
            """
            SET LOCAL ROLE da_owner;
            ALTER TABLE monitor.report_schedule ALTER COLUMN formats SET DEFAULT '{pdf}';
            COMMENT ON COLUMN monitor.report_schedule.formats IS 'pdf, xlsx, html';
            RESET ROLE;
            """
        )
    )
