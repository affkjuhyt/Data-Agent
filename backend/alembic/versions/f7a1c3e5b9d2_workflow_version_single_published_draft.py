"""workflow_version_single_published_draft

publish_workflow_version / republish_workflow_version archive the current
published version and then flip the target, and create_draft_version checks
"no draft yet" with a SELECT before inserting. With nothing to serialise them,
two concurrent requests could leave two published versions (the runtime then
picks one arbitrarily) or two drafts. The repository now locks the parent
workflow row; these partial unique indexes are the backstop that makes the
invariant true whatever writes the table.

Upgrade fails if a workflow already has two published (or two draft) versions:
that data is exactly what the indexes forbid, and needs a human decision on
which one wins.

Revision ID: f7a1c3e5b9d2
Revises: e4a6c8b0d2f1
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f7a1c3e5b9d2"
down_revision: str | None = "e4a6c8b0d2f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflowversions_one_published "
        'ON "WorkflowVersions" (workflow_id) WHERE status = \'published\''
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_workflowversions_one_draft "
        'ON "WorkflowVersions" (workflow_id) WHERE status = \'draft\''
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_workflowversions_one_draft")
    op.execute("DROP INDEX IF EXISTS uq_workflowversions_one_published")
