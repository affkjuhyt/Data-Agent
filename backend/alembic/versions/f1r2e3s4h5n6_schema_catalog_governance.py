"""Schema Catalog governance: lineage, owner, freshness, classification

Adds the fields an answer needs in order to carry an "as of" and a staleness
warning. Before this the catalog knew when the DDL changed (`last_altered`) and
when the crawler last looked (`last_crawled_at`) — neither of which is the age
of the data itself.

Every column is nullable with no default. That is the point: NULL means
"undeclared" or "never measured", which backend/libs/shared/freshness.py reports as
UNKNOWN rather than as fresh. Backfilling a default here would turn every
existing row into a false assurance.

Revision ID: f1r2e3s4h5n6
Revises: d1a2t3a4a5c6
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f1r2e3s4h5n6"
down_revision = "d1a2t3a4a5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("SchemaCatalog", sa.Column("owner", sa.String(length=255), nullable=True))
    op.add_column("SchemaCatalog", sa.Column("classification", sa.String(length=32), nullable=True))
    op.add_column("SchemaCatalog", sa.Column("upstream", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("SchemaCatalog", sa.Column("freshness_column", sa.String(length=128), nullable=True))
    op.add_column("SchemaCatalog", sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=True))
    op.add_column("SchemaCatalog", sa.Column("freshness_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("SchemaCatalog", sa.Column("freshness_sla_seconds", sa.Integer(), nullable=True))

    # Answering "which tables are past their freshness window?" is a governance
    # dashboard's first question and would otherwise scan the whole catalog.
    # Partial — rows with no SLA declared can never be stale, so they are dead
    # weight in this index.
    op.create_index(
        "idx_schemacatalog_freshness",
        "SchemaCatalog",
        ["tenant_id", "data_as_of"],
        postgresql_where=sa.text("freshness_sla_seconds IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_schemacatalog_freshness", table_name="SchemaCatalog")
    for column in (
        "freshness_sla_seconds",
        "freshness_checked_at",
        "data_as_of",
        "freshness_column",
        "upstream",
        "classification",
        "owner",
    ):
        op.drop_column("SchemaCatalog", column)
