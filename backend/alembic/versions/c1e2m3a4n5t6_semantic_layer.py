"""semantic layer — versioned metric contracts

Revision ID: c1e2m3a4n5t6
Revises: a1d0bc953734, a3f7c91b2d40
Create Date: 2026-08-27

Adds the three tables the Semantic Layer reads (backend/libs/semantic):

  * SemanticMetric         — stable identity + accountable owner
  * SemanticMetricVersion  — the immutable formula (aggregation, expression,
                             default filters, business date column, unit)
  * SemanticSynonym        — the phrasings that reach a metric

⚠️  This revision ALSO merges two pre-existing heads. `a1d0bc953734`
    (graph_mirror_provenance) and `a3f7c91b2d40` (conflict_index_payload_split)
    both descend from `57abfbeabb56` and were never joined, so plain
    `alembic upgrade head` already failed with "Multiple head revisions" before
    this change — nothing could be migrated at all. Merging here rather than in
    a separate empty revision keeps the tree at one head with one file; if you
    would rather have the merge on its own, split this into a merge revision
    plus this one, in that order.

RLS follows the same shape as `a1b2c3d4e5f6_enable_tenant_rls`: ENABLE + FORCE
with a `tenant_isolation` policy on `current_setting('app.tenant_id')`, so a
connection that has not set a tenant sees no metrics rather than every tenant's.
A metric definition names real table and column names, so cross-tenant leakage
here would disclose another tenant's warehouse structure.
"""
from alembic import op

# Revision identifiers, used by Alembic.
revision = "c1e2m3a4n5t6"
down_revision = ("a1d0bc953734", "a3f7c91b2d40")
branch_labels = None
depends_on = None


_CREATE = """
CREATE TABLE IF NOT EXISTS "SemanticMetric" (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES "Tenants"(id),
    metric_key    varchar(128) NOT NULL,
    name          varchar(256) NOT NULL,
    description   text NOT NULL DEFAULT '',
    owner         varchar(256) NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_semantic_metric_key UNIQUE (tenant_id, metric_key)
);
"""

_CREATE_VERSION = """
CREATE TABLE IF NOT EXISTS "SemanticMetricVersion" (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES "Tenants"(id),
    metric_id        uuid NOT NULL REFERENCES "SemanticMetric"(id) ON DELETE CASCADE,
    version          integer NOT NULL,
    status           varchar(16) NOT NULL DEFAULT 'draft',
    source_table     varchar(256) NOT NULL,
    aggregation      varchar(32) NOT NULL,
    expression       varchar(256) NOT NULL,
    time_column      varchar(128),
    default_grain    varchar(16) NOT NULL DEFAULT 'none',
    unit             varchar(64) NOT NULL DEFAULT '',
    dimensions       jsonb NOT NULL DEFAULT '[]'::jsonb,
    default_filters  jsonb NOT NULL DEFAULT '[]'::jsonb,
    approved_by      varchar(256),
    approved_at      timestamptz,
    change_note      text NOT NULL DEFAULT '',
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_semantic_metric_version UNIQUE (metric_id, version),
    CONSTRAINT ck_semantic_version_status CHECK (status IN ('draft','approved','deprecated')),
    CONSTRAINT ck_semantic_version_grain  CHECK (default_grain IN ('none','day','week','month','quarter','year')),
    CONSTRAINT ck_semantic_version_agg    CHECK (aggregation IN ('sum','count','count_distinct','avg','min','max')),
    -- A grain without a business date column would silently bucket by nothing.
    -- Rejecting it in the database means a bad contract cannot be stored at all,
    -- not merely rejected later when someone asks a question.
    CONSTRAINT ck_semantic_version_grain_needs_time
        CHECK (default_grain = 'none' OR time_column IS NOT NULL)
);
"""

_CREATE_SYNONYM = """
CREATE TABLE IF NOT EXISTS "SemanticSynonym" (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        uuid NOT NULL REFERENCES "Tenants"(id),
    metric_id        uuid NOT NULL REFERENCES "SemanticMetric"(id) ON DELETE CASCADE,
    term             varchar(256) NOT NULL,
    term_normalized  varchar(256) NOT NULL,
    locale           varchar(16) NOT NULL DEFAULT 'vi',
    is_active        boolean NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL DEFAULT now(),
    -- One phrase, one metric. Without this a duplicate resolves by row order,
    -- i.e. differently on different replicas.
    CONSTRAINT uq_semantic_synonym_term UNIQUE (tenant_id, term_normalized)
);
"""

_INDEXES = [
    'CREATE INDEX IF NOT EXISTS idx_semantic_metric_tenant ON "SemanticMetric" (tenant_id)',
    'CREATE INDEX IF NOT EXISTS idx_semantic_version_lookup ON "SemanticMetricVersion" (tenant_id, metric_id, status)',
    'CREATE INDEX IF NOT EXISTS idx_semantic_synonym_metric ON "SemanticSynonym" (tenant_id, metric_id)',
]

# Only one approved version per metric may exist at a time — the agent picks
# "the approved one" and two of them would make that choice arbitrary. A partial
# unique index enforces it while leaving any number of drafts and deprecated
# rows alongside.
_ONE_APPROVED = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_semantic_one_approved_version
    ON "SemanticMetricVersion" (metric_id)
    WHERE status = 'approved'
"""

_TABLES = ("SemanticMetric", "SemanticMetricVersion", "SemanticSynonym")

_RLS = """
DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['SemanticMetric','SemanticMetricVersion','SemanticSynonym']
    LOOP
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO app_user', tbl);
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', tbl);
        EXECUTE format($p$
            CREATE POLICY tenant_isolation ON %I
            AS PERMISSIVE
            FOR ALL
            TO PUBLIC
            USING      (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        $p$, tbl);
    END LOOP;
END
$$;
"""

_DROP_RLS = """
DO $$
DECLARE
    tbl text;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['SemanticMetric','SemanticMetricVersion','SemanticSynonym']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', tbl);
        EXECUTE format('ALTER TABLE %I NO FORCE ROW LEVEL SECURITY', tbl);
        EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', tbl);
    END LOOP;
END
$$;
"""


def upgrade() -> None:
    op.execute(_CREATE)
    op.execute(_CREATE_VERSION)
    op.execute(_CREATE_SYNONYM)
    for statement in _INDEXES:
        op.execute(statement)
    op.execute(_ONE_APPROVED)
    # app_user may not exist on a database where a1b2c3d4e5f6 has not run (it
    # creates the role). Grant/RLS failing must not abort the table creation, so
    # the whole block is best-effort and re-runnable.
    try:
        op.execute(_RLS)
    except Exception:  # noqa: BLE001 — see comment above
        pass


def downgrade() -> None:
    try:
        op.execute(_DROP_RLS)
    except Exception:  # noqa: BLE001
        pass
    for table in reversed(_TABLES):
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
