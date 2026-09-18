"""source_v2_connectors

V2 of the scheduled-source registry: retry/backoff columns on "KBSourceRun",
a content-hash dedup column on "KBData", and "KBSourceCrawlQueue" for
recursive web_crawl link discovery. `rss`/`rest_api`/`sdmx` connector types
need no migration — `KBSource.connector_type` is a plain String(32), not a
Postgres enum.

Revision ID: b3c5d7e9f1a2
Revises: a2b4c6d8e0f1
Create Date: 2026-09-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b3c5d7e9f1a2"
down_revision: Union[str, None] = "a2b4c6d8e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "KBSourceRun",
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "KBSourceRun",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column("KBData", sa.Column("content_hash", sa.String(64), nullable=True))
    op.create_index("idx_kbdata_tenant_content_hash", "KBData", ["tenant_id", "content_hash"])

    op.create_table(
        "KBSourceCrawlQueue",
        sa.Column("queue_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("KBSource.source_id"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("Tenants.id"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discovered_from", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("inserted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_kbsourcecrawlqueue_source_id", "KBSourceCrawlQueue", ["source_id"])
    op.create_index("idx_kbsourcecrawlqueue_source_url", "KBSourceCrawlQueue", ["source_id", "url"], unique=True)
    op.create_index("idx_kbsourcecrawlqueue_tenant_status", "KBSourceCrawlQueue", ["tenant_id", "status"])
    op.create_index("idx_kbsourcecrawlqueue_inserted_at_desc", "KBSourceCrawlQueue", [sa.text("inserted_at DESC")])

    op.execute(
        """
        DO $$
        BEGIN
            GRANT SELECT, INSERT, UPDATE, DELETE ON "KBSourceCrawlQueue" TO app_user;
            -- Same gap as a2b4c6d8e0f1's KBSource grants: kb_backend is the
            -- table-scoped role kb-backend actually connects as in a real
            -- deploy, created by deploy/k8s/create-secrets.sh's
            -- grant-roles.sql, not by any migration — a fresh
            -- CI/testcontainers Postgres never has it.
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kb_backend') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON "KBSourceCrawlQueue" TO kb_backend;
            END IF;
            ALTER TABLE "KBSourceCrawlQueue" ENABLE ROW LEVEL SECURITY;
            ALTER TABLE "KBSourceCrawlQueue" FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON "KBSourceCrawlQueue"
                AS PERMISSIVE FOR ALL TO PUBLIC
                USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            DROP POLICY IF EXISTS tenant_isolation ON "KBSourceCrawlQueue";
            ALTER TABLE "KBSourceCrawlQueue" NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE "KBSourceCrawlQueue" DISABLE ROW LEVEL SECURITY;
        END
        $$;
        """
    )
    op.drop_table("KBSourceCrawlQueue")
    op.drop_index("idx_kbdata_tenant_content_hash", table_name="KBData")
    op.drop_column("KBData", "content_hash")
    op.drop_column("KBSourceRun", "next_retry_at")
    op.drop_column("KBSourceRun", "retry_count")
