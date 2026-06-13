CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Enums ─────────────────────────────────────────────────────────────────────

CREATE TYPE KBLanguage          AS ENUM ('english', 'vietnamese');
CREATE TYPE KBSourceType        AS ENUM ('doc', 'web', 'image', 'video', 'warehouse');
CREATE TYPE KBTier              AS ENUM ('bronze', 'silver', 'gold');
CREATE TYPE KBPolicyFormat      AS ENUM ('natural_language', 'exact_word');
CREATE TYPE KBPolicyType        AS ENUM ('entity_node', 'relationship_edge');
CREATE TYPE KBConflictType      AS ENUM ('content_contradiction','content_conflict','content_duplicate','content_update','table_schema');
CREATE TYPE KBConflictSeverity  AS ENUM ('low', 'medium', 'high');
CREATE TYPE KBConflictStatus    AS ENUM ('pending', 'awaiting', 'resolved');
CREATE TYPE KBConflictResolution AS ENUM ('keep_existing', 'keep_incoming', 'merge', 'no_action');
CREATE TYPE KBSimilarityMetric  AS ENUM ('cosine', 'euclidean', 'dot');
CREATE TYPE KBHttpMethod        AS ENUM ('GET', 'POST', 'PUT', 'PATCH', 'DELETE');
CREATE TYPE KBAPIType           AS ENUM ('NEO4J', 'QDRANT', 'RETRIEVE');

-- ── KBModel ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBModel" (
    model_id    UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_name  VARCHAR(255) NOT NULL,
    task_type   VARCHAR(64)  NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted  BOOL         NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_kbmodel_name_task UNIQUE (model_name, task_type)
);
CREATE INDEX IF NOT EXISTS idx_kbmodel_task_type        ON "KBModel" (task_type);
CREATE INDEX IF NOT EXISTS idx_kbmodel_inserted_at_desc ON "KBModel" (inserted_at DESC);

-- ── KBModelVersion ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBModelVersion" (
    version_id     SERIAL       PRIMARY KEY,
    model_id       UUID         REFERENCES "KBModel"(model_id),
    version_number INT,
    added_on       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    added_by       VARCHAR(255),
    config         JSONB,
    is_active      BOOL         NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted     BOOL         NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_modelversion_active         ON "KBModelVersion" (model_id, version_id) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_kbmodelversion_inserted_at  ON "KBModelVersion" (inserted_at DESC);

-- ── Seed: two embedding model variants ────────────────────────────────────────
-- bge-large: high-quality 1024-dim; bge-small: lightweight 384-dim.
-- The model-service registers an active KBModelVersion on startup for whichever
-- variant it is actually running (controlled by the EMBED_MODEL env var).

INSERT INTO "KBModel" (model_id, model_name, task_type) VALUES
    ('00000000-0000-0000-0000-000000000001', 'BAAI/bge-large-en-v1.5', 'embedding'),
    ('00000000-0000-0000-0000-000000000002', 'BAAI/bge-small-en-v1.5', 'embedding')
ON CONFLICT (model_name, task_type) DO NOTHING;

INSERT INTO "KBModelVersion" (model_id, version_number, is_active, config, added_on) VALUES
    ('00000000-0000-0000-0000-000000000001', 1, false,
     '{"vector_dim": 1024, "normalize": true}', NOW()),
    ('00000000-0000-0000-0000-000000000002', 1, false,
     '{"vector_dim": 384,  "normalize": true}', NOW())
ON CONFLICT DO NOTHING;

-- ── KBKnowledgeBase ───────────────────────────────────────────────────────────
-- The "kind" of knowledge a document belongs to. A tenant has many knowledge bases.

CREATE TABLE IF NOT EXISTS "KBKnowledgeBase" (
    knowledge_base_id UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id         UUID         NOT NULL,
    name              VARCHAR(255) NOT NULL,
    description       TEXT,
    is_active         BOOL         NOT NULL DEFAULT TRUE,
    created_by        VARCHAR(255),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted        BOOL         NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_kbknowledgebase_tenant_name UNIQUE (tenant_id, name)
);
CREATE INDEX IF NOT EXISTS idx_kbknowledgebase_tenant      ON "KBKnowledgeBase" (tenant_id);
CREATE INDEX IF NOT EXISTS idx_kbknowledgebase_inserted_at ON "KBKnowledgeBase" (inserted_at DESC);


-- ── KBData ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBData" (
    data_id           UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id         UUID         NOT NULL,
    role_id           UUID         NOT NULL,
    knowledge_base_id UUID         REFERENCES "KBKnowledgeBase"(knowledge_base_id),
    name         VARCHAR(512) NOT NULL,
    extension    VARCHAR(32)  NOT NULL DEFAULT '',
    language     VARCHAR(32)  NOT NULL DEFAULT 'english',
    source_type  VARCHAR(32)  NOT NULL,
    current_tier VARCHAR(16)  NOT NULL DEFAULT 'bronze',
    added_on     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    added_by     VARCHAR(255) NOT NULL DEFAULT 'system',
    abstract     TEXT         NOT NULL DEFAULT '',
    metadata     JSONB        NOT NULL DEFAULT '{}',
    path         TEXT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted   BOOL         NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_kbdata_name UNIQUE (name)
);
CREATE INDEX IF NOT EXISTS idx_data_tenant_source    ON "KBData" (tenant_id, source_type);
CREATE INDEX IF NOT EXISTS idx_data_tenant_role      ON "KBData" (tenant_id, role_id);
CREATE INDEX IF NOT EXISTS idx_data_knowledge_base   ON "KBData" (knowledge_base_id);
CREATE INDEX IF NOT EXISTS idx_data_tenant_tier      ON "KBData" (tenant_id, current_tier);
CREATE INDEX IF NOT EXISTS idx_kbdata_inserted_at    ON "KBData" (inserted_at DESC);
CREATE INDEX IF NOT EXISTS idx_kbdata_tenant_id      ON "KBData" (tenant_id);

-- ── KBLifecycleHistory ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBLifecycleHistory" (
    history_id         UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    data_id            UUID         NOT NULL REFERENCES "KBData"(data_id),
    from_tier          VARCHAR(16),
    to_tier            VARCHAR(16)  NOT NULL,
    transitioned_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    approved_by        VARCHAR(255),
    notes              TEXT,
    pipeline_metadata  JSONB,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted         BOOL         NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_lifecycle_data_id        ON "KBLifecycleHistory" (data_id);
CREATE INDEX IF NOT EXISTS idx_lifecycle_inserted_at    ON "KBLifecycleHistory" (inserted_at DESC);

-- ── KBFilterPolicy ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBFilterPolicy" (
    policy_id    UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id    UUID         NOT NULL,
    policy_name  VARCHAR(255) NOT NULL,
    configformat VARCHAR(64)  NOT NULL,
    config       JSONB,
    is_active    BOOL         NOT NULL DEFAULT FALSE,
    created_by   VARCHAR(255),
    language     VARCHAR(32),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted   BOOL         NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_filterpolicy_tenant_lang   ON "KBFilterPolicy" (tenant_id, language);
CREATE INDEX IF NOT EXISTS idx_filterpolicy_inserted_at   ON "KBFilterPolicy" (inserted_at DESC);

-- ── KBExtractionPolicy ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBExtractionPolicy" (
    policy_id       UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID         NOT NULL,
    policy_name     VARCHAR(255) NOT NULL,
    policy_type     VARCHAR(64)  NOT NULL,
    custom_override TEXT,
    created_by      VARCHAR(255),
    language        VARCHAR(32),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted      BOOL         NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_extractionpolicy_tenant_lang ON "KBExtractionPolicy" (tenant_id, language);
CREATE INDEX IF NOT EXISTS idx_extractionpolicy_inserted_at ON "KBExtractionPolicy" (inserted_at DESC);

-- ── KBConflictBatch ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBConflictBatch" (
    batch_id    UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID,
    batch_title VARCHAR(512) NOT NULL,
    status      VARCHAR(32)  NOT NULL DEFAULT 'pending',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted  BOOL         NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_conflictbatch_tenant_status ON "KBConflictBatch" (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_conflictbatch_inserted_at   ON "KBConflictBatch" (inserted_at DESC);

-- ── KBConflict ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBConflict" (
    conflict_id            UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id              UUID,
    batch_id               UUID         REFERENCES "KBConflictBatch"(batch_id),
    conflict_type          VARCHAR(64)  NOT NULL,
    detected_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    severity               VARCHAR(16)  NOT NULL,
    status                 VARCHAR(32)  NOT NULL DEFAULT 'pending',
    detailed_explanation   TEXT,
    existing_snapshot      JSONB,
    incoming_snapshot      JSONB,
    resolution_instruction TEXT,
    resolution_method      VARCHAR(20),
    resolved_at            TIMESTAMPTZ,
    resolved_by            VARCHAR(255),
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted             BOOL         NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_conflict_batch_pending            ON "KBConflict" (batch_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_conflict_tenant_status_severity   ON "KBConflict" (tenant_id, status, severity);
CREATE INDEX IF NOT EXISTS idx_conflict_inserted_at              ON "KBConflict" (inserted_at DESC);

-- ── KBWarehouse ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBWarehouse" (
    warehouse_id UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    service      VARCHAR(255) NOT NULL,
    description  TEXT,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted   BOOL         NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_kbwarehouse_inserted_at ON "KBWarehouse" (inserted_at DESC);

-- ── KBWarehouseConfig ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBWarehouseConfig" (
    config_id      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    warehouse_id   UUID         NOT NULL REFERENCES "KBWarehouse"(warehouse_id),
    version_number INT          NOT NULL,
    is_active      BOOL         NOT NULL DEFAULT FALSE,
    config         JSONB,
    created_by     VARCHAR(255),
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted     BOOL         NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_warehouseconfig_version    ON "KBWarehouseConfig" (warehouse_id, version_number);
CREATE INDEX IF NOT EXISTS idx_warehouseconfig_active     ON "KBWarehouseConfig" (warehouse_id) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_warehouseconfig_inserted   ON "KBWarehouseConfig" (inserted_at DESC);

-- ── KBTable ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBTable" (
    table_id    UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_id    UUID         REFERENCES "KBData"(data_id),
    table_name  VARCHAR(255),
    description TEXT,
    schema      JSONB,
    created_by  VARCHAR(255),
    created_on  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted  BOOL         NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_table_name     ON "KBTable" (table_name);
CREATE INDEX IF NOT EXISTS idx_table_owner_id ON "KBTable" (owner_id);

-- ── KBTextBlock ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBTextBlock" (
    block_id    UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_id    UUID        REFERENCES "KBData"(data_id),
    block_index INT         NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_deleted  BOOL        NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_textblock_owner        ON "KBTextBlock" (owner_id, block_id);
CREATE INDEX IF NOT EXISTS idx_textblock_inserted_at  ON "KBTextBlock" (inserted_at DESC);

-- ── KBTextBlockVersion ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBTextBlockVersion" (
    version_id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    block_id           UUID        NOT NULL REFERENCES "KBTextBlock"(block_id) ON DELETE CASCADE,
    version_number     INT         NOT NULL,
    content            TEXT,
    created_by         VARCHAR(255),
    is_active          BOOL        DEFAULT FALSE,
    table_involved     BOOL,
    embedding_model_id UUID        REFERENCES "KBModel"(model_id),
    payload            JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    inserted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_deleted         BOOL        NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_textblockversion_active      ON "KBTextBlockVersion" (block_id, version_number) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_textblockversion_inserted_at ON "KBTextBlockVersion" (inserted_at DESC);

-- ── KBTextTable ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBTextTable" (
    version_id  UUID         PRIMARY KEY REFERENCES "KBTextBlockVersion"(version_id) ON DELETE CASCADE,
    table_name  VARCHAR(255) NOT NULL,
    description TEXT,
    data        JSONB,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted  BOOL         NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_kbtexttable_inserted_at ON "KBTextTable" (inserted_at DESC);

-- ── KBQdrantConnection ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBQdrantConnection" (
    connection_id    UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id        UUID        NOT NULL,
    is_active        BOOL        NOT NULL DEFAULT FALSE,
    total_collection INT         NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    inserted_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_deleted       BOOL        NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_qdrantconn_active       ON "KBQdrantConnection" (tenant_id) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_qdrantconn_inserted_at  ON "KBQdrantConnection" (inserted_at DESC);

-- ── KBQdrantCollection ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBQdrantCollection" (
    collection_id      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    connection_id      UUID         NOT NULL REFERENCES "KBQdrantConnection"(connection_id),
    collection_name    VARCHAR(255) NOT NULL,
    is_active          BOOL         NOT NULL DEFAULT FALSE,
    similarity_metric  VARCHAR(16)  NOT NULL DEFAULT 'cosine',
    points_count       INT          NOT NULL DEFAULT 0,
    vector_dimension   INT,
    embedding_model_id UUID         REFERENCES "KBModel"(model_id),
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted         BOOL         NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_qdrantcol_connection   ON "KBQdrantCollection" (connection_id);
CREATE INDEX IF NOT EXISTS idx_qdrantcol_inserted_at  ON "KBQdrantCollection" (inserted_at DESC);

-- ── KBNeo4jConnection ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBNeo4jConnection" (
    connection_id      UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id          UUID        NOT NULL,
    is_connected       BOOL        NOT NULL DEFAULT FALSE,
    total_node         INT         NOT NULL DEFAULT 0,
    total_edge         INT         NOT NULL DEFAULT 0,
    embedding_model_id UUID        REFERENCES "KBModel"(model_id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    inserted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_deleted         BOOL        NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_neo4jconn_active      ON "KBNeo4jConnection" (tenant_id) WHERE is_connected = TRUE;
CREATE INDEX IF NOT EXISTS idx_neo4jconn_inserted_at ON "KBNeo4jConnection" (inserted_at DESC);

-- ── KBNeo4jNode ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBNeo4jNode" (
    node_id          UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    connection_id    UUID         REFERENCES "KBNeo4jConnection"(connection_id),
    node_name        VARCHAR(512) NOT NULL,
    node_description TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted       BOOL         NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_neo4jnode_connection  ON "KBNeo4jNode" (connection_id);
CREATE INDEX IF NOT EXISTS idx_neo4jnode_inserted_at ON "KBNeo4jNode" (inserted_at DESC);

-- ── KBNeo4jRelationship ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBNeo4jRelationship" (
    from_node   UUID  REFERENCES "KBNeo4jNode"(node_id),
    to_node     UUID  REFERENCES "KBNeo4jNode"(node_id),
    score       FLOAT,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_deleted  BOOL        NOT NULL DEFAULT FALSE,
    PRIMARY KEY (from_node, to_node)
);
CREATE INDEX IF NOT EXISTS idx_neo4jrel_inserted_at ON "KBNeo4jRelationship" (inserted_at DESC);

-- ── KBEntityLookup ────────────────────────────────────────────────────────────

-- alias_name is stored in canonical form (lowercase, underscores, Vietnamese
-- diacritics preserved). tenant_id NULL = global seed alias shared by all tenants.
CREATE TABLE IF NOT EXISTS "KBEntityLookup" (
    lookup_id      UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id      UUID,
    alias_name     VARCHAR(512) NOT NULL,
    canonical_name VARCHAR(512) NOT NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    inserted_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_deleted     BOOL         NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_entitylookup_alias        ON "KBEntityLookup" USING HASH (alias_name);
CREATE INDEX IF NOT EXISTS idx_entitylookup_tenant_alias ON "KBEntityLookup" (tenant_id, alias_name);
CREATE INDEX IF NOT EXISTS idx_entitylookup_canonical    ON "KBEntityLookup" (canonical_name);
CREATE INDEX IF NOT EXISTS idx_entitylookup_inserted_at  ON "KBEntityLookup" (inserted_at DESC);

-- ── KBPublishAPI ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS "KBPublishAPI" (
    id           UUID          PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id    UUID          NOT NULL,
    name         VARCHAR(255)  NOT NULL,
    type         VARCHAR(32)   NOT NULL,
    endpoint_url VARCHAR(1024) NOT NULL,
    http_method  VARCHAR(8)    NOT NULL,
    is_published BOOL          NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    inserted_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    is_deleted   BOOL          NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_publishapi_active      ON "KBPublishAPI" (tenant_id) WHERE is_published = TRUE;
CREATE INDEX IF NOT EXISTS idx_publishapi_inserted_at ON "KBPublishAPI" (inserted_at DESC);
