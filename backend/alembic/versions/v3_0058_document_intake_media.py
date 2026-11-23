"""A document is what its content says: workbooks and text files are read (DI-10).

document.document.media takes xlsx (a workbook, read as tables by cell type) and text (a .txt or
.md file); the upload decides a file's kind by its content first (libs/document_intake) and
refuses what the platform does not read with 415. document.element.extractor takes openpyxl, the
reader of a workbook's sheets. Both CHECKs are single-column, so their names are Postgres' own.

Fresh installs get the same from the generated dataagent.sql; every statement is idempotent.

Revision ID: v3_0058
Revises: v3_0057
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0058"
down_revision = "v3_0057"
branch_labels = None
depends_on = None

SQL = """
SET LOCAL ROLE da_owner;

ALTER TABLE document.document DROP CONSTRAINT IF EXISTS document_media_check;
ALTER TABLE document.document ADD CONSTRAINT document_media_check
  CHECK (media IN ('pdf','image','docx','xlsx','text','email','audio','html'));
ALTER TABLE document.element DROP CONSTRAINT IF EXISTS element_extractor_check;
ALTER TABLE document.element ADD CONSTRAINT element_extractor_check
  CHECK (extractor IN ('docling','legacy','openpyxl'));
COMMENT ON COLUMN document.collection.media_kinds IS 'pdf, image, docx, xlsx, text, email, audio, html';
COMMENT ON COLUMN document.document.media IS 'pdf, image, docx, xlsx, text, email, audio, html; theo nội dung tệp, không theo đuôi (DI-10, 23/11/2026)';
COMMENT ON COLUMN document.element.extractor IS 'docling, legacy, openpyxl (XLSX, DI-10)';

RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL))


# Back to v3_0057's CHECKs, NOT VALID: a workbook or a text file already read keeps its row, new
# ones are refused (the chain tests go down through this revision to v3_0053, DI-06).
DOWN = """
SET LOCAL ROLE da_owner;

ALTER TABLE document.document DROP CONSTRAINT IF EXISTS document_media_check;
ALTER TABLE document.document ADD CONSTRAINT document_media_check
  CHECK (media IN ('pdf','image','docx','email','audio','html')) NOT VALID;
ALTER TABLE document.element DROP CONSTRAINT IF EXISTS element_extractor_check;
ALTER TABLE document.element ADD CONSTRAINT element_extractor_check
  CHECK (extractor IN ('docling','legacy')) NOT VALID;

RESET ROLE;
"""


def downgrade() -> None:
    op.execute(sa.text(DOWN))
