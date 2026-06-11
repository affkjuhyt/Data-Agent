CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Enums ─────────────────────────────────────────────────────────────────────

CREATE TYPE KBLanguage        AS ENUM ('english', 'vietnamese');
CREATE TYPE KBSourceType      AS ENUM ('doc', 'web', 'image', 'video', 'warehouse');
CREATE TYPE KBTier            AS ENUM ('bronze', 'silver', 'gold');
CREATE TYPE KBPolicyFormat    AS ENUM ('Natural Language', 'Exact Match For Word or Phrase');
CREATE TYPE KBPolicyType      AS ENUM ('Entity', 'Relationship Edge');
CREATE TYPE KBConflictType    AS ENUM ('content_contradiction','content_conflict','content_duplicate','content_update','table_schema');
CREATE TYPE KBConflictSeverity AS ENUM ('low', 'medium', 'high');
CREATE TYPE KBConflictStatus  AS ENUM ('pending', 'awaiting', 'resolved');
CREATE TYPE KBTaskType        AS ENUM ('embedding', 'Vision Language Model');
CREATE TYPE KBSimilarityMetric AS ENUM ('cosine', 'euclidean', 'dot');
CREATE TYPE KBHttpMethod      AS ENUM ('GET', 'POST', 'PUT', 'PATCH', 'DELETE');
CREATE TYPE KBAPIType         AS ENUM ('NEO4J', 'QDRANT', 'RETRIEVE');

-- ── KBModel ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBModel (
    model_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_name  VARCHAR(50),
    task_type   KBTaskType NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_model_task_type ON KBModel (task_type);

-- ── KBModelVersion ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBModelVersion (
    version_id     SERIAL PRIMARY KEY,
    model_id       UUID REFERENCES KBModel(model_id),
    version_number INT,
    added_on       TIMESTAMPTZ,
    added_by       UUID,
    config         JSONB,
    is_active      BOOL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_modelversion_active ON KBModelVersion (model_id, version_id) WHERE is_active = TRUE;

-- ── KBData ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBData (
    data_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id    UUID NOT NULL,
    role_id      UUID NOT NULL,
    name         VARCHAR(50) UNIQUE,
    extension    VARCHAR(10),
    language     KBLanguage,
    source_type  KBSourceType NOT NULL,
    current_tier KBTier NOT NULL DEFAULT 'bronze',
    added_on     TIMESTAMPTZ,
    added_by     UUID,
    abstract     TEXT,
    metadata     JSONB,
    path         TEXT
);
CREATE INDEX IF NOT EXISTS idx_data_tenant_source   ON KBData (tenant_id, source_type);
CREATE INDEX IF NOT EXISTS idx_data_tenant_role     ON KBData (tenant_id, role_id);
CREATE INDEX IF NOT EXISTS idx_data_tenant_tier     ON KBData (tenant_id, current_tier);

-- ── KBLifecycleHistory ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBLifecycleHistory (
    history_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    data_id         UUID NOT NULL REFERENCES KBData(data_id),
    from_tier       KBTier,
    to_tier         KBTier NOT NULL,
    transitioned_at TIMESTAMPTZ NOT NULL,
    approved_by     UUID,
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_lifecycle_data_id ON KBLifecycleHistory (data_id);

-- ── KBFilterPolicy ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBFilterPolicy (
    policy_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID NOT NULL,
    policy_name VARCHAR NOT NULL,
    configformat KBPolicyFormat NOT NULL,
    config      JSONB,
    is_active   BOOL DEFAULT FALSE,
    created_at  TIMESTAMPTZ,
    created_by  UUID,
    language    KBLanguage
);
CREATE INDEX IF NOT EXISTS idx_filterpolicy_tenant_lang ON KBFilterPolicy (tenant_id, language);

-- ── KBExtractionPolicy ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBExtractionPolicy (
    policy_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       UUID NOT NULL,
    policy_name     VARCHAR NOT NULL,
    policy_type     KBPolicyType NOT NULL,
    custom_override TEXT,
    created_at      TIMESTAMPTZ,
    created_by      UUID,
    language        KBLanguage
);
CREATE INDEX IF NOT EXISTS idx_extractionpolicy_tenant_lang ON KBExtractionPolicy (tenant_id, language);

-- ── KBConflictBatch ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBConflictBatch (
    batch_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id   UUID,
    batch_title VARCHAR(100) NOT NULL,
    created_at  TIMESTAMPTZ,
    status      KBConflictStatus NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_conflictbatch_tenant_status ON KBConflictBatch (tenant_id, status);

-- ── KBConflict ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBConflict (
    conflict_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id              UUID,
    batch_id               UUID REFERENCES KBConflictBatch(batch_id),
    conflict_type          KBConflictType NOT NULL,
    detected_at            TIMESTAMPTZ,
    severity               KBConflictSeverity NOT NULL,
    status                 KBConflictStatus NOT NULL DEFAULT 'pending',
    detailed_explanation   TEXT,
    existing_snapshot      JSONB,
    incoming_snapshot      JSONB,
    resolution_instruction TEXT,
    resolved_at            TIMESTAMPTZ,
    resolved_by            UUID,
    selected_resolution_method VARCHAR(50)
);
-- Used when joining KBConflictBatch with its pending conflicts
CREATE INDEX IF NOT EXISTS idx_conflict_batch_pending       ON KBConflict (batch_id) WHERE status = 'pending';
-- Used for broader conflict queries (tenant + status + severity filtering)
CREATE INDEX IF NOT EXISTS idx_conflict_tenant_status_severity ON KBConflict (tenant_id, status, severity);

-- ── KBWarehouse ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBWarehouse (
    warehouse_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    service      VARCHAR(50) NOT NULL,
    description  TEXT
);

-- ── KBWarehouse_Config ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBWarehouse_Config (
    config_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    warehouse_id   UUID NOT NULL REFERENCES KBWarehouse(warehouse_id),
    version_number INT,
    is_active      BOOL,
    config         JSONB,
    created_at     TIMESTAMPTZ,
    created_by     UUID
);
CREATE INDEX IF NOT EXISTS idx_warehouseconfig_version   ON KBWarehouse_Config (warehouse_id, version_number);
CREATE INDEX IF NOT EXISTS idx_warehouseconfig_active    ON KBWarehouse_Config (warehouse_id) WHERE is_active = TRUE;

-- ── KBTable ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBTable (
    owner_id    UUID,
    table_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    table_name  VARCHAR(50),
    description TEXT,
    schema      JSONB,
    created_on  TIMESTAMPTZ,
    created_by  UUID
);
CREATE INDEX IF NOT EXISTS idx_table_name     ON KBTable (table_name);
CREATE INDEX IF NOT EXISTS idx_table_owner_id ON KBTable (owner_id);

-- ── KBTextBlock ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBTextBlock (
    block_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_id    UUID REFERENCES KBData(data_id),
    block_index INT
);
CREATE INDEX IF NOT EXISTS idx_textblock_owner ON KBTextBlock (owner_id, block_id);

-- ── KBTextBlockVersion ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBTextBlockVersion (
    version_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    block_id           UUID NOT NULL REFERENCES KBTextBlock(block_id) ON DELETE CASCADE,
    version_number     INT NOT NULL,
    content            TEXT,
    created_at         TIMESTAMPTZ,
    created_by         UUID,
    is_active          BOOL DEFAULT FALSE,
    table_involved     BOOL,
    embedding_model_id UUID REFERENCES KBModel(model_id),
    payload            JSONB
);
CREATE INDEX IF NOT EXISTS idx_textblockversion_active ON KBTextBlockVersion (block_id, version_number) WHERE is_active = TRUE;

-- ── KBTextTable ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBTextTable (
    version_id  UUID PRIMARY KEY REFERENCES KBTextBlockVersion(version_id) ON DELETE CASCADE,
    table_name  VARCHAR(50),
    description TEXT,
    data        JSONB
);

-- ── KBQdrantConnection ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBQdrantConnection (
    connection_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id        UUID,
    is_active        BOOL,
    created_at       TIMESTAMPTZ,
    total_collection INT DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_qdrantconn_active ON KBQdrantConnection (tenant_id) WHERE is_active = TRUE;

-- ── KBQdrantCollection ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBQdrantCollection (
    collection_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    connection_id    UUID NOT NULL REFERENCES KBQdrantConnection(connection_id),
    collection_name  VARCHAR(50) NOT NULL,
    is_active        BOOL,
    similarity_metric KBSimilarityMetric DEFAULT 'cosine',
    points_count     INT DEFAULT 0,
    vector_dimension INT,
    embedding_model_id UUID REFERENCES KBModel(model_id)
);
CREATE INDEX IF NOT EXISTS idx_qdrantcol_connection ON KBQdrantCollection (connection_id);

-- ── KBNeo4jConnection ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBNeo4jConnection (
    connection_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id          UUID,
    is_connected       BOOL,
    total_node         INT DEFAULT 0,
    total_edge         INT DEFAULT 0,
    created_at         TIMESTAMPTZ,
    embedding_model_id UUID REFERENCES KBModel(model_id)
);
CREATE INDEX IF NOT EXISTS idx_neo4jconn_active ON KBNeo4jConnection (tenant_id) WHERE is_connected = TRUE;

-- ── KBNeo4jNode ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBNeo4jNode (
    connection_id    UUID REFERENCES KBNeo4jConnection(connection_id),
    node_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    node_name        VARCHAR(50),
    node_description TEXT,
    inserted_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_neo4jnode_connection ON KBNeo4jNode (connection_id);

-- ── KBNeo4jRelationship ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBNeo4jRelationship (
    from_node   UUID REFERENCES KBNeo4jNode(node_id),
    to_node     UUID REFERENCES KBNeo4jNode(node_id),
    score       FLOAT,
    description TEXT,
    PRIMARY KEY (from_node, to_node)
);

-- ── KBEntityLookup ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBEntityLookup (
    lookup_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    alias_name     VARCHAR(50) NOT NULL,
    canonical_name VARCHAR(200) NOT NULL,
    created_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_entitylookup_alias ON KBEntityLookup USING HASH (alias_name);

-- ── KBPublishAPI ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS KBPublishAPI (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id    UUID,
    name         VARCHAR(50),
    type         KBAPIType,
    endpoint_url VARCHAR(100),
    http_method  KBHttpMethod,
    is_published BOOL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_publishapi_active ON KBPublishAPI (tenant_id) WHERE is_published = TRUE;
