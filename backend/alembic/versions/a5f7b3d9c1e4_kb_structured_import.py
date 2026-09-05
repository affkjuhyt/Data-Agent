"""kb structured import

Backs the opt-in "structured table" ingestion mode (see the Knowledge Base
Excel/CSV upload feature): a user uploading a spreadsheet can ask an LLM to
propose a normalized table schema instead of the default RAG/Qdrant chunk
path, review/edit it, and — only on explicit approval — get a real Postgres
table created from the file's rows. Nothing here changes the default path;
`KBData.ingestion_mode` defaults to 'document', which is the untouched,
existing behavior.

Three things, one migration since they're one feature landing together:
  1. `KBData.ingestion_mode` — a real column, not a key stuffed into the
     existing `metadata` JSONB (that column is a strictly-validated
     discriminated Pydantic union keyed by source_type and silently drops
     any key its model doesn't declare).
  2. `kb_structured` schema — dedicated namespace for every dynamically
     created structured table, kept separate from the CamelCase
     Alembic-managed application schema so there is zero collision risk.
  3. `KBStructuredImportJob` — the propose/review/approve audit trail.
     `KBTable` (created in ff9ae8dcd7f7_initial_schema, never written to by
     any code until now) is what a job's approval eventually populates, one
     row per finished table — no schema change needed there.

Revision ID: a5f7b3d9c1e4
Revises: d2e3f4a5b6c7
Create Date: 2026-09-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a5f7b3d9c1e4"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "KBData",
        sa.Column("ingestion_mode", sa.String(length=32), nullable=False, server_default="document"),
    )

    op.execute("CREATE SCHEMA IF NOT EXISTS kb_structured")

    op.create_table(
        "KBStructuredImportJob",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="proposing", nullable=False),
        sa.Column("llm_proposed_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("human_edited_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sample_rows", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_table_name", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("inserted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["KBData.data_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id"),
    )
    op.create_index("ix_KBStructuredImportJob_owner_id", "KBStructuredImportJob", ["owner_id"])
    op.create_index(
        "idx_kbstructuredimportjob_owner_created", "KBStructuredImportJob",
        ["owner_id", sa.literal_column("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_kbstructuredimportjob_owner_created", table_name="KBStructuredImportJob")
    op.drop_index("ix_KBStructuredImportJob_owner_id", table_name="KBStructuredImportJob")
    op.drop_table("KBStructuredImportJob")
    op.execute("DROP SCHEMA IF EXISTS kb_structured CASCADE")
    op.drop_column("KBData", "ingestion_mode")
