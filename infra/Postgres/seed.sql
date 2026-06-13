-- ── Dev seed data for KB testing environment ─────────────────────────────────
--
-- Dev tenant UUID : 00000000-0000-0000-0000-000000000001  (matches mockApi.ts)
-- Role UUID       : 00000000-0000-0000-ffff-000000000001  (dummy, no FK enforced)
-- Model large     : 00000000-0000-0000-0000-000000000001  (bge-large-en-v1.5)
-- Model small     : 00000000-0000-0000-0000-000000000002  (bge-small-en-v1.5)
--
-- Insert order follows FK dependencies.

-- ── KBData — Bronze (5 docs) ──────────────────────────────────────────────────

INSERT INTO "KBData"
    (data_id, tenant_id, role_id, name, extension, language, source_type,
     current_tier, added_by, abstract, metadata)
VALUES
    -- PDF
    ('10000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'Quarterly Revenue Report 2024', 'pdf', 'english', 'doc', 'bronze',
     'dev-user',
     'Consolidated revenue and expense breakdown for fiscal Q4 2024.',
     '{"type":"Doc/PDF","author":"Finance Team","publishedDate":"2024-12-31","accessRole":"EXECUTIVE"}'),

    -- DOCX
    ('10000000-0000-0000-0000-000000000002',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'Product Roadmap Overview Q1 2025', 'docx', 'english', 'doc', 'bronze',
     'dev-user',
     'High-level roadmap for product releases planned in Q1 2025.',
     '{"type":"Doc/DOCX","author":"Product Team","publishedDate":"2025-01-10","accessRole":"AI_ENGINEER"}'),

    -- Web
    ('10000000-0000-0000-0000-000000000003',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'Company Homepage Crawl 2025-02', '', 'english', 'web', 'bronze',
     'dev-user',
     'Snapshot of the corporate homepage scraped in February 2025.',
     '{"type":"web","url":"https://example.com","accessRole":"BUSINESS_OPERATOR"}'),

    -- Image
    ('10000000-0000-0000-0000-000000000004',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'Brand Identity Logo 2025', 'jpg', 'english', 'image', 'bronze',
     'dev-user',
     'Official brand logo in high resolution JPEG format.',
     '{"type":"Image/JPEG","width":3840,"height":2160,"colorSpace":"sRGB","fileSize":2457600,"accessRole":"BUSINESS_OPERATOR"}'),

    -- Video
    ('10000000-0000-0000-0000-000000000005',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'Executive Town Hall Recording Jan 2025', 'mp4', 'english', 'video', 'bronze',
     'dev-user',
     'All-hands town hall recording from January 2025.',
     '{"type":"Video/MP4","width":1920,"height":1080,"totalFrame":162000,"fileSize":524288000,"accessRole":"AI_ENGINEER"}')
ON CONFLICT (name) DO NOTHING;

-- ── KBData — Silver (4 docs) ──────────────────────────────────────────────────

INSERT INTO "KBData"
    (data_id, tenant_id, role_id, name, extension, language, source_type,
     current_tier, added_by, abstract, metadata)
VALUES
    ('20000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'Customer Support Policy Manual v3', 'pdf', 'english', 'doc', 'silver',
     'dev-user',
     'Internal manual covering escalation paths, SLA targets, and resolution workflows.',
     '{"type":"Doc/PDF","author":"Support Ops","publishedDate":"2024-11-15","accessRole":"BUSINESS_OPERATOR"}'),

    ('20000000-0000-0000-0000-000000000002',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'API Integration Guide v2', 'docx', 'english', 'doc', 'silver',
     'dev-user',
     'Step-by-step guide for integrating with the AeroFlow REST API.',
     '{"type":"Doc/DOCX","author":"Platform Engineering","publishedDate":"2025-01-22","accessRole":"AI_ENGINEER"}'),

    ('20000000-0000-0000-0000-000000000003',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'Competitor Analysis Crawl 2025-03', '', 'english', 'web', 'silver',
     'dev-user',
     'Structured competitive landscape data scraped from public sources.',
     '{"type":"web","url":"https://competitor-tracker.internal/march-2025","accessRole":"EXECUTIVE"}'),

    ('20000000-0000-0000-0000-000000000004',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'Operations Dashboard Screenshot Set', 'jpg', 'english', 'image', 'silver',
     'dev-user',
     'Collection of annotated operational dashboards from production monitoring.',
     '{"type":"Image/JPEG","width":2560,"height":1440,"colorSpace":"sRGB","fileSize":1048576,"accessRole":"AI_ENGINEER"}')
ON CONFLICT (name) DO NOTHING;

-- ── KBData — Gold (5 docs) ────────────────────────────────────────────────────

INSERT INTO "KBData"
    (data_id, tenant_id, role_id, name, extension, language, source_type,
     current_tier, added_by, abstract, metadata)
VALUES
    ('30000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'Enterprise Risk Management Framework 2024', 'pdf', 'english', 'doc', 'gold',
     'dev-user',
     'Comprehensive ERM framework covering operational, financial, and compliance risk domains.',
     '{"type":"Doc/PDF","author":"Risk & Compliance","publishedDate":"2024-09-01","accessRole":"EXECUTIVE"}'),

    ('30000000-0000-0000-0000-000000000002',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'Financial Performance Summary FY2024', 'pdf', 'english', 'doc', 'gold',
     'dev-user',
     'Audited FY2024 financial statements with YoY comparison and KPI analysis.',
     '{"type":"Doc/PDF","author":"CFO Office","publishedDate":"2025-02-14","accessRole":"EXECUTIVE"}'),

    ('30000000-0000-0000-0000-000000000003',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'Technology Architecture Blueprint v4', 'docx', 'english', 'doc', 'gold',
     'dev-user',
     'System architecture reference covering microservices topology, data flows, and SLOs.',
     '{"type":"Doc/DOCX","author":"CTO Office","publishedDate":"2025-03-01","accessRole":"AI_ENGINEER"}'),

    ('30000000-0000-0000-0000-000000000004',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'Market Intelligence Report Q4 2024', '', 'english', 'web', 'gold',
     'dev-user',
     'Curated market signals and industry trend analysis for Q4 2024.',
     '{"type":"web","url":"https://market-intel.internal/q4-2024","accessRole":"EXECUTIVE"}'),

    ('30000000-0000-0000-0000-000000000005',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'Investor Relations Video Deck Q3 2024', 'mp4', 'english', 'video', 'gold',
     'dev-user',
     'Narrated slide deck from the Q3 2024 investor briefing.',
     '{"type":"Video/MP4","width":1920,"height":1080,"totalFrame":108000,"fileSize":314572800,"accessRole":"EXECUTIVE"}')
ON CONFLICT (name) DO NOTHING;

-- ── KBData — Warehouse gold doc (same UUID as KBWarehouse below) ──────────────

INSERT INTO "KBData"
    (data_id, tenant_id, role_id, name, extension, language, source_type,
     current_tier, added_by, abstract, metadata)
VALUES
    ('40000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-ffff-000000000001',
     'Snowflake Analytics Warehouse', '', 'english', 'warehouse', 'gold',
     'dev-user',
     'Enterprise Snowflake instance housing sales, finance, and operational data marts.',
     '{"type":"Warehouse/Snowflake","warehouseType":"snowflake","accessRole":"AI_ENGINEER"}')
ON CONFLICT (name) DO NOTHING;

-- ── KBLifecycleHistory ────────────────────────────────────────────────────────

-- Silver docs: bronze → silver
INSERT INTO "KBLifecycleHistory"
    (history_id, data_id, from_tier, to_tier, approved_by, notes, pipeline_metadata,
     transitioned_at)
VALUES
    ('51000000-0000-0000-0000-000000000001',
     '20000000-0000-0000-0000-000000000001',
     'bronze', 'silver', 'dev-user',
     'Auto-promoted after OCR + chunk extraction passed quality gate.',
     '{"chunks_accepted":12,"chunks_rejected":1,"tables_found":2,"total_chunks":13}',
     NOW() - INTERVAL '5 days'),

    ('51000000-0000-0000-0000-000000000002',
     '20000000-0000-0000-0000-000000000002',
     'bronze', 'silver', 'dev-user',
     'Manual review approved by AI_ENGINEER role.',
     '{"chunks_accepted":8,"chunks_rejected":0,"tables_found":0,"total_chunks":8}',
     NOW() - INTERVAL '3 days'),

    ('51000000-0000-0000-0000-000000000003',
     '20000000-0000-0000-0000-000000000003',
     'bronze', 'silver', 'dev-user',
     'Web content parsed and chunked successfully.',
     '{"chunks_accepted":6,"chunks_rejected":0,"tables_found":1,"total_chunks":6}',
     NOW() - INTERVAL '2 days')
ON CONFLICT DO NOTHING;

-- Gold docs: bronze → silver, then silver → gold
INSERT INTO "KBLifecycleHistory"
    (history_id, data_id, from_tier, to_tier, approved_by, notes, pipeline_metadata,
     transitioned_at)
VALUES
    ('52000000-0000-0000-0000-000000000001',
     '30000000-0000-0000-0000-000000000001',
     'bronze', 'silver', 'dev-user',
     'Initial chunking passed all quality checks.',
     '{"chunks_accepted":18,"chunks_rejected":2,"tables_found":3,"total_chunks":20}',
     NOW() - INTERVAL '14 days'),

    ('52000000-0000-0000-0000-000000000002',
     '30000000-0000-0000-0000-000000000001',
     'silver', 'gold', 'dev-user',
     'Graph extraction and embedding committed to vector store.',
     '{"committed":16,"conflicted":1,"skipped":1,"failed":0,"embed_model_id":"00000000-0000-0000-0000-000000000002"}',
     NOW() - INTERVAL '10 days'),

    ('52000000-0000-0000-0000-000000000003',
     '30000000-0000-0000-0000-000000000002',
     'bronze', 'silver', 'dev-user',
     'PDF parsing completed with table extraction.',
     '{"chunks_accepted":10,"chunks_rejected":0,"tables_found":2,"total_chunks":10}',
     NOW() - INTERVAL '20 days'),

    ('52000000-0000-0000-0000-000000000004',
     '30000000-0000-0000-0000-000000000002',
     'silver', 'gold', 'dev-user',
     'All chunks embedded and knowledge graph updated.',
     '{"committed":10,"conflicted":0,"skipped":0,"failed":0,"embed_model_id":"00000000-0000-0000-0000-000000000002"}',
     NOW() - INTERVAL '15 days'),

    ('52000000-0000-0000-0000-000000000005',
     '30000000-0000-0000-0000-000000000003',
     'bronze', 'silver', 'dev-user',
     'Architecture document chunked successfully.',
     '{"chunks_accepted":14,"chunks_rejected":1,"tables_found":1,"total_chunks":15}',
     NOW() - INTERVAL '7 days'),

    ('52000000-0000-0000-0000-000000000006',
     '30000000-0000-0000-0000-000000000003',
     'silver', 'gold', 'dev-user',
     'Promoted to gold after executive sign-off.',
     '{"committed":13,"conflicted":1,"skipped":0,"failed":0,"embed_model_id":"00000000-0000-0000-0000-000000000001"}',
     NOW() - INTERVAL '4 days')
ON CONFLICT DO NOTHING;

-- ── KBTextBlock — Silver doc: Customer Support Policy Manual ──────────────────

INSERT INTO "KBTextBlock"(block_id, owner_id, block_index) VALUES
    ('61000000-0000-0000-0000-000000000001', '20000000-0000-0000-0000-000000000001', 0),
    ('61000000-0000-0000-0000-000000000002', '20000000-0000-0000-0000-000000000001', 1),
    ('61000000-0000-0000-0000-000000000003', '20000000-0000-0000-0000-000000000001', 2)
ON CONFLICT DO NOTHING;

INSERT INTO "KBTextBlockVersion"
    (version_id, block_id, version_number, content, created_by, is_active,
     table_involved, embedding_model_id, payload)
VALUES
    ('71000000-0000-0000-0000-000000000001',
     '61000000-0000-0000-0000-000000000001', 1,
     'All customer support tickets must be acknowledged within 2 business hours. Priority P1 issues require escalation to the on-call engineer within 30 minutes of receipt. Ticket ownership must not be transferred without explicit handoff documentation.',
     'dev-user', true, false, '00000000-0000-0000-0000-000000000002',
     '{"entities":["SLA","ticket","escalation","P1"],"intents":["define_policy","enforce_compliance"]}'),

    ('71000000-0000-0000-0000-000000000002',
     '61000000-0000-0000-0000-000000000002', 1,
     'Customer refund requests are processed within 5 business days. Refunds above $500 require manager approval via the finance portal. All refund decisions must be logged in Zendesk with reason codes.',
     'dev-user', true, false, '00000000-0000-0000-0000-000000000002',
     '{"entities":["refund","approval","Zendesk","finance portal"],"intents":["define_workflow","financial_control"]}'),

    ('71000000-0000-0000-0000-000000000003',
     '61000000-0000-0000-0000-000000000003', 1,
     'Support agents are required to complete quarterly compliance training. Failure to complete training within the grace period results in temporary suspension of ticket handling permissions until the module is passed.',
     'dev-user', true, false, '00000000-0000-0000-0000-000000000002',
     '{"entities":["compliance training","suspension","permissions","quarterly"],"intents":["enforce_policy","training_requirement"]}')
ON CONFLICT DO NOTHING;

-- ── KBTextBlock — Silver doc: API Integration Guide ──────────────────────────

INSERT INTO "KBTextBlock"(block_id, owner_id, block_index) VALUES
    ('61000000-0000-0000-0000-000000000004', '20000000-0000-0000-0000-000000000002', 0),
    ('61000000-0000-0000-0000-000000000005', '20000000-0000-0000-0000-000000000002', 1)
ON CONFLICT DO NOTHING;

INSERT INTO "KBTextBlockVersion"
    (version_id, block_id, version_number, content, created_by, is_active,
     table_involved, embedding_model_id, payload)
VALUES
    ('71000000-0000-0000-0000-000000000004',
     '61000000-0000-0000-0000-000000000004', 1,
     'Authentication is handled via Bearer tokens issued by the /auth/token endpoint. Tokens expire after 3600 seconds. Clients must refresh tokens before expiry using the refresh_token grant type. All API calls must include the Authorization header.',
     'dev-user', true, false, '00000000-0000-0000-0000-000000000002',
     '{"entities":["Bearer token","authentication","refresh_token","Authorization header"],"intents":["api_authentication","security_requirement"]}'),

    ('71000000-0000-0000-0000-000000000005',
     '61000000-0000-0000-0000-000000000005', 1,
     'Rate limits are enforced at 1000 requests per minute per API key. Exceeding the limit returns HTTP 429 with a Retry-After header indicating the wait time in seconds. Burst allowance of 50 additional requests is granted for short spikes.',
     'dev-user', true, false, '00000000-0000-0000-0000-000000000002',
     '{"entities":["rate limit","HTTP 429","Retry-After","burst allowance"],"intents":["define_constraint","api_behavior"]}')
ON CONFLICT DO NOTHING;

-- ── KBTextBlock — Gold doc: ERM Framework (2 versions per block) ──────────────

INSERT INTO "KBTextBlock"(block_id, owner_id, block_index) VALUES
    ('62000000-0000-0000-0000-000000000001', '30000000-0000-0000-0000-000000000001', 0),
    ('62000000-0000-0000-0000-000000000002', '30000000-0000-0000-0000-000000000001', 1),
    ('62000000-0000-0000-0000-000000000003', '30000000-0000-0000-0000-000000000001', 2)
ON CONFLICT DO NOTHING;

INSERT INTO "KBTextBlockVersion"
    (version_id, block_id, version_number, content, created_by, is_active,
     table_involved, embedding_model_id, payload)
VALUES
    -- Block 1: v1 (inactive original)
    ('72000000-0000-0000-0000-000000000001',
     '62000000-0000-0000-0000-000000000001', 1,
     'Operational risk includes failures arising from inadequate internal processes, people, systems, or external events. The organisation maintains a risk register updated quarterly.',
     'dev-user', false, false, '00000000-0000-0000-0000-000000000002',
     '{"entities":["operational risk","risk register","internal processes"],"intents":["define_risk_category"]}'),

    -- Block 1: v2 (active revised)
    ('72000000-0000-0000-0000-000000000002',
     '62000000-0000-0000-0000-000000000001', 2,
     'Operational risk encompasses failures arising from inadequate internal processes, people, systems, or external events. Risk registers are updated monthly and reviewed by the CRO. All Tier-1 risks must have documented mitigation plans.',
     'dev-user', true, false, '00000000-0000-0000-0000-000000000002',
     '{"entities":["operational risk","risk register","CRO","Tier-1","mitigation plan"],"intents":["define_risk_category","governance_requirement"]}'),

    -- Block 2: v1 (inactive)
    ('72000000-0000-0000-0000-000000000003',
     '62000000-0000-0000-0000-000000000002', 1,
     'Financial risk covers exposure to market volatility, credit defaults, and liquidity constraints. Hedging strategies are reviewed annually.',
     'dev-user', false, false, '00000000-0000-0000-0000-000000000002',
     '{"entities":["financial risk","market volatility","hedging","liquidity"],"intents":["define_risk_category"]}'),

    -- Block 2: v2 (active)
    ('72000000-0000-0000-0000-000000000004',
     '62000000-0000-0000-0000-000000000002', 2,
     'Financial risk encompasses exposure to market volatility, credit defaults, and liquidity constraints. Hedging strategies are reviewed semi-annually by the Treasury function. Currency exposure above $1M requires board-level approval.',
     'dev-user', true, false, '00000000-0000-0000-0000-000000000002',
     '{"entities":["financial risk","hedging","liquidity","Treasury","board approval"],"intents":["define_risk_category","financial_governance"]}'),

    -- Block 3: v1 (active, has table)
    ('72000000-0000-0000-0000-000000000005',
     '62000000-0000-0000-0000-000000000003', 1,
     'Risk classification matrix mapping likelihood and impact across four severity tiers.',
     'dev-user', true, true, '00000000-0000-0000-0000-000000000002',
     '{"entities":["risk matrix","likelihood","impact","severity"],"intents":["define_framework","provide_reference_data"]}')
ON CONFLICT DO NOTHING;

-- KBTextTable for gold ERM block 3 v1
INSERT INTO "KBTextTable" (version_id, table_name, description, data) VALUES
    ('72000000-0000-0000-0000-000000000005',
     'Risk Classification Matrix',
     'Likelihood vs impact severity grid',
     '{"headers":["Likelihood","Low Impact","Medium Impact","High Impact"],
       "rows":[
         ["Rare",      "Low",    "Low",      "Medium"],
         ["Unlikely",  "Low",    "Medium",   "High"],
         ["Possible",  "Medium", "High",     "Critical"],
         ["Likely",    "Medium", "Critical", "Critical"],
         ["Almost Certain","High","Critical","Critical"]
       ]}')
ON CONFLICT DO NOTHING;

-- ── KBTextBlock — Gold doc: Financial Performance Summary (with table) ────────

INSERT INTO "KBTextBlock"(block_id, owner_id, block_index) VALUES
    ('62000000-0000-0000-0000-000000000004', '30000000-0000-0000-0000-000000000002', 0),
    ('62000000-0000-0000-0000-000000000005', '30000000-0000-0000-0000-000000000002', 1)
ON CONFLICT DO NOTHING;

INSERT INTO "KBTextBlockVersion"
    (version_id, block_id, version_number, content, created_by, is_active,
     table_involved, embedding_model_id, payload)
VALUES
    ('72000000-0000-0000-0000-000000000006',
     '62000000-0000-0000-0000-000000000004', 1,
     'Total revenue for FY2024 reached $142.7M, a 23% year-on-year increase driven by SaaS subscription growth. EBITDA margin improved to 18.4% from 14.1% in FY2023 due to operational leverage and workforce rationalisation.',
     'dev-user', true, false, '00000000-0000-0000-0000-000000000002',
     '{"entities":["revenue","EBITDA","SaaS","FY2024","FY2023"],"intents":["report_financial_results","yoy_comparison"]}'),

    -- Block with embedded table
    ('72000000-0000-0000-0000-000000000007',
     '62000000-0000-0000-0000-000000000005', 1,
     'Key financial KPIs by business unit for FY2024.',
     'dev-user', true, true, '00000000-0000-0000-0000-000000000002',
     '{"entities":["KPI","business unit","revenue","gross margin"],"intents":["report_financial_results","segment_breakdown"]}')
ON CONFLICT DO NOTHING;

INSERT INTO "KBTextTable" (version_id, table_name, description, data) VALUES
    ('72000000-0000-0000-0000-000000000007',
     'FY2024 Business Unit KPIs',
     'Revenue, gross margin, and headcount by BU',
     '{"headers":["Business Unit","Revenue ($M)","Gross Margin (%)","Headcount"],
       "rows":[
         ["Enterprise Sales","68.4","62","210"],
         ["SMB Subscriptions","41.2","71","85"],
         ["Professional Services","33.1","38","130"]
       ]}')
ON CONFLICT DO NOTHING;

-- ── KBTextBlock — Gold doc: Tech Architecture Blueprint ───────────────────────

INSERT INTO "KBTextBlock"(block_id, owner_id, block_index) VALUES
    ('62000000-0000-0000-0000-000000000006', '30000000-0000-0000-0000-000000000003', 0),
    ('62000000-0000-0000-0000-000000000007', '30000000-0000-0000-0000-000000000003', 1)
ON CONFLICT DO NOTHING;

INSERT INTO "KBTextBlockVersion"
    (version_id, block_id, version_number, content, created_by, is_active,
     table_involved, embedding_model_id, payload)
VALUES
    ('72000000-0000-0000-0000-000000000008',
     '62000000-0000-0000-0000-000000000006', 1,
     'The platform is decomposed into 14 bounded-context microservices communicating over Kafka event streams. Each service owns its own PostgreSQL schema and exposes a REST API behind Kong gateway. Service-to-service calls within a context boundary use gRPC.',
     'dev-user', true, false, '00000000-0000-0000-0000-000000000001',
     '{"entities":["microservices","Kafka","PostgreSQL","Kong","gRPC","REST"],"intents":["describe_architecture","define_communication_pattern"]}'),

    ('72000000-0000-0000-0000-000000000009',
     '62000000-0000-0000-0000-000000000007', 1,
     'SLOs are defined per service: ingestion pipeline targets 99.5% availability with p99 latency under 800ms; query service targets 99.9% availability with p99 under 200ms. Error budget burn rate is reviewed weekly.',
     'dev-user', true, false, '00000000-0000-0000-0000-000000000001',
     '{"entities":["SLO","ingestion pipeline","query service","error budget","latency"],"intents":["define_slo","reliability_target"]}')
ON CONFLICT DO NOTHING;

-- ── KBWarehouse ───────────────────────────────────────────────────────────────
-- warehouse_id must match the KBData.data_id for the warehouse doc above so
-- that _assert_tenant (which checks KBData) works for warehouse config routes.

INSERT INTO "KBWarehouse"(warehouse_id, service, description) VALUES
    ('40000000-0000-0000-0000-000000000001',
     'snowflake',
     'Enterprise Snowflake instance — sales, finance, and operational data marts.')
ON CONFLICT DO NOTHING;

INSERT INTO "KBWarehouseConfig"
    (config_id, warehouse_id, version_number, is_active, created_by, config)
VALUES
    ('80000000-0000-0000-0000-000000000001',
     '40000000-0000-0000-0000-000000000001',
     1, true, 'dev-user',
     '{"connection":{"host":"org-account.snowflakecomputing.com","port":"443","database":"ANALYTICS_DB"},"selected_tables":["sales_fact","customer_dim","product_dim"],"sync_schedule":"0 2 * * *","schema_filter":"PUBLIC"}')
ON CONFLICT DO NOTHING;

-- ── KBFilterPolicy ────────────────────────────────────────────────────────────

INSERT INTO "KBFilterPolicy"
    (policy_id, tenant_id, policy_name, configformat, config, is_active, created_by, language)
VALUES
    ('91000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001',
     'PII Redaction — Names & Emails',
     'natural_language',
     '{"rules":[{"type":"entity_node","value":"PERSON"},{"type":"entity_node","value":"EMAIL"}],"action":"redact"}',
     true, 'dev-user', 'english'),

    ('91000000-0000-0000-0000-000000000002',
     '00000000-0000-0000-0000-000000000001',
     'Competitor Brand Exclusion',
     'exact_word',
     '{"rules":[{"type":"relationship_edge","value":"CompetitorBrand"},{"type":"relationship_edge","value":"ExternalProduct"}],"action":"exclude"}',
     true, 'dev-user', 'english'),

    ('91000000-0000-0000-0000-000000000003',
     '00000000-0000-0000-0000-000000000001',
     'Internal Code Name Filter',
     'exact_word',
     '{"rules":[{"type":"entity_node","value":"PROJECT_AURORA"},{"type":"entity_node","value":"PROJECT_NOVA"}],"action":"mask"}',
     false, 'dev-user', 'english')
ON CONFLICT DO NOTHING;

-- ── KBExtractionPolicy ────────────────────────────────────────────────────────

INSERT INTO "KBExtractionPolicy"
    (policy_id, tenant_id, policy_name, policy_type, custom_override, created_by, language)
VALUES
    ('92000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001',
     'Global Entity Extraction Policy',
     'entity_node',
     'Extract named entities including ORG, PERSON, PRODUCT, REGULATION, and MONEY. Exclude stopwords and generic pronouns. Normalise dates to ISO-8601.',
     'dev-user', 'english'),

    ('92000000-0000-0000-0000-000000000002',
     '00000000-0000-0000-0000-000000000001',
     'Relationship Edge Extraction Policy',
     'relationship_edge',
     'Capture directed relationships: WORKS_FOR, REPORTS_TO, PRODUCED_BY, COMPLIANT_WITH, SUPERSEDES. Minimum confidence threshold 0.72.',
     'dev-user', 'english')
ON CONFLICT DO NOTHING;

-- ── KBConflictBatch + KBConflict ──────────────────────────────────────────────

INSERT INTO "KBConflictBatch"
    (batch_id, tenant_id, batch_title, status)
VALUES
    ('a1000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001',
     'Ingestion Conflicts — ERM Framework Refresh', 'pending'),

    ('a1000000-0000-0000-0000-000000000002',
     '00000000-0000-0000-0000-000000000001',
     'Deduplication Sweep — Customer Policy Docs', 'awaiting')
ON CONFLICT DO NOTHING;

INSERT INTO "KBConflict"
    (conflict_id, tenant_id, batch_id, conflict_type, severity, status,
     detailed_explanation, existing_snapshot, incoming_snapshot)
VALUES
    -- Pending batch, conflict 1
    ('a2000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001',
     'a1000000-0000-0000-0000-000000000001',
     'content_contradiction', 'high', 'pending',
     'Two chunks assert conflicting retention periods for financial records: existing states 7 years, incoming states 5 years per updated IFRS guidance.',
     '{"chunk_id":"72000000-0000-0000-0000-000000000006","text":"Financial records must be retained for a minimum of 7 years per internal policy."}',
     '{"source":"ERM Framework 2024 v2","text":"Financial records retention period updated to 5 years in line with revised IFRS 9 guidance."}'),

    -- Pending batch, conflict 2
    ('a2000000-0000-0000-0000-000000000002',
     '00000000-0000-0000-0000-000000000001',
     'a1000000-0000-0000-0000-000000000001',
     'content_update', 'medium', 'pending',
     'Risk appetite statement has been updated with more specific quantitative thresholds in the incoming document.',
     '{"chunk_id":"72000000-0000-0000-0000-000000000002","text":"Risk tolerance is defined qualitatively across four domains."}',
     '{"source":"ERM Framework 2024 v2","text":"Risk tolerance thresholds: credit VaR <2.5% equity, operational losses <0.3% revenue, liquidity coverage ratio >110%."}'),

    -- Awaiting batch, conflict 1 (merge in progress)
    ('a2000000-0000-0000-0000-000000000003',
     '00000000-0000-0000-0000-000000000001',
     'a1000000-0000-0000-0000-000000000002',
     'content_duplicate', 'low', 'awaiting',
     'Substantially identical SLA definition appears in both the Customer Support Policy Manual and the API Integration Guide.',
     '{"chunk_id":"71000000-0000-0000-0000-000000000001","text":"P1 issues require escalation to the on-call engineer within 30 minutes."}',
     '{"chunk_id":"71000000-0000-0000-0000-000000000004","text":"Critical API failures must be escalated to on-call within 30 minutes of detection."}'),

    -- Resolved conflict (standalone, no batch)
    ('a2000000-0000-0000-0000-000000000004',
     '00000000-0000-0000-0000-000000000001',
     NULL,
     'content_conflict', 'medium', 'resolved',
     'Conflicting approval thresholds for purchase orders resolved by keeping the more restrictive existing policy.',
     '{"text":"Purchase orders above $10,000 require CFO approval."}',
     '{"text":"Purchase orders above $25,000 require CFO approval."}')
ON CONFLICT DO NOTHING;

-- Mark resolved conflict
UPDATE "KBConflict"
SET    resolved_by = 'dev-user',
       resolved_at = NOW() - INTERVAL '2 days',
       resolution_method = 'keep_existing'
WHERE  conflict_id = 'a2000000-0000-0000-0000-000000000004';

-- ── KBQdrantConnection + KBQdrantCollection ───────────────────────────────────

INSERT INTO "KBQdrantConnection"
    (connection_id, tenant_id, is_active, total_collection)
VALUES
    ('b1000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001',
     true, 2)
ON CONFLICT DO NOTHING;

INSERT INTO "KBQdrantCollection"
    (collection_id, connection_id, collection_name, is_active,
     similarity_metric, points_count, vector_dimension, embedding_model_id)
VALUES
    ('b2000000-0000-0000-0000-000000000001',
     'b1000000-0000-0000-0000-000000000001',
     'kb_bge_large_1024',
     true, 'cosine', 487, 1024,
     '00000000-0000-0000-0000-000000000001'),

    ('b2000000-0000-0000-0000-000000000002',
     'b1000000-0000-0000-0000-000000000001',
     'kb_bge_small_384',
     false, 'cosine', 487, 384,
     '00000000-0000-0000-0000-000000000002')
ON CONFLICT DO NOTHING;

-- ── KBNeo4jConnection + KBNeo4jNode + KBNeo4jRelationship ────────────────────

INSERT INTO "KBNeo4jConnection"
    (connection_id, tenant_id, is_connected, total_node, total_edge, embedding_model_id)
VALUES
    ('c1000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001',
     true, 6, 7,
     '00000000-0000-0000-0000-000000000002')
ON CONFLICT DO NOTHING;

INSERT INTO "KBNeo4jNode" (node_id, connection_id, node_name, node_description) VALUES
    ('c2000000-0000-0000-0000-000000000001',
     'c1000000-0000-0000-0000-000000000001',
     'Operational Risk',
     'Risk domain covering internal process failures, people errors, and system outages.'),

    ('c2000000-0000-0000-0000-000000000002',
     'c1000000-0000-0000-0000-000000000001',
     'Financial Risk',
     'Risk domain covering market volatility, credit defaults, and liquidity constraints.'),

    ('c2000000-0000-0000-0000-000000000003',
     'c1000000-0000-0000-0000-000000000001',
     'Enterprise Risk Management',
     'Overarching governance framework for identifying, assessing, and mitigating risks.'),

    ('c2000000-0000-0000-0000-000000000004',
     'c1000000-0000-0000-0000-000000000001',
     'CRO',
     'Chief Risk Officer — executive accountable for the ERM programme.'),

    ('c2000000-0000-0000-0000-000000000005',
     'c1000000-0000-0000-0000-000000000001',
     'Risk Register',
     'Living document cataloguing identified risks with likelihood, impact, and mitigation status.'),

    ('c2000000-0000-0000-0000-000000000006',
     'c1000000-0000-0000-0000-000000000001',
     'IFRS 9',
     'International financial reporting standard governing financial instruments and expected credit loss.')
ON CONFLICT DO NOTHING;

INSERT INTO "KBNeo4jRelationship" (from_node, to_node, score, description) VALUES
    ('c2000000-0000-0000-0000-000000000003',
     'c2000000-0000-0000-0000-000000000001',
     0.91, 'ENCOMPASSES'),

    ('c2000000-0000-0000-0000-000000000003',
     'c2000000-0000-0000-0000-000000000002',
     0.89, 'ENCOMPASSES'),

    ('c2000000-0000-0000-0000-000000000004',
     'c2000000-0000-0000-0000-000000000003',
     0.95, 'OVERSEES'),

    ('c2000000-0000-0000-0000-000000000004',
     'c2000000-0000-0000-0000-000000000005',
     0.88, 'MAINTAINS'),

    ('c2000000-0000-0000-0000-000000000002',
     'c2000000-0000-0000-0000-000000000006',
     0.82, 'GOVERNED_BY'),

    ('c2000000-0000-0000-0000-000000000001',
     'c2000000-0000-0000-0000-000000000005',
     0.85, 'TRACKED_IN'),

    ('c2000000-0000-0000-0000-000000000003',
     'c2000000-0000-0000-0000-000000000005',
     0.93, 'PRODUCES')
ON CONFLICT DO NOTHING;

-- ── KBEntityLookup ────────────────────────────────────────────────────────────

-- alias_name and canonical_name are stored in canonical form: lowercase,
-- underscores, Unicode (Vietnamese diacritics) preserved. tenant_id NULL =
-- global alias visible to every tenant.
INSERT INTO "KBEntityLookup" (alias_name, canonical_name) VALUES
    ('cfo',  'chief_financial_officer'),
    ('cro',  'chief_risk_officer'),
    ('erm',  'enterprise_risk_management'),
    ('sla',  'service_level_agreement'),
    ('ifrs', 'international_financial_reporting_standards'),
    -- Vietnamese synonym seeds (same entity, zero shared tokens)
    ('tp_hcm',   'thành_phố_hồ_chí_minh'),
    ('tphcm',    'thành_phố_hồ_chí_minh'),
    ('sài_gòn',  'thành_phố_hồ_chí_minh'),
    ('mỹ',       'hoa_kỳ'),
    ('nhnn',     'ngân_hàng_nhà_nước_việt_nam')
ON CONFLICT DO NOTHING;

-- ── KBPublishAPI ──────────────────────────────────────────────────────────────

INSERT INTO "KBPublishAPI"
    (id, tenant_id, name, type, endpoint_url, http_method, is_published)
VALUES
    ('d1000000-0000-0000-0000-000000000001',
     '00000000-0000-0000-0000-000000000001',
     'KB Semantic Search',
     'QDRANT',
     'http://qdrant:6333/collections/kb_bge_large_1024/points/search',
     'POST', true),

    ('d1000000-0000-0000-0000-000000000002',
     '00000000-0000-0000-0000-000000000001',
     'KB Graph Query',
     'NEO4J',
     'bolt://neo4j:7687',
     'POST', true),

    ('d1000000-0000-0000-0000-000000000003',
     '00000000-0000-0000-0000-000000000001',
     'KB RAG Retrieval',
     'RETRIEVE',
     'http://kb-backend:8000/api/retrieve',
     'POST', false)
ON CONFLICT DO NOTHING;
