-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  KB seed data — kb_e2e stack                                        ║
-- ╚══════════════════════════════════════════════════════════════════════════╝
--
-- Runs after init.sql at Postgres first-init. SINGLE source of seed data — the
-- previous Python seeder (services/kb_backend/dev_seed.py) is removed; everything
-- it inserted is reproduced here as SQL so there is one place to look.
--
-- Tenant / role IDs match the seeded `aeroflow` Keycloak realm users (helios-*),
-- whose JWT carries tenant_id = a0000000-0000-0000-0000-000000000001. The backend
-- filters every query by the JWT tenant, so the seed MUST use this tenant or the
-- UI (logged in as a realm user) sees nothing.
--
--   tenant            a0000000-0000-0000-0000-000000000001
--   role (admin)      00000000-0000-0000-0000-000000000010   (RolePermissions.id)
--   knowledge base    00000000-0000-0003-0000-000000000001
--   model (large)     00000000-0000-0000-0000-000000000001
--   model (small)     00000000-0000-0000-0000-000000000002
--
-- Every INSERT is ON CONFLICT DO NOTHING so re-running is a no-op. Order follows
-- FK dependencies: parents first, then documents, then chunks/tables/conflicts.

-- ── Tenants ─────────────────────────────────────────────────────────────────--

INSERT INTO "Tenants" (id, name, slug, data_residency, is_active) VALUES
    ('a0000000-0000-0000-0000-000000000001', 'Dev Tenant', 'dev', 'Asia-SE1', true)
ON CONFLICT (id) DO NOTHING;

-- ── RolePermissions (FK target for KBData.role_id) ───────────────────────────--

INSERT INTO "RolePermissions" (id, role_id, resource, action) VALUES
    ('00000000-0000-0000-0000-000000000010', 'PLATFORM_ADMIN',    'kb_data', 'write'),
    ('00000000-0000-0000-0000-000000000011', 'AI_ENGINEER',       'kb_data', 'write'),
    ('00000000-0000-0000-0000-000000000012', 'BUSINESS_OPERATOR', 'kb_data', 'write'),
    ('00000000-0000-0000-0000-000000000013', 'EXECUTIVE',         'kb_data', 'write')
ON CONFLICT (id) DO NOTHING;

-- ── KBModel (embedding models — FK target for Qdrant/Neo4j/TextBlockVersion) ──--

INSERT INTO "KBModel" (model_id, model_name, task_type) VALUES
    ('00000000-0000-0000-0000-000000000001', 'BAAI/bge-large-en-v1.5', 'embedding'),
    ('00000000-0000-0000-0000-000000000002', 'BAAI/bge-small-en-v1.5', 'embedding')
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

-- ── KBNeo4jConnection + Nodes + Relationships ────────────────────────────────

INSERT INTO "KBNeo4jConnection"
    (connection_id, tenant_id, is_connected, total_node, total_edge, embedding_model_id) VALUES
    ('00000000-0000-0002-0000-000000000001', 'a0000000-0000-0000-0000-000000000001',
     true, 8, 5, '00000000-0000-0000-0000-000000000001')
ON CONFLICT (connection_id) DO NOTHING;

INSERT INTO "KBNeo4jNode" (node_id, connection_id, node_name, node_description) VALUES
    ('00000000-0000-0002-0000-000000000002', '00000000-0000-0002-0000-000000000001', 'Organization', 'Top-level enterprise entity'),
    ('00000000-0000-0002-0000-000000000003', '00000000-0000-0002-0000-000000000001', 'Person',       'Individual employee or contact'),
    ('00000000-0000-0002-0000-000000000004', '00000000-0000-0002-0000-000000000001', 'Policy',       'Corporate governance policy'),
    ('00000000-0000-0002-0000-000000000005', '00000000-0000-0002-0000-000000000001', 'Contract',     'Legal agreement document'),
    ('00000000-0000-0002-0000-000000000006', '00000000-0000-0002-0000-000000000001', 'Location',     'Geographic site or office'),
    ('00000000-0000-0002-0000-000000000007', '00000000-0000-0002-0000-000000000001', 'Regulation',   'Regulatory compliance framework'),
    ('00000000-0000-0002-0000-000000000008', '00000000-0000-0002-0000-000000000001', 'Department',   'Organizational business unit'),
    ('00000000-0000-0002-0000-000000000009', '00000000-0000-0002-0000-000000000001', 'Agreement',    'Bilateral commercial agreement')
ON CONFLICT (node_id) DO NOTHING;

INSERT INTO "KBNeo4jRelationship" (from_node, to_node, score, description) VALUES
    ('00000000-0000-0002-0000-000000000002', '00000000-0000-0002-0000-000000000003', 0.90, '[WORKS_FOR]'),
    ('00000000-0000-0002-0000-000000000002', '00000000-0000-0002-0000-000000000004', 0.85, '[GOVERNED_BY]'),
    ('00000000-0000-0002-0000-000000000002', '00000000-0000-0002-0000-000000000005', 0.80, '[PART_OF]'),
    ('00000000-0000-0002-0000-000000000003', '00000000-0000-0002-0000-000000000006', 0.75, '[LOCATED_IN]'),
    ('00000000-0000-0002-0000-000000000004', '00000000-0000-0002-0000-000000000007', 0.90, '[COMPLIES_WITH]')
ON CONFLICT (from_node, to_node) DO NOTHING;

-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  Documents (KBData)  — role_id = PLATFORM_ADMIN, kb = General KB           ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

-- ── Bronze (5 docs across every source type) ─────────────────────────────────

INSERT INTO "KBData"
    (data_id, tenant_id, role_id, knowledge_base_id, name, extension, language,
     source_type, current_tier, added_by, abstract, metadata) VALUES
    ('10000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000010', '00000000-0000-0003-0000-000000000001',
     'Quarterly Revenue Report 2024', 'pdf', 'english', 'doc', 'bronze', 'helios-admin',
     'Consolidated revenue and expense breakdown for fiscal Q4 2024.',
     '{"type":"Doc/PDF","author":"Finance Team","publishedDate":"2024-12-31","accessRole":"EXECUTIVE"}'),
    ('10000000-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000010', '00000000-0000-0003-0000-000000000001',
     'Product Roadmap Overview Q1 2025', 'docx', 'english', 'doc', 'bronze', 'helios-admin',
     'High-level roadmap for product releases planned in Q1 2025.',
     '{"type":"Doc/DOCX","author":"Product Team","publishedDate":"2025-01-10","accessRole":"AI_ENGINEER"}'),
    ('10000000-0000-0000-0000-000000000003', 'a0000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000010', '00000000-0000-0003-0000-000000000001',
     'Company Homepage Crawl 2025-02', '', 'english', 'web', 'bronze', 'helios-admin',
     'Snapshot of the corporate homepage scraped in February 2025.',
     '{"type":"web","url":"https://example.com","accessRole":"BUSINESS_OPERATOR"}'),
    ('10000000-0000-0000-0000-000000000004', 'a0000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000010', '00000000-0000-0003-0000-000000000001',
     'Brand Identity Logo 2025', 'jpg', 'english', 'image', 'bronze', 'helios-admin',
     'Official brand logo in high resolution JPEG format.',
     '{"type":"Image/JPEG","width":3840,"height":2160,"colorSpace":"sRGB","fileSize":2457600,"accessRole":"BUSINESS_OPERATOR"}'),
    ('10000000-0000-0000-0000-000000000005', 'a0000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000010', '00000000-0000-0003-0000-000000000001',
     'Executive Town Hall Recording Jan 2025', 'mp4', 'english', 'video', 'bronze', 'helios-admin',
     'All-hands town hall recording from January 2025.',
     '{"type":"Video/MP4","width":1920,"height":1080,"totalFrame":162000,"fileSize":524288000,"accessRole":"AI_ENGINEER"}')
ON CONFLICT (data_id) DO NOTHING;

-- ── Silver (3 docs — chunked, staged) ────────────────────────────────────────

INSERT INTO "KBData"
    (data_id, tenant_id, role_id, knowledge_base_id, name, extension, language,
     source_type, current_tier, added_by, abstract, metadata) VALUES
    ('20000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000010', '00000000-0000-0003-0000-000000000001',
     'Employee Handbook 2025', 'pdf', 'english', 'doc', 'silver', 'helios-admin',
     'Company-wide HR policies, benefits, and conduct guidelines for 2025.',
     '{"type":"Doc/PDF","author":"HR Department","publishedDate":"2025-01-05","accessRole":"BUSINESS_OPERATOR"}'),
    ('20000000-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000010', '00000000-0000-0003-0000-000000000001',
     'Support Knowledge Base Snapshot', '', 'english', 'web', 'silver', 'helios-admin',
     'Crawled snapshot of the public support knowledge base.',
     '{"type":"web","url":"https://support.example.com","accessRole":"BUSINESS_OPERATOR"}'),
    ('20000000-0000-0000-0000-000000000003', 'a0000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000010', '00000000-0000-0003-0000-000000000001',
     'Vendor Contract Template', 'docx', 'english', 'doc', 'silver', 'helios-admin',
     'Standard master services agreement template with pricing schedule table.',
     '{"type":"Doc/DOCX","author":"Legal Team","publishedDate":"2024-11-20","accessRole":"EXECUTIVE"}')
ON CONFLICT (data_id) DO NOTHING;

-- ── Gold (3 docs — published) ────────────────────────────────────────────────

INSERT INTO "KBData"
    (data_id, tenant_id, role_id, knowledge_base_id, name, extension, language,
     source_type, current_tier, added_by, abstract, metadata) VALUES
    ('30000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000010', '00000000-0000-0003-0000-000000000001',
     'Data Privacy Policy v3', 'pdf', 'english', 'doc', 'gold', 'helios-admin',
     'Tenant-wide data privacy and retention policy, third revision.',
     '{"type":"Doc/PDF","author":"Compliance Office","publishedDate":"2025-02-01","accessRole":"EXECUTIVE"}'),
    ('30000000-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000010', '00000000-0000-0003-0000-000000000001',
     'Compliance Portal Crawl 2025-02', '', 'english', 'web', 'gold', 'helios-admin',
     'Published crawl of the internal compliance portal.',
     '{"type":"web","url":"https://compliance.example.com","accessRole":"AI_ENGINEER"}'),
    ('30000000-0000-0000-0000-000000000003', 'a0000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000010', '00000000-0000-0003-0000-000000000001',
     'Financial Controls Manual', 'pdf', 'english', 'doc', 'gold', 'helios-admin',
     'Internal financial controls and approval matrix with a thresholds table.',
     '{"type":"Doc/PDF","author":"Finance Team","publishedDate":"2025-01-18","accessRole":"EXECUTIVE"}')
ON CONFLICT (data_id) DO NOTHING;

-- ── Warehouse (KBWarehouse + active config; NOT a KBData row — warehouses live
--    only in KBWarehouse and are merged into the inventory listing from there.
--    added_by in the UI is read from the active config's created_by.) ───────────

INSERT INTO "KBWarehouse" (warehouse_id, service, description) VALUES
    ('40000000-0000-0001-0000-000000000001', 'snowflake', 'Snowflake analytics warehouse')
ON CONFLICT (warehouse_id) DO NOTHING;

INSERT INTO "KBWarehouseConfig" (config_id, warehouse_id, version_number, is_active, config, created_by) VALUES
    ('40000000-0000-0002-0000-000000000001', '40000000-0000-0001-0000-000000000001', 1, true,
     '{"account":"xy12345.eu-west-1","warehouse":"ANALYTICS_WH","database":"PROD","schema":"PUBLIC","tables":["CUSTOMERS","ORDERS"]}',
     'helios-admin')
ON CONFLICT (config_id) DO NOTHING;

-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  Chunks (KBTextBlock + KBTextBlockVersion) + tables (KBTextTable)          ║
-- ╚══════════════════════════════════════════════════════════════════════════╝
-- embedding_model_id = bge-small (00000000-0000-0000-0000-000000000002)

-- Silver: Employee Handbook (2 chunks, single active version each)
INSERT INTO "KBTextBlock" (block_id, owner_id, block_index) VALUES
    ('b0000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 0),
    ('b0000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 1)
ON CONFLICT (block_id) DO NOTHING;

INSERT INTO "KBTextBlockVersion"
    (version_id, block_id, version_number, content, embedding_model_id, is_active, table_involved) VALUES
    ('11100000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001', 1,
     'Section 1 — Code of Conduct. All employees are expected to act with integrity and respect.',
     '00000000-0000-0000-0000-000000000002', true, false),
    ('11100000-0000-0000-0000-000000000002', 'b0000000-0000-0000-0000-000000000002', 1,
     'Section 2 — Benefits. Health, dental, and retirement plan eligibility begins on day one.',
     '00000000-0000-0000-0000-000000000002', true, false)
ON CONFLICT (version_id) DO NOTHING;

-- Silver: Vendor Contract Template (1 chunk with a pricing table)
INSERT INTO "KBTextBlock" (block_id, owner_id, block_index) VALUES
    ('b0000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000003', 0)
ON CONFLICT (block_id) DO NOTHING;

INSERT INTO "KBTextBlockVersion"
    (version_id, block_id, version_number, content, embedding_model_id, is_active, table_involved) VALUES
    ('11100000-0000-0000-0000-000000000003', 'b0000000-0000-0000-0000-000000000003', 1,
     'Pricing schedule — see attached table for tiered rates by service level.',
     '00000000-0000-0000-0000-000000000002', true, true)
ON CONFLICT (version_id) DO NOTHING;

INSERT INTO "KBTextTable" (version_id, table_name, description, data) VALUES
    ('11100000-0000-0000-0000-000000000003', 'Pricing Schedule', 'Tiered service rates',
     '{"columns":["Tier","Monthly","Annual"],"rows":[["Basic","$500","$5400"],["Pro","$1200","$13000"],["Enterprise","Custom","Custom"]]}')
ON CONFLICT (version_id) DO NOTHING;

-- Gold: Data Privacy Policy (2 chunks; chunk 1 has two versions, v2 active)
INSERT INTO "KBTextBlock" (block_id, owner_id, block_index) VALUES
    ('b0000000-0000-0000-0000-000000000010', '30000000-0000-0000-0000-000000000001', 0),
    ('b0000000-0000-0000-0000-000000000011', '30000000-0000-0000-0000-000000000001', 1)
ON CONFLICT (block_id) DO NOTHING;

INSERT INTO "KBTextBlockVersion"
    (version_id, block_id, version_number, content, embedding_model_id, is_active, table_involved) VALUES
    ('22200000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000010', 1,
     'Personal data is retained for 24 months unless legally required otherwise.',
     '00000000-0000-0000-0000-000000000002', false, false),
    ('22200000-0000-0000-0000-000000000002', 'b0000000-0000-0000-0000-000000000010', 2,
     'Personal data is retained for 18 months unless legally required otherwise (rev 3).',
     '00000000-0000-0000-0000-000000000002', true, false),
    ('22200000-0000-0000-0000-000000000003', 'b0000000-0000-0000-0000-000000000011', 1,
     'Data subjects may request erasure via the privacy portal within 30 days.',
     '00000000-0000-0000-0000-000000000002', true, false)
ON CONFLICT (version_id) DO NOTHING;

-- Gold: Financial Controls Manual (1 chunk with an approval-thresholds table)
INSERT INTO "KBTextBlock" (block_id, owner_id, block_index) VALUES
    ('b0000000-0000-0000-0000-000000000020', '30000000-0000-0000-0000-000000000003', 0)
ON CONFLICT (block_id) DO NOTHING;

INSERT INTO "KBTextBlockVersion"
    (version_id, block_id, version_number, content, embedding_model_id, is_active, table_involved) VALUES
    ('22200000-0000-0000-0000-000000000010', 'b0000000-0000-0000-0000-000000000020', 1,
     'Approval thresholds by expenditure amount — see table.',
     '00000000-0000-0000-0000-000000000002', true, true)
ON CONFLICT (version_id) DO NOTHING;

INSERT INTO "KBTextTable" (version_id, table_name, description, data) VALUES
    ('22200000-0000-0000-0000-000000000010', 'Approval Matrix', 'Spend approval thresholds',
     '{"columns":["Amount","Approver"],"rows":[["< $5k","Manager"],["$5k-$50k","Director"],["> $50k","CFO"]]}')
ON CONFLICT (version_id) DO NOTHING;

-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  Conflicts (1 pending batch, 2 conflicts)                                  ║
-- ╚══════════════════════════════════════════════════════════════════════════╝

INSERT INTO "KBConflictBatch" (batch_id, tenant_id, batch_title, status) VALUES
    ('c0000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000001',
     'Data Privacy Policy v3 — ingestion conflicts', 'pending')
ON CONFLICT (batch_id) DO NOTHING;

INSERT INTO "KBConflict"
    (conflict_id, tenant_id, batch_id, conflict_type, severity, status,
     detailed_explanation, existing_snapshot, incoming_snapshot) VALUES
    ('c1000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000001',
     'c0000000-0000-0000-0000-000000000001', 'content_contradiction', 'high', 'pending',
     'Retention period differs between the existing chunk (24 months) and the incoming revision (18 months).',
     '{"content":"Personal data is retained for 24 months."}',
     '{"content":"Personal data is retained for 18 months."}'),
    ('c1000000-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000001',
     'c0000000-0000-0000-0000-000000000001', 'content_duplicate', 'low', 'pending',
     'Incoming erasure clause closely matches an existing clause already in the knowledge base.',
     '{"content":"Data subjects may request erasure within 30 days."}',
     '{"content":"Erasure requests are honored within 30 days via the portal."}')
ON CONFLICT (conflict_id) DO NOTHING;
