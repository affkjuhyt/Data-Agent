-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  KB schema — kb_e2e stack                                           ║
-- ╚══════════════════════════════════════════════════════════════════════════╝
--
-- SCHEMA ONLY. Seed data lives in seed.sql (runs after this at Postgres init).
--
-- This is the SINGLE source of truth for the KB schema and MUST stay in lockstep
-- with the SQLAlchemy ORM in basemodel/services_databaseconnector/postgres_orm.
-- It mirrors the canonical ORM-generated schema exactly:
--   * mixed-case quoted identifiers ("KBData", not kbdata) — the code queries the
--     quoted names, so unquoted (lowercase-folded) tables are invisible to it.
--   * character-varying columns (NOT native pg ENUM types) — the ORM stores enums
--     as plain strings; init.sql used to declare pg enums, which the ORM never used.
--   * the created_at / inserted_at / is_deleted bookkeeping columns every table has.
--
-- Every statement is idempotent (CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT
-- EXISTS) so re-running on an already-initialised database is a no-op rather than
-- an error. The backend's Base.metadata.create_all sees these tables already exist
-- and skips them.
--
-- Tables are ordered by FK dependency: parents (Tenants, RolePermissions, KBModel,
-- KBKnowledgeBase) before children.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Tenants (FK parent for every tenant-scoped table) ─────────────────────────

CREATE TABLE IF NOT EXISTS "Tenants" (
    id             uuid PRIMARY KEY,
    name           varchar(255) NOT NULL,
    slug           varchar(100) NOT NULL UNIQUE,
    plan_id        smallint,
    data_residency varchar(20) NOT NULL,
    is_active      boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    deleted_at     timestamptz,
    inserted_at    timestamptz NOT NULL DEFAULT now()
);

-- ── RolePermissions (FK parent for KBData.role_id) ────────────────────────────

CREATE TABLE IF NOT EXISTS "RolePermissions" (
    id          uuid PRIMARY KEY,
    role_id     varchar(50) NOT NULL,
    resource    varchar(100) NOT NULL,
    action      varchar(50) NOT NULL,
    inserted_at timestamptz NOT NULL DEFAULT now()
);

-- ── KBModel ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBModel" (
    model_id    uuid PRIMARY KEY,
    model_name  varchar(255) NOT NULL,
    task_type   varchar(64) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    inserted_at timestamptz NOT NULL DEFAULT now(),
    is_deleted  boolean NOT NULL DEFAULT false
);

-- ── KBModelVersion ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBModelVersion" (
    version_id     serial PRIMARY KEY,
    model_id       uuid NOT NULL REFERENCES "KBModel"(model_id),
    version_number integer NOT NULL,
    added_by       varchar(255),
    config         jsonb,
    is_active      boolean NOT NULL DEFAULT false,
    added_on       timestamptz NOT NULL DEFAULT now(),
    created_at     timestamptz NOT NULL DEFAULT now(),
    inserted_at    timestamptz NOT NULL DEFAULT now(),
    is_deleted     boolean NOT NULL DEFAULT false
);

-- ── KBKnowledgeBase ─────────────────────────────────────────────────────────--

CREATE TABLE IF NOT EXISTS "KBKnowledgeBase" (
    knowledge_base_id uuid PRIMARY KEY,
    tenant_id         uuid NOT NULL REFERENCES "Tenants"(id),
    name              varchar(255) NOT NULL,
    description       text,
    is_active         boolean NOT NULL DEFAULT true,
    created_by        varchar(255),
    created_at        timestamptz NOT NULL DEFAULT now(),
    inserted_at       timestamptz NOT NULL DEFAULT now(),
    is_deleted        boolean NOT NULL DEFAULT false
);

-- ── KBData ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBData" (
    data_id           uuid PRIMARY KEY,
    tenant_id         uuid NOT NULL REFERENCES "Tenants"(id),
    role_id           uuid NOT NULL REFERENCES "RolePermissions"(id),
    knowledge_base_id uuid REFERENCES "KBKnowledgeBase"(knowledge_base_id),
    name              varchar(512) NOT NULL,
    extension         varchar(32) NOT NULL,
    language          varchar(32) NOT NULL,
    source_type       varchar(32) NOT NULL,
    added_by          varchar(255) NOT NULL,
    abstract          text NOT NULL,
    metadata          jsonb NOT NULL,
    current_tier      varchar(16) NOT NULL,
    path              varchar(1024),
    added_on          timestamptz NOT NULL DEFAULT now(),
    created_at        timestamptz NOT NULL DEFAULT now(),
    inserted_at       timestamptz NOT NULL DEFAULT now(),
    is_deleted        boolean NOT NULL DEFAULT false
);

-- ── KBLifecycleHistory ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBLifecycleHistory" (
    history_id        uuid PRIMARY KEY,
    data_id           uuid NOT NULL REFERENCES "KBData"(data_id),
    to_tier           varchar(16) NOT NULL,
    from_tier         varchar(16),
    approved_by       varchar(255),
    notes             text,
    pipeline_metadata jsonb,
    transitioned_at   timestamptz NOT NULL DEFAULT now(),
    created_at        timestamptz NOT NULL DEFAULT now(),
    inserted_at       timestamptz NOT NULL DEFAULT now(),
    is_deleted        boolean NOT NULL DEFAULT false
);

-- ── KBFilterPolicy ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBFilterPolicy" (
    policy_id    uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES "Tenants"(id),
    policy_name  varchar(255) NOT NULL,
    configformat varchar(64) NOT NULL,
    config       jsonb,
    is_active    boolean NOT NULL DEFAULT false,
    created_by   varchar(255),
    language     varchar(32),
    created_at   timestamptz NOT NULL DEFAULT now(),
    inserted_at  timestamptz NOT NULL DEFAULT now(),
    is_deleted   boolean NOT NULL DEFAULT false
);

-- ── KBExtractionPolicy ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBExtractionPolicy" (
    policy_id       uuid PRIMARY KEY,
    tenant_id       uuid NOT NULL REFERENCES "Tenants"(id),
    policy_name     varchar(255) NOT NULL,
    policy_type     varchar(64) NOT NULL,
    custom_override text,
    created_by      varchar(255),
    language        varchar(32),
    created_at      timestamptz NOT NULL DEFAULT now(),
    inserted_at     timestamptz NOT NULL DEFAULT now(),
    is_deleted      boolean NOT NULL DEFAULT false
);

-- ── KBConflictBatch ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBConflictBatch" (
    batch_id          uuid PRIMARY KEY,
    tenant_id         uuid REFERENCES "Tenants"(id),
    -- The one text block this batch is anchored on; every conflict in it targets this block.
    existing_block_id uuid,
    batch_title       varchar(512) NOT NULL,
    status            varchar(32) NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    inserted_at       timestamptz NOT NULL DEFAULT now(),
    is_deleted        boolean NOT NULL DEFAULT false
);

-- One open batch per block: two chunks conflicting with the same block concurrently
-- must join the same batch, not race into two.
CREATE UNIQUE INDEX IF NOT EXISTS "idx_kbconflictbatch_open_block"
    ON "KBConflictBatch" (tenant_id, existing_block_id)
    WHERE status <> 'resolved' AND is_deleted = false;

-- ── KBConflict ────────────────────────────────────────────────────────────────

-- The queryable index only. The incoming chunk's content/embedding/entities live in
-- Mongo (kb_conflict_payload), keyed by (tenant_id, conflict_id).
CREATE TABLE IF NOT EXISTS "KBConflict" (
    conflict_id            uuid PRIMARY KEY,
    tenant_id              uuid REFERENCES "Tenants"(id),
    -- The document whose chunk raised this conflict; scopes re-promotion cleanup.
    data_id                uuid REFERENCES "KBData"(data_id),
    conflict_type          varchar(64) NOT NULL,
    severity               varchar(16) NOT NULL,
    batch_id               uuid REFERENCES "KBConflictBatch"(batch_id),
    status                 varchar(32) NOT NULL,
    detailed_explanation   text,
    -- Set when a merge re-check reopened this conflict, so a re-approved merge forces through.
    from_recheck           boolean NOT NULL DEFAULT false,
    resolution_instruction text,
    resolution_method      varchar(20),
    resolved_by            varchar(255),
    resolved_at            timestamptz,
    detected_at            timestamptz NOT NULL DEFAULT now(),
    created_at             timestamptz NOT NULL DEFAULT now(),
    inserted_at            timestamptz NOT NULL DEFAULT now(),
    is_deleted             boolean NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS "idx_kbconflict_data_id" ON "KBConflict" (data_id);

-- ── KBWarehouse ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBWarehouse" (
    warehouse_id uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES "Tenants"(id),
    service      varchar(255) NOT NULL,
    description  text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    inserted_at  timestamptz NOT NULL DEFAULT now(),
    is_deleted   boolean NOT NULL DEFAULT false
);

-- ── KBWarehouseConfig ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBWarehouseConfig" (
    config_id      uuid PRIMARY KEY,
    warehouse_id   uuid NOT NULL REFERENCES "KBWarehouse"(warehouse_id) ON DELETE CASCADE,
    version_number integer NOT NULL,
    is_active      boolean NOT NULL DEFAULT false,
    config         jsonb,
    created_by     varchar(255),
    created_at     timestamptz NOT NULL DEFAULT now(),
    inserted_at    timestamptz NOT NULL DEFAULT now(),
    is_deleted     boolean NOT NULL DEFAULT false
);

-- ── KBTable ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBTable" (
    table_id    uuid PRIMARY KEY,
    owner_id    uuid NOT NULL REFERENCES "KBData"(data_id) ON DELETE CASCADE,
    table_name  varchar(255) NOT NULL,
    description text,
    schema      jsonb,
    created_by  varchar(255),
    created_on  timestamptz NOT NULL DEFAULT now(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    inserted_at timestamptz NOT NULL DEFAULT now(),
    is_deleted  boolean NOT NULL DEFAULT false
);

-- ── KBTextBlock ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBTextBlock" (
    block_id    uuid PRIMARY KEY,
    owner_id    uuid NOT NULL REFERENCES "KBData"(data_id) ON DELETE CASCADE,
    block_index integer NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    inserted_at timestamptz NOT NULL DEFAULT now(),
    is_deleted  boolean NOT NULL DEFAULT false
);

-- ── KBTextBlockVersion ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBTextBlockVersion" (
    version_id         uuid PRIMARY KEY,
    block_id           uuid NOT NULL REFERENCES "KBTextBlock"(block_id) ON DELETE CASCADE,
    version_number     integer NOT NULL,
    content            text,
    created_by         varchar(255),
    table_involved     boolean,
    embedding_model_id uuid REFERENCES "KBModel"(model_id),
    payload            jsonb,
    is_active          boolean,
    created_at         timestamptz NOT NULL DEFAULT now(),
    inserted_at        timestamptz NOT NULL DEFAULT now(),
    is_deleted         boolean NOT NULL DEFAULT false
);

-- ── KBTextTable ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBTextTable" (
    version_id  uuid PRIMARY KEY REFERENCES "KBTextBlockVersion"(version_id) ON DELETE CASCADE,
    table_name  varchar(255) NOT NULL,
    description text,
    data        jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    inserted_at timestamptz NOT NULL DEFAULT now(),
    is_deleted  boolean NOT NULL DEFAULT false
);

-- ── KBQdrantConnection ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBQdrantConnection" (
    connection_id    uuid PRIMARY KEY,
    tenant_id        uuid NOT NULL REFERENCES "Tenants"(id),
    is_active        boolean NOT NULL DEFAULT false,
    total_collection integer NOT NULL DEFAULT 0,
    created_at       timestamptz NOT NULL DEFAULT now(),
    inserted_at      timestamptz NOT NULL DEFAULT now(),
    is_deleted       boolean NOT NULL DEFAULT false
);

-- ── KBQdrantCollection ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBQdrantCollection" (
    collection_id      uuid PRIMARY KEY,
    connection_id      uuid NOT NULL REFERENCES "KBQdrantConnection"(connection_id),
    collection_name    varchar(255) NOT NULL,
    is_active          boolean NOT NULL DEFAULT false,
    similarity_metric  varchar(16) NOT NULL,
    points_count       integer NOT NULL DEFAULT 0,
    vector_dimension   integer,
    embedding_model_id uuid REFERENCES "KBModel"(model_id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    inserted_at        timestamptz NOT NULL DEFAULT now(),
    is_deleted         boolean NOT NULL DEFAULT false
);

-- ── KBNeo4jConnection ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBNeo4jConnection" (
    connection_id      uuid PRIMARY KEY,
    tenant_id          uuid NOT NULL REFERENCES "Tenants"(id),
    is_connected       boolean NOT NULL DEFAULT false,
    total_node         integer NOT NULL DEFAULT 0,
    total_edge         integer NOT NULL DEFAULT 0,
    embedding_model_id uuid REFERENCES "KBModel"(model_id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    inserted_at        timestamptz NOT NULL DEFAULT now(),
    is_deleted         boolean NOT NULL DEFAULT false
);

-- ── KBNeo4jNode ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBNeo4jNode" (
    node_id          uuid PRIMARY KEY,
    connection_id    uuid REFERENCES "KBNeo4jConnection"(connection_id),
    tenant_id        uuid,
    knowledge_base   varchar(512),
    node_name        varchar(512) NOT NULL,
    node_description text,
    inserted_at      timestamptz NOT NULL DEFAULT now(),
    created_at       timestamptz NOT NULL DEFAULT now(),
    is_deleted       boolean NOT NULL DEFAULT false
);

-- ── KBNeo4jRelationship ───────────────────────────────────────────────────────
-- One row per (entity pair, chunk) — block_id is the provenance. An edge exists
-- while >=1 row references it; its corroboration count is COUNT(*) over the pair.

CREATE TABLE IF NOT EXISTS "KBNeo4jRelationship" (
    from_node      uuid NOT NULL REFERENCES "KBNeo4jNode"(node_id),
    to_node        uuid NOT NULL REFERENCES "KBNeo4jNode"(node_id),
    block_id       uuid NOT NULL,
    data_id        uuid,
    tenant_id      uuid,
    knowledge_base varchar(512),
    score          double precision,
    description    text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    inserted_at    timestamptz NOT NULL DEFAULT now(),
    is_deleted     boolean NOT NULL DEFAULT false,
    PRIMARY KEY (from_node, to_node, block_id)
);

-- ── KBEntityLookup ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBEntityLookup" (
    lookup_id      uuid PRIMARY KEY,
    tenant_id      uuid REFERENCES "Tenants"(id),
    alias_name     varchar(512) NOT NULL,
    canonical_name varchar(512) NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    inserted_at    timestamptz NOT NULL DEFAULT now(),
    is_deleted     boolean NOT NULL DEFAULT false
);

-- ── KBPublishAPI ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBPublishAPI" (
    id           uuid PRIMARY KEY,
    tenant_id    uuid NOT NULL REFERENCES "Tenants"(id),
    name         varchar(255) NOT NULL,
    type         varchar(32) NOT NULL,
    endpoint_url varchar(1024) NOT NULL,
    http_method  varchar(8) NOT NULL,
    is_published boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    inserted_at  timestamptz NOT NULL DEFAULT now(),
    is_deleted   boolean NOT NULL DEFAULT false
);

-- ── Indexes (mirror the ORM; cursor-pagination keyset + tenant filters) ───────

CREATE INDEX IF NOT EXISTS idx_kbdata_keyset            ON "KBData" (tenant_id, current_tier, inserted_at, data_id);
CREATE INDEX IF NOT EXISTS idx_kbdata_keyset_all        ON "KBData" (tenant_id, inserted_at, data_id);
CREATE INDEX IF NOT EXISTS idx_kbdata_tenant_role       ON "KBData" (tenant_id, role_id);
CREATE INDEX IF NOT EXISTS idx_kbdata_tenant_source     ON "KBData" (tenant_id, source_type);
CREATE INDEX IF NOT EXISTS idx_kbdata_tenant_tier       ON "KBData" (tenant_id, current_tier);
CREATE INDEX IF NOT EXISTS idx_kbdata_inserted_at_desc  ON "KBData" (inserted_at DESC);

-- Document name is unique PER TENANT, not globally — two tenants may each have a
-- document with the same name.
CREATE UNIQUE INDEX IF NOT EXISTS idx_kbdata_tenant_name          ON "KBData" (tenant_id, name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kbknowledgebase_tenant_name ON "KBKnowledgeBase" (tenant_id, name);

CREATE INDEX IF NOT EXISTS idx_kbconflict_tenant_status_severity ON "KBConflict" (tenant_id, status, severity);
CREATE INDEX IF NOT EXISTS idx_kbconflict_inserted_at_desc       ON "KBConflict" (inserted_at DESC);
CREATE INDEX IF NOT EXISTS idx_kbconflictbatch_inserted_at_desc  ON "KBConflictBatch" (inserted_at DESC);

CREATE INDEX IF NOT EXISTS idx_kbfilterpolicy_tenant_lang     ON "KBFilterPolicy" (tenant_id, language);
CREATE INDEX IF NOT EXISTS idx_kbextractionpolicy_tenant_lang ON "KBExtractionPolicy" (tenant_id, language);
CREATE INDEX IF NOT EXISTS idx_kbwarehouse_tenant            ON "KBWarehouse" (tenant_id);

CREATE INDEX IF NOT EXISTS idx_kbtextblock_owner_block          ON "KBTextBlock" (owner_id, block_id);
CREATE INDEX IF NOT EXISTS idx_kbtextblockversion_block_version ON "KBTextBlockVersion" (block_id, version_number) WHERE (is_active = true);

CREATE INDEX IF NOT EXISTS idx_kbqdrantconn_tenant_active  ON "KBQdrantConnection" (tenant_id) WHERE (is_active = true);
CREATE INDEX IF NOT EXISTS idx_kbneo4jconn_tenant_connected ON "KBNeo4jConnection" (tenant_id) WHERE (is_connected = true);
CREATE INDEX IF NOT EXISTS idx_kbneo4jnode_tenant_kb       ON "KBNeo4jNode" (tenant_id, knowledge_base);
CREATE INDEX IF NOT EXISTS idx_kbneo4jrelationship_pair    ON "KBNeo4jRelationship" (from_node, to_node);
CREATE INDEX IF NOT EXISTS idx_kbneo4jrelationship_block   ON "KBNeo4jRelationship" (block_id);
CREATE INDEX IF NOT EXISTS idx_kbneo4jrelationship_data    ON "KBNeo4jRelationship" (data_id);
CREATE INDEX IF NOT EXISTS idx_kbneo4jrelationship_tenkb   ON "KBNeo4jRelationship" (tenant_id, knowledge_base);

CREATE INDEX IF NOT EXISTS idx_kbentitylookup_alias        ON "KBEntityLookup" USING hash (alias_name);
CREATE INDEX IF NOT EXISTS idx_kbentitylookup_tenant_alias ON "KBEntityLookup" (tenant_id, alias_name);

CREATE INDEX IF NOT EXISTS idx_kbpublishapi_tenant_published ON "KBPublishAPI" (tenant_id) WHERE (is_published = true);
