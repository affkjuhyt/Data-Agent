"""Unified catalog as a view over tables, columns, documents, series and APIs.

catalog.asset is not a stored table. The generated DDL is the schema, and a new
table would fail the check that every table has a generated model. The view's
asset_id is the id the row already has, so a later join uses one key.
security_invoker keeps each caller's row security. A column is one current
column_meta row. An API is a source of kind rest_api. A series is an
external_series row.
"""

from alembic import op

revision = "v3_0014"
down_revision = "v3_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW catalog.asset
        WITH (security_invoker = true) AS
        SELECT
            dataset.id AS asset_id,
            dataset.org_id,
            dataset.data_space_id,
            CASE dataset.object_kind
                WHEN 'materialized_view' THEN 'view'
                WHEN 'view' THEN 'view'
                ELSE 'table'
            END AS kind,
            COALESCE(dataset.display_name, dataset.schema_name || '.' || dataset.table_name) AS name,
            dataset.source_id,
            dataset.updated_at AS refreshed_at
        FROM catalog.dataset AS dataset
        WHERE dataset.deleted_at IS NULL
        UNION ALL
        SELECT
            column_meta.id,
            column_meta.org_id,
            dataset.data_space_id,
            'column',
            COALESCE(
                column_meta.display_name,
                dataset.schema_name || '.' || dataset.table_name || '.' || column_meta.name
            ),
            dataset.source_id,
            column_meta.updated_at
        FROM catalog.column_meta AS column_meta
        JOIN catalog.dataset AS dataset ON dataset.id = column_meta.dataset_id
        WHERE column_meta.removed_in_version IS NULL
          AND column_meta.dataset_version = dataset.current_version
          AND dataset.deleted_at IS NULL
        UNION ALL
        SELECT
            document.id,
            document.org_id,
            collection.data_space_id,
            'document',
            COALESCE(document.title, document.file_name),
            document.document_source_id,
            document.updated_at
        FROM document.document AS document
        JOIN document.collection AS collection ON collection.id = document.collection_id
        WHERE document.deleted_at IS NULL
        UNION ALL
        SELECT
            series.id,
            series.org_id,
            NULL::uuid,
            'series',
            series.name,
            NULL::uuid,
            series.updated_at
        FROM skill.external_series AS series
        UNION ALL
        SELECT
            source.id,
            source.org_id,
            source.data_space_id,
            'api',
            source.name,
            source.id,
            source.updated_at
        FROM catalog.source AS source
        WHERE source.kind = 'rest_api'
          AND source.deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS catalog.asset")
