"""A skill version that has run is never deleted (AC-03-06-04, BR-97).

An old skill_run must still show how it computed, so retiring hides a version instead of
deleting it. Fresh installs get the trigger from the generated dataagent.sql
(routines/skill.sql); this revision adds it on clusters already migrated, idempotently.

Revision ID: v3_0008
Revises: v3_0007
"""

from alembic import op

revision = "v3_0008"
down_revision = "v3_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        SET LOCAL ROLE da_owner;
        CREATE OR REPLACE FUNCTION skill.skill_version_keep_if_run() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF EXISTS (SELECT 1 FROM skill.skill_run r WHERE r.skill_version_id = OLD.id) THEN
            RAISE EXCEPTION 'Phiên bản kỹ năng đã chạy không xoá được; hãy ngừng dùng (BR-97)'
              USING ERRCODE = 'check_violation';
          END IF;
          RETURN OLD;
        END $$;
        DROP TRIGGER IF EXISTS skill_version_keep_if_run ON skill.skill_version;
        CREATE TRIGGER skill_version_keep_if_run BEFORE DELETE ON skill.skill_version
          FOR EACH ROW EXECUTE FUNCTION skill.skill_version_keep_if_run();
        RESET ROLE;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS skill_version_keep_if_run ON skill.skill_version")
    op.execute("DROP FUNCTION IF EXISTS skill.skill_version_keep_if_run()")
