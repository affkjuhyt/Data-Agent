"""kbsource_registry

Adds the scheduled-source registry (V1: http_file / web_crawl /
web_crawl_headless connectors) — "KBSource" (identity) + "KBSourceConfig"
(versioned config, mirrors KBWarehouseConfig) + "KBSourceRun" (crawl run
history, read by the new platform_workers "web-crawler" worker to decide
what's due and by the Source Registry UI for run history).

Revision ID: a2b4c6d8e0f1
Revises: c7d8e9f0a1b2
Create Date: 2026-09-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "a2b4c6d8e0f1"
down_revision: Union[str, None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "KBSource",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("Tenants.id"), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("RolePermissions.id"), nullable=False),
        sa.Column("connector_type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("publisher", sa.String(255), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("inserted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_index("idx_kbsource_tenant", "KBSource", ["tenant_id"])
    op.create_index("idx_kbsource_inserted_at_desc", "KBSource", [sa.text("inserted_at DESC")])

    op.create_table(
        "KBSourceConfig",
        sa.Column("config_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("KBSource.source_id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("inserted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("row_version", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_kbsourceconfig_source_version", "KBSourceConfig", ["source_id", "version_number"])
    op.create_index(
        "idx_kbsourceconfig_source_active", "KBSourceConfig", ["source_id"],
        postgresql_where=sa.text("is_active = true"),
    )

    op.create_table(
        "KBSourceRun",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("KBSource.source_id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("data_ids", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("quality_metrics", postgresql.JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("inserted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_kbsourcerun_source_started_desc", "KBSourceRun", ["source_id", sa.text("started_at DESC")])

    op.execute(
        """
        DO $$
        BEGIN
            GRANT SELECT, INSERT, UPDATE, DELETE ON "KBSource", "KBSourceConfig", "KBSourceRun" TO app_user;
            ALTER TABLE "KBSource" ENABLE ROW LEVEL SECURITY;
            ALTER TABLE "KBSource" FORCE ROW LEVEL SECURITY;
            CREATE POLICY tenant_isolation ON "KBSource"
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
            DROP POLICY IF EXISTS tenant_isolation ON "KBSource";
            ALTER TABLE "KBSource" NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE "KBSource" DISABLE ROW LEVEL SECURITY;
        END
        $$;
        """
    )
    op.drop_table("KBSourceRun")
    op.drop_table("KBSourceConfig")
    op.drop_table("KBSource")
