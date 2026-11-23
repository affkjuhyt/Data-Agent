"""Every answer that states numbers carries a receipt, in a data product or not (AE-05).

Until now only an answer inside a data product had a receipt (BR-175): the explore, verified-query
and agentic paths and a governed answer outside every product sealed none. qa.answer_receipt's
data_product_id, data_product_code and data_product_version become nullable, together (one CHECK,
named as a fresh install names it); the insert trigger lets a receipt without a product belong to
an answer that is not in scope, and still holds an in-scope answer's receipt to its product.

Fresh installs get the same from the generated dataagent.sql; every statement is idempotent.

Revision ID: v3_0049
Revises: v3_0048
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0049"
down_revision = "v3_0048"
branch_labels = None
depends_on = None

SQL = r"""
SET LOCAL ROLE da_owner;

ALTER TABLE qa.answer_receipt ALTER COLUMN data_product_id DROP NOT NULL;
ALTER TABLE qa.answer_receipt ALTER COLUMN data_product_code DROP NOT NULL;
ALTER TABLE qa.answer_receipt ALTER COLUMN data_product_version DROP NOT NULL;
ALTER TABLE qa.answer_receipt DROP CONSTRAINT IF EXISTS answer_receipt_check4;
ALTER TABLE qa.answer_receipt ADD CONSTRAINT answer_receipt_check4
  CHECK ((data_product_id IS NULL) = (data_product_code IS NULL)
         AND (data_product_id IS NULL) = (data_product_version IS NULL));
COMMENT ON COLUMN qa.answer.product_scope IS 'in_scope, out_of_scope, not_applicable (làm rõ, từ chối); out_of_scope hiển thị nhãn “Ngoài phạm vi data product”; từ AE-05 câu ngoài phạm vi vẫn có biên nhận, không mang data product';
COMMENT ON TABLE qa.answer_receipt IS 'Biên nhận của một câu trả lời có số: data product và phiên bản khi câu trả lời trong phạm vi, phiên bản bộ biên dịch và Agent runtime, mô hình và provider của mọi lần gọi, độ tươi lúc trả lời, mã băm toàn biên nhận. Ghi cùng giao dịch với qa.answer, khoá ngay, chỉ ghi thêm (BR-176, ADR-43). Từ 23/11/2026 (AE-05) mọi đường trả lời (governed, khám phá, verified query, agentic) ghi biên nhận cho câu trả lời có số; câu ngoài data product có biên nhận không mang data product. Thêm 10/10/2026. Use case: UC-01-07, UC-01-01, UC-07-14.';
COMMENT ON COLUMN qa.answer_receipt.data_product_id IS 'logic → context.data_product; null khi câu trả lời ngoài data product (AE-05)';
COMMENT ON COLUMN qa.answer_receipt.compiler_version IS 'Phiên bản semantic compiler, ví dụ semcomp 1.8.2+4f2a91c; none khi số không qua semantic compiler (khám phá, agentic)';

CREATE OR REPLACE FUNCTION qa.answer_receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  a_scope text;
  a_product uuid;
  prefix text;
  last_no integer;
BEGIN
  IF TG_OP = 'INSERT' THEN
    SELECT product_scope, data_product_id INTO a_scope, a_product FROM qa.answer WHERE id = NEW.answer_id;
    IF NEW.data_product_id IS NULL THEN
      IF a_scope = 'in_scope' THEN
        RAISE EXCEPTION 'Câu trả lời trong phạm vi data product phải có biên nhận mang data product đó (BR-175)'
          USING ERRCODE = 'check_violation';
      END IF;
    ELSIF a_scope IS DISTINCT FROM 'in_scope' OR a_product IS DISTINCT FROM NEW.data_product_id THEN
      RAISE EXCEPTION 'Biên nhận chỉ cấp cho câu trả lời trong phạm vi cùng data product (BR-175)'
        USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.sealed_at IS NOT NULL OR NEW.receipt_sha256 IS NOT NULL THEN
      RAISE EXCEPTION 'Biên nhận mới chưa được khoá; khoá sau khi ghi đủ số (BR-176)'
        USING ERRCODE = 'check_violation';
    END IF;
    -- Serialise numbering per organisation and day so two answers never get the same receipt_no.
    prefix := 'BN-' || to_char(NEW.answered_at AT TIME ZONE 'UTC', 'YYYYMMDD') || '-';
    PERFORM pg_advisory_xact_lock(hashtextextended('qa.answer_receipt:' || NEW.org_id || ':' || prefix, 0));
    SELECT max(substr(receipt_no, 13)::integer) INTO last_no
      FROM qa.answer_receipt WHERE org_id = NEW.org_id AND receipt_no LIKE prefix || '%';
    NEW.receipt_no := prefix || lpad((coalesce(last_no, 0) + 1)::text, 6, '0');
    NEW.figure_count := 0;
    NEW.masked_count := 0;
    RETURN NEW;
  ELSIF TG_OP = 'UPDATE' THEN
    IF OLD.sealed_at IS NOT NULL OR NEW.sealed_at IS NULL
       OR to_jsonb(NEW) - ARRAY['sealed_at', 'figure_count', 'masked_count', 'receipt_sha256']
          IS DISTINCT FROM to_jsonb(OLD) - ARRAY['sealed_at', 'figure_count', 'masked_count', 'receipt_sha256'] THEN
      RAISE EXCEPTION 'Biên nhận chỉ ghi thêm: chỉ được đặt sealed_at một lần (BR-176)'
        USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*), count(*) FILTER (WHERE display_state <> 'shown')
      INTO NEW.figure_count, NEW.masked_count
      FROM qa.answer_receipt_figure WHERE receipt_id = NEW.id;
    NEW.receipt_sha256 := qa.receipt_digest(NEW);
    RETURN NEW;
  ELSE
    IF OLD.retain_until >= current_date THEN
      RAISE EXCEPTION 'Biên nhận còn trong hạn lưu giữ, không được xoá (BR-176)'
        USING ERRCODE = 'check_violation';
    END IF;
    PERFORM qa.answer_receipt_purge_figures(OLD.id);
    RETURN OLD;
  END IF;
END
$$;

RESET ROLE;
"""


def upgrade() -> None:
    op.execute(sa.text(SQL))


def downgrade() -> None:
    raise NotImplementedError("v3_0049 is not reversed")
