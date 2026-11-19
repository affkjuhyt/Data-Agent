"""eval.eval_item carries what a golden case needs (EV-08): the action, the gap, the rubric, the
verification and the snapshot (docs/blueprint/DATASET_AND_EVALUATION.md §5.1).

The defaults keep every existing item what it was: an answer to check exactly, a draft, no snapshot.
A fresh install has the columns from the generated DDL already; each statement is a no-op there.

Revision ID: v3_0037
Revises: v3_0036
"""

import sqlalchemy as sa
from alembic import op

revision = "v3_0037"
down_revision = "v3_0036"
branch_labels = None
depends_on = None

CHECKS = {
    "eval_item_expected_action_check": (
        "expected_action IN ('answer','clarify','refuse_missing_source','refuse_permission',"
        "'handover','flag_conflict')"
    ),
    "eval_item_failure_category_check": (
        "failure_category IN ('none','ambiguous','missing_source','wrong_schema',"
        "'conflicting_metrics','unavailable_document','corrupted_file','duplicates','schema_drift',"
        "'permission_violation','injection','stale_data','partial_ingestion')"
    ),
    # Brackets, not backslashes: the design export reads a backslash as markdown.
    "eval_item_rubric_check": (
        "rubric ~ '^(exact|set_equality|contains_all|numeric_tolerance[(][0-9]+([.][0-9]+)?[)]"
        "|judge[(][A-Za-z0-9_.-]+[)])$'"
    ),
    "eval_item_verification_status_check": (
        "verification_status IN ('draft','sql_run_on_snapshot','human_verified','disputed')"
    ),
}

SQL = (
    """
SET LOCAL ROLE da_owner;

ALTER TABLE eval.eval_item
  ADD COLUMN IF NOT EXISTS expected_action text NOT NULL DEFAULT 'answer',
  ADD COLUMN IF NOT EXISTS failure_category text NOT NULL DEFAULT 'none',
  ADD COLUMN IF NOT EXISTS rubric text NOT NULL DEFAULT 'exact',
  ADD COLUMN IF NOT EXISTS verification_status text NOT NULL DEFAULT 'draft',
  ADD COLUMN IF NOT EXISTS snapshot_ref jsonb;

DO $$ BEGIN
"""
    + "".join(
        f"""  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = '{name}'
      AND conrelid = 'eval.eval_item'::regclass
  ) THEN
    ALTER TABLE eval.eval_item ADD CONSTRAINT {name} CHECK ({check});
  END IF;
"""
        for name, check in CHECKS.items()
    )
    + """END $$;

RESET ROLE;
"""
)


def upgrade() -> None:
    op.execute(sa.text(SQL.replace(":", "\\:")))


def downgrade() -> None:
    raise NotImplementedError("v3_0037 is not reversed")
