-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  KB seed data                                                          ║
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

-- ── RolePermissions ──────────────────────────────────────────────────────────
--
-- This table now serves TWO distinct purposes. Keep them straight:
--
--   1. ROLE ANCHORS (resource = '_role_anchor'). Four fixed-UUID rows that
--      exist solely because KBData.role_id is a FK to RolePermissions.id.
--      A document's role_id is a SENSITIVITY LABEL, not a permission — see
--      kb_backend/operations/access.py. These UUIDs are mirrored in
--      kb_backend/config.py ROLE_PERMISSION_IDS and MUST NOT change: live
--      KBData rows point at them. They are excluded from permission lookups.
--
--   2. PERMISSION GRANTS (every other resource). One row per
--      (role, resource, permission) the role may exercise. This is the
--      authorisation matrix kb_backend/dependencies/auth.py:require_perm
--      reads at request time — it used to be a hardcoded registry in that
--      file, and this table is now the only source of truth.
--
-- `action` holds the permission name EXACTLY as the route decorators spell it
-- (require_perm("viewDocuments")); `resource` groups permissions by subsystem
-- and is not part of the lookup. Adding a row here grants nothing until some
-- route asks for that name, and renaming one silently revokes access — so
-- these strings and the require_perm() call sites change together.

-- 1. Role anchors. The UPDATE re-labels rows seeded before permissions moved
--    into this table, so an existing database converges on re-run.
INSERT INTO "RolePermissions" (id, role_id, resource, action) VALUES
    ('00000000-0000-0000-0000-000000000010', 'PLATFORM_ADMIN',    '_role_anchor', 'label'),
    ('00000000-0000-0000-0000-000000000011', 'AI_ENGINEER',       '_role_anchor', 'label'),
    ('00000000-0000-0000-0000-000000000012', 'BUSINESS_OPERATOR', '_role_anchor', 'label'),
    ('00000000-0000-0000-0000-000000000013', 'EXECUTIVE',         '_role_anchor', 'label')
ON CONFLICT (id) DO NOTHING;

UPDATE "RolePermissions"
   SET resource = '_role_anchor', action = 'label'
 WHERE id IN ('00000000-0000-0000-0000-000000000010',
              '00000000-0000-0000-0000-000000000011',
              '00000000-0000-0000-0000-000000000012',
              '00000000-0000-0000-0000-000000000013')
   AND (resource, action) IS DISTINCT FROM ('_role_anchor', 'label');

-- 2a. Universal permissions — every role holds all of these.
INSERT INTO "RolePermissions" (id, role_id, resource, action)
SELECT gen_random_uuid(), r.role_id, p.resource, p.action
  FROM (VALUES
      ('PLATFORM_ADMIN'), ('AI_ENGINEER'), ('EXECUTIVE'), ('BUSINESS_OPERATOR')
  ) AS r(role_id)
  CROSS JOIN (VALUES
      ('documents',       'viewDocuments'),
      ('documents',       'editTableRow'),
      ('conflicts',       'viewConflicts'),
      ('fleet',           'viewFleetStats'),
      ('knowledge_bases', 'viewKnowledgeBases'),
      ('retrieval',       'retrieveKnowledge'),
      ('graph',           'viewGraph'),
      ('graph',           'runGraphQuery'),
      ('policies',        'viewPolicies'),
      ('qdrant',          'viewQdrantCollections'),
      ('qdrant',          'searchQdrant'),
      ('trash',           'viewTrash'),
      ('warehouses',      'viewWarehouses'),
      ('sources',         'viewSources')
  ) AS p(resource, action)
ON CONFLICT (role_id, resource, action) DO NOTHING;

-- 2b. Restricted permissions — granted per role.
--     BUSINESS_OPERATOR deliberately holds none: it is read-only beyond 2a.
INSERT INTO "RolePermissions" (id, role_id, resource, action)
SELECT gen_random_uuid(), v.role_id, v.resource, v.action
  FROM (VALUES
      -- PLATFORM_ADMIN — every restricted permission.
      ('PLATFORM_ADMIN', 'data',       'addData'),
      ('PLATFORM_ADMIN', 'data',       'processLayer'),
      ('PLATFORM_ADMIN', 'data',       'deleteData'),
      ('PLATFORM_ADMIN', 'documents',  'addChunkVersion'),
      ('PLATFORM_ADMIN', 'conflicts',  'editConflict'),
      ('PLATFORM_ADMIN', 'policies',   'addFilteringPolicy'),
      ('PLATFORM_ADMIN', 'policies',   'editFilteringPolicy'),
      ('PLATFORM_ADMIN', 'policies',   'deleteFilteringPolicy'),
      ('PLATFORM_ADMIN', 'policies',   'editExtractionPolicy'),
      ('PLATFORM_ADMIN', 'qdrant',     'toggleQdrant'),
      ('PLATFORM_ADMIN', 'warehouses', 'addWarehouse'),
      ('PLATFORM_ADMIN', 'warehouses', 'addWarehouseConfig'),
      ('PLATFORM_ADMIN', 'warehouses', 'editWarehouseConfig'),
      ('PLATFORM_ADMIN', 'sources',    'addSource'),
      ('PLATFORM_ADMIN', 'sources',    'editSource'),
      ('PLATFORM_ADMIN', 'sources',    'deleteSource'),
      ('PLATFORM_ADMIN', 'sources',    'runSource'),
      -- AI_ENGINEER
      ('AI_ENGINEER',    'qdrant',     'toggleQdrant'),
      ('AI_ENGINEER',    'policies',   'editExtractionPolicy'),
      -- EXECUTIVE
      ('EXECUTIVE',      'conflicts',  'editConflict'),
      ('EXECUTIVE',      'data',       'processLayer'),
      ('EXECUTIVE',      'policies',   'addFilteringPolicy')
  ) AS v(role_id, resource, action)
ON CONFLICT (role_id, resource, action) DO NOTHING;

-- ── KBModel (embedding models — FK target for Qdrant/Neo4j/TextBlockVersion) ─

-- model_name MUST match the names model_service registers (backends/sync.py), so its
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
