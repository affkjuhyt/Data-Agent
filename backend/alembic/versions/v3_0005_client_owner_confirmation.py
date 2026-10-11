"""An API client acts for its owner only once the owner agrees (BR-48, tab 3b integration.api_client).

An administrator who grants a client to someone else could otherwise borrow that person's rights.
The client now waits in `pending_owner` until the owner confirms, recorded in owner_confirmed_at.
Clients granted before this revision by someone other than their owner go back to pending_owner;
those the owner granted themselves count as confirmed when they were created.

Fresh installs get the same column and CHECKs from the generated dataagent.sql
(backend/schema/csdl/gen_ddl.py); this revision is idempotent so both paths end equal.

Revision ID: v3_0005
Revises: v3_0004
"""

from alembic import op

revision = "v3_0005"
down_revision = "v3_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE integration.api_client ADD COLUMN IF NOT EXISTS owner_confirmed_at timestamptz;

        ALTER TABLE integration.api_client DROP CONSTRAINT IF EXISTS api_client_status_check;
        ALTER TABLE integration.api_client ADD CONSTRAINT api_client_status_check
          CHECK (status IN ('pending_owner','active','paused','revoked','expired'));

        UPDATE integration.api_client SET status = 'pending_owner'
         WHERE status IN ('active','paused') AND owner_confirmed_at IS NULL
           AND created_by IS DISTINCT FROM owner_user_id;
        UPDATE integration.api_client SET owner_confirmed_at = created_at
         WHERE status IN ('active','paused') AND owner_confirmed_at IS NULL;

        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid = 'integration.api_client'::regclass AND contype = 'c'
               AND pg_get_constraintdef(oid) LIKE '%owner_confirmed_at%'
          ) THEN
            ALTER TABLE integration.api_client ADD CONSTRAINT api_client_owner_confirmed_check
              CHECK (status NOT IN ('active','paused') OR owner_confirmed_at IS NOT NULL);
          END IF;
        END $$;

        COMMENT ON COLUMN integration.api_client.status IS 'pending_owner, active, paused, revoked, expired; pending_owner khi người cấp không phải người phụ trách: client chưa gọi được cho tới khi người phụ trách xác nhận (BR-48)';
        COMMENT ON COLUMN integration.api_client.owner_confirmed_at IS 'Lúc người phụ trách đồng ý để client hành động với quyền của mình (BR-48); bằng created_at khi người cấp chính là người phụ trách. Mở rộng tools hoặc data_space_ids bởi người khác đặt lại về rỗng và status về pending_owner';
        """
    )


def downgrade() -> None:
    raise NotImplementedError("Restore the database instead; pending clients have no earlier state")
