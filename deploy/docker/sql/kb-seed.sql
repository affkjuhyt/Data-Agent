-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  KB seed data — kb-e2e stack                                           ║
-- ╚══════════════════════════════════════════════════════════════════════════╝
--
-- Runs after init.sql at Postgres first-init. Inserts only the structural
-- rows needed for the stack to function. Documents are ingested by the user,
-- not pre-seeded here.
--
-- Tenant / role IDs match the seeded `aeroflow` Keycloak realm users
-- (all six dev users carry tenant_id = a0000000-0000-0000-0000-000000000001
-- via a user-attribute → JWT protocol mapper on the aeroflow-frontend client).
-- The backend filters every query by JWT tenant_id, so the seed MUST use
-- this value or the UI sees nothing after login.
--
--   tenant            a0000000-0000-0000-0000-000000000001
--   knowledge base    00000000-0000-0003-0000-000000000001
--   model (large)     00000000-0000-0000-0000-000000000001
--   model (small)     00000000-0000-0000-0000-000000000002
--
-- Every INSERT is ON CONFLICT DO NOTHING so re-running is a no-op.

-- ── Tenants ──────────────────────────────────────────────────────────────────

INSERT INTO "Tenants" (id, name, slug, data_residency, is_active) VALUES
    ('a0000000-0000-0000-0000-000000000001', 'Dev Tenant', 'dev', 'Asia-SE1', true)
ON CONFLICT (id) DO NOTHING;

-- ── RolePermissions (FK target for KBData.role_id) ───────────────────────────

INSERT INTO "RolePermissions" (id, role_id, resource, action) VALUES
    ('00000000-0000-0000-0000-000000000010', 'PLATFORM_ADMIN',    'kb_data', 'write'),
    ('00000000-0000-0000-0000-000000000011', 'AI_ENGINEER',       'kb_data', 'write'),
    ('00000000-0000-0000-0000-000000000012', 'BUSINESS_OPERATOR', 'kb_data', 'write'),
    ('00000000-0000-0000-0000-000000000013', 'EXECUTIVE',         'kb_data', 'write')
ON CONFLICT (id) DO NOTHING;

-- ── KBModel (embedding models — FK target for Qdrant/Neo4j/TextBlockVersion) ─

-- model_name MUST match the names model-service registers (backends/sync.py), so its
-- name→UUID resolution binds THESE seed UUIDs (which the Qdrant/Neo4j rows reference)
-- instead of inserting fresh ones. id …0001 = the 1024-dim model, …0002 = the smaller.
INSERT INTO "KBModel" (model_id, model_name, task_type) VALUES
    ('00000000-0000-0000-0000-000000000001', 'AITeamVN/Vietnamese_Embedding', 'embedding'),
    ('00000000-0000-0000-0000-000000000002', 'intfloat/multilingual-e5-small', 'embedding')
ON CONFLICT (model_id) DO NOTHING;

-- ── KBKnowledgeBase ──────────────────────────────────────────────────────────

INSERT INTO "KBKnowledgeBase" (knowledge_base_id, tenant_id, name, description, is_active, created_by) VALUES
    ('00000000-0000-0003-0000-000000000001', 'a0000000-0000-0000-0000-000000000001',
     'General Knowledge Base', 'Default knowledge base for the dev tenant', true, 'system')
ON CONFLICT (knowledge_base_id) DO NOTHING;

-- ── KBFilterPolicy ───────────────────────────────────────────────────────────

INSERT INTO "KBFilterPolicy" (policy_id, tenant_id, policy_name, configformat, config, is_active, language) VALUES
    ('00000000-0000-0004-0000-000000000001', 'a0000000-0000-0000-0000-000000000001',
     'PII Filter', 'natural_language', '{"keywords":["SSN","passport","credit card"]}', true, 'english'),
    ('00000000-0000-0004-0000-000000000002', 'a0000000-0000-0000-0000-000000000001',
     'Legal Stopwords', 'exact_word', '{"keywords":["confidential","restricted","draft"]}', false, 'english')
ON CONFLICT (policy_id) DO NOTHING;

-- ── KBExtractionPolicy ───────────────────────────────────────────────────────

INSERT INTO "KBExtractionPolicy" (policy_id, tenant_id, policy_name, policy_type, language) VALUES
    ('00000000-0000-0005-0000-000000000001', 'a0000000-0000-0000-0000-000000000001',
     'Default Extraction', 'entity_node', 'english')
ON CONFLICT (policy_id) DO NOTHING;

-- ── KBQdrantConnection + Collections ─────────────────────────────────────────

INSERT INTO "KBQdrantConnection" (connection_id, tenant_id, is_active, total_collection) VALUES
    ('00000000-0000-0001-0000-000000000001', 'a0000000-0000-0000-0000-000000000001', true, 2)
ON CONFLICT (connection_id) DO NOTHING;

INSERT INTO "KBQdrantCollection"
    (collection_id, connection_id, collection_name, is_active, similarity_metric, points_count, vector_dimension, embedding_model_id) VALUES
    ('00000000-0000-0001-0000-000000000002', '00000000-0000-0001-0000-000000000001',
     'aeroflow_kb_prod',    true,  'cosine', 0, 1024, '00000000-0000-0000-0000-000000000001'),
    ('00000000-0000-0001-0000-000000000003', '00000000-0000-0001-0000-000000000001',
     'aeroflow_kb_archive', false, 'cosine', 0, 1024, '00000000-0000-0000-0000-000000000001')
ON CONFLICT (collection_id) DO NOTHING;

-- ── KBNeo4jConnection + entity schema ────────────────────────────────────────

INSERT INTO "KBNeo4jConnection"
    (connection_id, tenant_id, is_connected, total_node, total_edge, embedding_model_id) VALUES
    ('00000000-0000-0002-0000-000000000001', 'a0000000-0000-0000-0000-000000000001',
     true, 0, 0, '00000000-0000-0000-0000-000000000001')
ON CONFLICT (connection_id) DO NOTHING;

-- ── KBWarehouse ───────────────────────────────────────────────────────────────

INSERT INTO "KBWarehouse" (warehouse_id, tenant_id, service, description) VALUES
    ('40000000-0000-0001-0000-000000000001', 'a0000000-0000-0000-0000-000000000001',
     'snowflake', 'Snowflake analytics warehouse')
ON CONFLICT (warehouse_id) DO NOTHING;

INSERT INTO "KBWarehouseConfig" (config_id, warehouse_id, version_number, is_active, config, created_by) VALUES
    ('40000000-0000-0002-0000-000000000001', '40000000-0000-0001-0000-000000000001', 1, true,
     '{"account":"xy12345.eu-west-1","warehouse":"ANALYTICS_WH","database":"PROD","schema":"PUBLIC","tables":["CUSTOMERS","ORDERS"]}',
     'system')
ON CONFLICT (config_id) DO NOTHING;
