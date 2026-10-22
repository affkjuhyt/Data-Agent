-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║  Platform seed data (legacy tables still read by kept services)          ║
-- ╚══════════════════════════════════════════════════════════════════════════╝
--
-- Runs after grant-roles.sql at Postgres first-init (db-init Job). Only the rows the
-- services that remain read: the dev tenant (auth-api, platform-workers), the embedding
-- models (model-service) and the demo warehouse (the schema crawler). The knowledge-base
-- rows went with kb-backend (KB-08); the V3 seeds are the v3-*.sql files.
--
-- Every INSERT is ON CONFLICT DO NOTHING so re-running is a no-op.

-- ── Tenants ──────────────────────────────────────────────────────────────────

INSERT INTO "Tenants" (id, name, slug, is_active) VALUES
    ('a0000000-0000-0000-0000-000000000001', 'Dev Tenant', 'dev', true)
ON CONFLICT (id) DO NOTHING;

-- ── KBModel (embedding models model_service registers) ───────────────────────

-- model_name MUST match the names model_service registers (backends/sync.py), so its
-- name→UUID resolution binds these seed UUIDs instead of inserting fresh ones.
-- id …0001 = the 1024-dim model, …0002 = the smaller.
INSERT INTO "KBModel" (model_id, model_name, task_type) VALUES
    ('00000000-0000-0000-0000-000000000001', 'AITeamVN/Vietnamese_Embedding', 'embedding'),
    ('00000000-0000-0000-0000-000000000002', 'intfloat/multilingual-e5-small', 'embedding')
ON CONFLICT (model_id) DO NOTHING;

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
