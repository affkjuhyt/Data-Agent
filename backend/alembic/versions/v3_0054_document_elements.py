"""A document's elements, tables and cells, and the chunk columns that point at them (DI-06).

document.element holds a document's parts in reading order (kind, heading level, page, bbox,
the extractor that read them); document.doc_table a table of one element (rows, columns, header
rows, caption, the table it continues); document.table_cell its cells (span, text, the number
and unit read from it, page and bbox for a citation to the cell). document.chunk gains kind
(section | table), element_ids, parent_chunk_id (a Khoản under its Điều) and table_id (a table's
summary chunk). DOCUMENT_INTELLIGENCE_SPEC §4.1, API_AND_SCHEMA_CONTRACTS §2.5, §2.6; the table is
doc_table because the generator does not quote names and `table` is reserved. Existing chunks
become kind 'section' with no elements.

Fresh installs get the same from the generated dataagent.sql; every statement is idempotent.

Revision ID: v3_0054
Revises: v3_0053
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0054"
down_revision = "v3_0053"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

CREATE TABLE IF NOT EXISTS document.element (
  id uuid NOT NULL,
  org_id uuid NOT NULL,
  document_id uuid NOT NULL,
  page_no integer,
  order_idx integer NOT NULL,
  kind text NOT NULL,
  level smallint,
  bbox jsonb,
  text text,
  parent_element_id uuid,
  extractor text NOT NULL,
  extractor_version text NOT NULL,
  confidence numeric(5,4),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT element_pkey PRIMARY KEY (id),
  UNIQUE (document_id, order_idx),
  CHECK (order_idx >= 0),
  CHECK (kind IN ('text','heading','list','table','picture','caption','footnote','formula','header','footer','code','page_number')),
  CHECK ((kind = 'heading') = (level IS NOT NULL)),
  CHECK (level IS NULL OR level BETWEEN 1 AND 6),
  CHECK (extractor IN ('docling','legacy')),
  CHECK (bbox IS NULL OR jsonb_typeof(bbox) = 'object'),
  CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
);

CREATE TABLE IF NOT EXISTS document.doc_table (
  id uuid NOT NULL,
  org_id uuid NOT NULL,
  document_id uuid NOT NULL,
  element_id uuid NOT NULL,
  page_from integer,
  page_to integer,
  n_rows integer NOT NULL,
  n_cols integer NOT NULL,
  header_rows smallint NOT NULL DEFAULT 1,
  caption text,
  continues_table_id uuid,
  numeric_share numeric(5,4),
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT doc_table_pkey PRIMARY KEY (id),
  UNIQUE (element_id),
  CHECK (n_rows >= 0 AND n_cols >= 0),
  CHECK (header_rows >= 0),
  CHECK (page_to IS NULL OR page_from IS NULL OR page_to >= page_from),
  CHECK (numeric_share IS NULL OR numeric_share BETWEEN 0 AND 1),
  CHECK (continues_table_id IS NULL OR continues_table_id <> id)
);

CREATE TABLE IF NOT EXISTS document.table_cell (
  id uuid NOT NULL,
  org_id uuid NOT NULL,
  table_id uuid NOT NULL,
  row_idx integer NOT NULL,
  col_idx integer NOT NULL,
  row_span smallint NOT NULL DEFAULT 1,
  col_span smallint NOT NULL DEFAULT 1,
  text text NOT NULL,
  numeric_value numeric(28,6),
  unit text,
  is_header boolean NOT NULL DEFAULT false,
  page_no integer,
  bbox jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT table_cell_pkey PRIMARY KEY (id),
  UNIQUE (table_id, row_idx, col_idx),
  CHECK (row_idx >= 0 AND col_idx >= 0),
  CHECK (row_span >= 1 AND col_span >= 1),
  CHECK (bbox IS NULL OR jsonb_typeof(bbox) = 'object')
);

ALTER TABLE document.chunk ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'section';
ALTER TABLE document.chunk ADD COLUMN IF NOT EXISTS element_ids uuid[] NOT NULL DEFAULT '{}';
ALTER TABLE document.chunk ADD COLUMN IF NOT EXISTS parent_chunk_id uuid;
ALTER TABLE document.chunk ADD COLUMN IF NOT EXISTS table_id uuid;
ALTER TABLE document.chunk DROP CONSTRAINT IF EXISTS chunk_kind_check;
ALTER TABLE document.chunk ADD CONSTRAINT chunk_kind_check CHECK (kind IN ('section','table'));
ALTER TABLE document.chunk DROP CONSTRAINT IF EXISTS chunk_check2;
ALTER TABLE document.chunk ADD CONSTRAINT chunk_check2 CHECK ((kind = 'table') = (table_id IS NOT NULL));
ALTER TABLE document.chunk DROP CONSTRAINT IF EXISTS chunk_check3;
ALTER TABLE document.chunk ADD CONSTRAINT chunk_check3 CHECK (parent_chunk_id IS NULL OR parent_chunk_id <> id);

COMMENT ON TABLE document.element IS 'Một phần tử của tài liệu theo thứ tự đọc: đoạn, tiêu đề, mục danh sách, bảng, hình, chú thích, chú thích cuối trang, công thức, mã; kèm trang, khung toạ độ và bộ trích ra nó (Docling hoặc pipeline cũ). Chỉ ghi thêm; xử lý lại xoá theo tài liệu rồi ghi mới trong cùng giao dịch. Thêm 23/11/2026 (DI-06, DOCUMENT_INTELLIGENCE_SPEC §4.1). Use case: UC-05-01, UC-05-02.';
COMMENT ON COLUMN document.element.document_id IS 'FK document';
COMMENT ON COLUMN document.element.page_no IS 'Trang; null với định dạng không có trang (DOCX, HTML)';
COMMENT ON COLUMN document.element.order_idx IS 'Thứ tự đọc trong cả tài liệu, từ 0';
COMMENT ON COLUMN document.element.kind IS 'text, heading, list, table, picture, caption, footnote, formula, header, footer, code, page_number';
COMMENT ON COLUMN document.element.level IS 'Cấp tiêu đề 1..6 khi kind = heading';
COMMENT ON COLUMN document.element.bbox IS '{l, t, r, b, coord_origin} trên trang';
COMMENT ON COLUMN document.element.parent_element_id IS 'FK element: bảng hoặc hình của một chú thích, phần tử chứa dấu của chú thích cuối trang';
COMMENT ON COLUMN document.element.extractor IS 'docling, legacy';
COMMENT ON COLUMN document.element.extractor_version IS 'Ví dụ docling-2.150.0+adapter-1; xử lý lại khi đổi';
COMMENT ON COLUMN document.element.confidence IS 'Độ tin OCR của trang khi phần tử đến từ OCR';
COMMENT ON TABLE document.doc_table IS 'Một bảng của tài liệu, gắn với phần tử kind = table: số hàng, số cột, hàng tiêu đề, chú thích, bảng trang trước mà nó nối tiếp. Số trong bảng đọc qua table_cell bằng SQL, không qua chunk. Thêm 23/11/2026 (DI-06, DOCUMENT_INTELLIGENCE_SPEC §4.3). Use case: UC-05-02, UC-05-03.';
COMMENT ON COLUMN document.doc_table.document_id IS 'FK document';
COMMENT ON COLUMN document.doc_table.element_id IS 'FK element; một bảng mỗi phần tử';
COMMENT ON COLUMN document.doc_table.page_from IS 'Null với định dạng không có trang';
COMMENT ON COLUMN document.doc_table.continues_table_id IS 'FK doc_table: bảng trang trước mà bảng này nối tiếp (§4.3.1)';
COMMENT ON COLUMN document.doc_table.numeric_share IS 'Tỷ lệ ô có số; từ 0,3 thì table_query được dùng bảng';
COMMENT ON TABLE document.table_cell IS 'Một ô của bảng: vị trí, ô gộp, văn bản gốc, giá trị số đã nhân đơn vị và scale, ô tiêu đề, trang và khung toạ độ để trích dẫn đúng ô. Thêm 23/11/2026 (DI-06, DOCUMENT_INTELLIGENCE_SPEC §4.3, §4.4). Use case: UC-05-02, UC-05-03.';
COMMENT ON COLUMN document.table_cell.table_id IS 'FK doc_table';
COMMENT ON COLUMN document.table_cell.row_idx IS 'Từ 0';
COMMENT ON COLUMN document.table_cell.col_idx IS 'Từ 0';
COMMENT ON COLUMN document.table_cell.text IS 'Văn bản gốc của ô';
COMMENT ON COLUMN document.table_cell.numeric_value IS 'Giá trị số tất định theo gói ngôn ngữ và đơn vị; null khi ô không phải số';
COMMENT ON COLUMN document.table_cell.unit IS 'Ví dụ VND, USD, percent';
COMMENT ON COLUMN document.table_cell.page_no IS 'Null với định dạng không có trang';
COMMENT ON COLUMN document.chunk.kind IS 'section (đoạn theo tiêu đề; chunk con có parent_chunk_id), table (tóm tắt một bảng, §5.4); thêm 23/11/2026 (DI-06)';
COMMENT ON COLUMN document.chunk.element_ids IS 'logic → document.element mà chunk được dựng từ';
COMMENT ON COLUMN document.chunk.parent_chunk_id IS 'FK chunk: chunk cha mức Điều của một chunk con mức Khoản (§5.3)';
COMMENT ON COLUMN document.chunk.table_id IS 'FK doc_table khi kind = table';

CREATE INDEX IF NOT EXISTS element_document_id_page_no_idx ON document.element (document_id, page_no);
CREATE INDEX IF NOT EXISTS doc_table_document_id_idx ON document.doc_table (document_id);
CREATE INDEX IF NOT EXISTS chunk_parent_chunk_id_idx ON document.chunk (parent_chunk_id);
CREATE INDEX IF NOT EXISTS chunk_table_id_idx ON document.chunk (table_id);

ALTER TABLE document.element DROP CONSTRAINT IF EXISTS element_document_id_fkey;
ALTER TABLE document.element ADD CONSTRAINT element_document_id_fkey FOREIGN KEY (document_id) REFERENCES document.document(id) ON DELETE CASCADE;
ALTER TABLE document.element DROP CONSTRAINT IF EXISTS element_parent_element_id_fkey;
ALTER TABLE document.element ADD CONSTRAINT element_parent_element_id_fkey FOREIGN KEY (parent_element_id) REFERENCES document.element(id) ON DELETE CASCADE;
ALTER TABLE document.doc_table DROP CONSTRAINT IF EXISTS doc_table_document_id_fkey;
ALTER TABLE document.doc_table ADD CONSTRAINT doc_table_document_id_fkey FOREIGN KEY (document_id) REFERENCES document.document(id) ON DELETE CASCADE;
ALTER TABLE document.doc_table DROP CONSTRAINT IF EXISTS doc_table_element_id_fkey;
ALTER TABLE document.doc_table ADD CONSTRAINT doc_table_element_id_fkey FOREIGN KEY (element_id) REFERENCES document.element(id) ON DELETE CASCADE;
ALTER TABLE document.doc_table DROP CONSTRAINT IF EXISTS doc_table_continues_table_id_fkey;
ALTER TABLE document.doc_table ADD CONSTRAINT doc_table_continues_table_id_fkey FOREIGN KEY (continues_table_id) REFERENCES document.doc_table(id) ON DELETE SET NULL;
ALTER TABLE document.table_cell DROP CONSTRAINT IF EXISTS table_cell_table_id_fkey;
ALTER TABLE document.table_cell ADD CONSTRAINT table_cell_table_id_fkey FOREIGN KEY (table_id) REFERENCES document.doc_table(id) ON DELETE CASCADE;
ALTER TABLE document.chunk DROP CONSTRAINT IF EXISTS chunk_parent_chunk_id_fkey;
ALTER TABLE document.chunk ADD CONSTRAINT chunk_parent_chunk_id_fkey FOREIGN KEY (parent_chunk_id) REFERENCES document.chunk(id) ON DELETE CASCADE;
ALTER TABLE document.chunk DROP CONSTRAINT IF EXISTS chunk_table_id_fkey;
ALTER TABLE document.chunk ADD CONSTRAINT chunk_table_id_fkey FOREIGN KEY (table_id) REFERENCES document.doc_table(id) ON DELETE CASCADE;

ALTER TABLE document.element ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON document.element;
CREATE POLICY org_isolation ON document.element USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid) WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid);
ALTER TABLE document.doc_table ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON document.doc_table;
CREATE POLICY org_isolation ON document.doc_table USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid) WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid);
ALTER TABLE document.table_cell ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON document.table_cell;
CREATE POLICY org_isolation ON document.table_cell USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid) WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid);

GRANT SELECT, INSERT, UPDATE, DELETE ON document.element TO svc_document;
GRANT SELECT ON document.element TO document_read;
GRANT SELECT, INSERT, UPDATE, DELETE ON document.doc_table TO svc_document;
GRANT SELECT ON document.doc_table TO document_read;
GRANT SELECT, INSERT, UPDATE, DELETE ON document.table_cell TO svc_document;
GRANT SELECT ON document.table_cell TO document_read;

RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL))


# Reversible, unlike most revisions: the tables are new and nothing reads them yet (DI-09 wires
# the extractor), so going back drops what this revision added and loses no data written before it.
DOWN = """
SET LOCAL ROLE da_owner;

ALTER TABLE document.chunk DROP CONSTRAINT IF EXISTS chunk_table_id_fkey;
ALTER TABLE document.chunk DROP CONSTRAINT IF EXISTS chunk_parent_chunk_id_fkey;
ALTER TABLE document.chunk DROP CONSTRAINT IF EXISTS chunk_check3;
ALTER TABLE document.chunk DROP CONSTRAINT IF EXISTS chunk_check2;
ALTER TABLE document.chunk DROP CONSTRAINT IF EXISTS chunk_kind_check;
DROP INDEX IF EXISTS document.chunk_table_id_idx;
DROP INDEX IF EXISTS document.chunk_parent_chunk_id_idx;
ALTER TABLE document.chunk DROP COLUMN IF EXISTS table_id;
ALTER TABLE document.chunk DROP COLUMN IF EXISTS parent_chunk_id;
ALTER TABLE document.chunk DROP COLUMN IF EXISTS element_ids;
ALTER TABLE document.chunk DROP COLUMN IF EXISTS kind;
DROP TABLE IF EXISTS document.table_cell;
DROP TABLE IF EXISTS document.doc_table;
DROP TABLE IF EXISTS document.element;

RESET ROLE;
"""


def downgrade() -> None:
    op.execute(sa.text(DOWN))
