"""A HITL request may be org-wide and may be assigned to a capability (tab 3b inbox.hitl_request,
decided 12/10/2026).

An audit export over 100 000 rows or an emergency grant belongs to no Data Space, so
data_space_id may be null; approvals routed by role (steward, then compliance) name the
capability in assignee_capability instead of a person or a user group.

Fresh installs get the same from the generated dataagent.sql (backend/schema/csdl/gen_ddl.py);
this revision is idempotent so both paths end equal.

Revision ID: v3_0010
Revises: v3_0009
"""

from alembic import op

revision = "v3_0010"
down_revision = "v3_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE inbox.hitl_request ALTER COLUMN data_space_id DROP NOT NULL;
        ALTER TABLE inbox.hitl_request ADD COLUMN IF NOT EXISTS assignee_capability text;

        -- The generated CHECK is unnamed; find it by what it says, not by the name Postgres gave it.
        DO $$
        DECLARE
          c text;
        BEGIN
          FOR c IN
            SELECT conname FROM pg_constraint
             WHERE conrelid = 'inbox.hitl_request'::regclass AND contype = 'c'
               AND pg_get_constraintdef(oid) LIKE '%assignee_id IS NOT NULL%'
               AND pg_get_constraintdef(oid) NOT LIKE '%assignee_capability%'
          LOOP
            EXECUTE format('ALTER TABLE inbox.hitl_request DROP CONSTRAINT %I', c);
          END LOOP;
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = 'inbox.hitl_request'::regclass AND contype = 'c'
               AND pg_get_constraintdef(oid) LIKE '%assignee_capability%'
          ) THEN
            ALTER TABLE inbox.hitl_request ADD CONSTRAINT hitl_request_assignee_check
              CHECK (assignee_id IS NOT NULL OR assignee_group IS NOT NULL
                     OR assignee_capability IS NOT NULL);
          END IF;
        END $$;

        COMMENT ON COLUMN inbox.hitl_request.data_space_id IS 'Rỗng với yêu cầu toàn tổ chức (bản xuất nhật ký kiểm toán, quyền khẩn cấp); khi đó năng lực của người duyệt xét ở cấp tổ chức. Sửa 12/10/2026';
        COMMENT ON COLUMN inbox.hitl_request.assignee_capability IS 'Năng lực của người duyệt (steward, compliance, audit_read…): ai có năng lực này trong Data Space của yêu cầu, hoặc ở cấp tổ chức khi data_space_id rỗng, thấy và duyệt được. Thêm 12/10/2026';
        COMMENT ON COLUMN inbox.hitl_approval_step.approver_role IS 'Năng lực người duyệt bước này phải có, ví dụ steward, compliance; mỗi bước một người khác (BR-132)';
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Restore the database instead; org-wide requests have no space to go back to"
    )
