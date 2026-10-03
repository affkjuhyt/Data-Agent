"""schema_hygiene

Closes 10-database-design §2.3's ORM/migration drift and two missing constraints
(the ORM side of the drift is fixed in the same commit; `alembic check` now runs
in tests/integration/workflow_runtime/test_alembic_migrations.py):

  * "Agents".published_version_id / draft_version_id -> "AgentVersions".id: the
    ORM declared both FKs, no migration created them. No dangling pointer on
    dev (03/10/2026); delete_agent already clears both before deleting versions.
  * "KBWarehouse".tenant_id NOT NULL, as the ORM declares (no NULL on dev): its
    row-level policy cannot match a NULL tenant.
  * "KBTextBlockVersion" UNIQUE (block_id, version_number): two rows for one
    version of a block made "the" version ambiguous. No duplicate on dev.
  * CHECK on the status columns, from the values the code writes (_STATUSES).

Revision ID: 8e4b6d1f3a7c
Revises: 7d2a5f9c4e6b
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "8e4b6d1f3a7c"
down_revision: str | None = "7d2a5f9c4e6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AGENT_FKS = (
    ("fk_agents_published_version", "published_version_id", "AgentVersions.id currently served."),
    ("fk_agents_draft_version", "draft_version_id", "AgentVersions.id of the editable draft."),
)
_AGENT_FK_COMMENTS_BEFORE = {
    "published_version_id": (
        "AgentVersions.id currently served. The ORM declares this FK but no migration created it."
    ),
    "draft_version_id": (
        "AgentVersions.id of the editable draft; draft updates find it by AgentVersions.status "
        "instead. The ORM declares this FK but no migration created it."
    ),
}

# table -> allowed status values (CHECK ck_<table>_status): every value a code
# path writes, found 03/10/2026; none is caller-supplied. KBConnections and MCP
# are left out: nothing writes their status, so there is no set to pin.
# AgentVersions allows 'archived' like WorkflowVersions, which the UI expects.
# key_rotations.status is nullable; a CHECK passes NULL.
_STATUSES: dict[str, tuple[str, ...]] = {
    "AgentTraces": ("success", "error"),
    "AgentVersions": ("draft", "published", "archived"),
    "WorkflowVersions": ("draft", "published", "archived"),
    "AuditLogs": ("SUCCESS", "FAILED"),
    "Conversations": ("active",),
    "EpisodicMemory": ("active", "archived"),
    "FutureMemory": ("pending", "executed", "cancelled"),
    "KBConflict": ("pending", "awaiting", "resolved"),
    "KBConflictBatch": ("pending", "awaiting", "resolved"),
    "KBSourceRun": ("queued", "running", "success", "failed"),
    "KBSourceCrawlQueue": ("pending", "running", "done", "failed"),
    "KBStructuredImportJob": ("proposing", "proposed", "approved", "rejected", "failed"),
    "key_rotations": ("SUCCESS", "FAILED"),
}


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def upgrade() -> None:
    for name, column, comment in _AGENT_FKS:
        op.create_foreign_key(name, "Agents", "AgentVersions", [column], ["id"])
        op.execute(f'COMMENT ON COLUMN "Agents".{column} IS {_quote(comment)}')
    op.alter_column("KBWarehouse", "tenant_id", nullable=False)
    op.create_unique_constraint(
        "uq_kbtextblockversion_block_version", "KBTextBlockVersion", ["block_id", "version_number"]
    )
    for table, values in _STATUSES.items():
        allowed = ", ".join(_quote(v) for v in values)
        op.create_check_constraint(f"ck_{table.lower()}_status", table, f"status IN ({allowed})")


def downgrade() -> None:
    for table in reversed(list(_STATUSES)):
        op.drop_constraint(f"ck_{table.lower()}_status", table, type_="check")
    op.drop_constraint("uq_kbtextblockversion_block_version", "KBTextBlockVersion", type_="unique")
    op.alter_column("KBWarehouse", "tenant_id", nullable=True)
    for name, column, _comment in reversed(_AGENT_FKS):
        op.drop_constraint(name, "Agents", type_="foreignkey")
        op.execute(f'COMMENT ON COLUMN "Agents".{column} IS {_quote(_AGENT_FK_COMMENTS_BEFORE[column])}')
