-- E2E seed — one agent that answers from a demo warehouse through the semantic
-- layer, so the whole "hỏi tiếng Việt → chỉ số đã duyệt → SQL qua guard →
-- __evidence" chain runs on real services.
--
--   docker exec -i dataagent-postgres psql -U admin -d dataagent -v ON_ERROR_STOP=1 < tests/e2e/seed.sql
--
-- Idempotent: every statement is INSERT … ON CONFLICT / IF NOT EXISTS, and the
-- demo fact table is rebuilt in full so the numbers are reproducible.
--
-- IDs are fixed (0000000e-2e00-…) so tests and scripts can refer to them:
--   agent          …0001   version …0002   workflow …0003   workflow version …0004
--   DB secret      …0005   metrics …0006/…0008 (versions …0007/…0009)

\set tenant '''a0000000-0000-0000-0000-000000000001'''

BEGIN;

-- ── LLM provider: "openai/responder" → litellm wildcard → host ollama ─────────
INSERT INTO "LLMProviders"(id, name, model_id, endpoint_url, type, is_default, is_active)
VALUES ('00000000-0000-0000-0000-0000000000a1', 'local-ollama', 'openai/responder',
        'http://litellm:4000', 'openai', true, true)
ON CONFLICT (id) DO NOTHING;

-- workflow_runtime connects as runtime_writer; a fresh DB gives it no SELECT.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO runtime_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO runtime_writer;

-- ── Agent + workflow (flow nodes live in Mongo — see seed-flow.js) ───────────
INSERT INTO "Agents"(id, tenant_id, name, is_active)
VALUES ('0000000e-2e00-4000-8000-000000000001', :tenant, 'e2e-sql-agent', true)
ON CONFLICT (id) DO NOTHING;

INSERT INTO "Workflows"(id, agent_id, name, description)
VALUES ('0000000e-2e00-4000-8000-000000000003', '0000000e-2e00-4000-8000-000000000001',
        'e2e-sql-flow', 'planner → db_connector → rrf → reranker → responder/visualization → judge')
ON CONFLICT (id) DO NOTHING;

INSERT INTO "WorkflowVersions"(id, workflow_id, version, status, changelog)
VALUES ('0000000e-2e00-4000-8000-000000000004', '0000000e-2e00-4000-8000-000000000003',
        1, 'published', 'e2e seed')
ON CONFLICT (id) DO NOTHING;

INSERT INTO "AgentVersions"(id, agent_id, version, status, responder_model_id,
                            workflow_version_id, llm_config, retrieval_config, memory_enabled)
VALUES ('0000000e-2e00-4000-8000-000000000002', '0000000e-2e00-4000-8000-000000000001',
        1, 'published', '00000000-0000-0000-0000-0000000000a1',
        '0000000e-2e00-4000-8000-000000000004', '{}'::jsonb, '{}'::jsonb, false)
ON CONFLICT (id) DO NOTHING;

UPDATE "Agents" SET published_version_id = '0000000e-2e00-4000-8000-000000000002'
WHERE id = '0000000e-2e00-4000-8000-000000000001';

-- ── DB connector password: metadata row here, value in OpenBao (e2e.sh setup) ─
-- workflow_runtime resolves dbConnections[].secretId → secrets_vault → OpenBao
-- KV at secret/data/tenants/<tenant>/<key_name>. Never plaintext in the flow.
INSERT INTO secrets_vault(id, tenant_id, key_name, key_type, algorithm, realm,
                          openbao_path, version, is_active, created_by)
VALUES ('0000000e-2e00-4000-8000-000000000005', :tenant, 'e2e_dw_password',
        'DB_CONNECTOR_PASSWORD', NULL, NULL,
        'secret/data/tenants/a0000000-0000-0000-0000-000000000001/e2e_dw_password',
        1, true, 'e2e-seed')
ON CONFLICT (id) DO UPDATE SET is_active = true, key_type = EXCLUDED.key_type;

-- ── Semantic layer: two revenue metrics so "doanh thu" is genuinely ambiguous ─
-- doanh_thu_gop  = SUM(amount)                    synonyms: doanh thu, doanh thu gộp
-- doanh_thu_thuan = SUM(net_amount)  [= amount - returned_amount]  synonyms: doanh thu thuần, net revenue
INSERT INTO "SemanticMetric"(id, tenant_id, metric_key, name, description, owner)
VALUES
  ('0000000e-2e00-4000-8000-000000000006', :tenant, 'doanh_thu_gop',
   'Doanh thu gộp', 'Tổng giá trị đơn đã hoàn tất, chưa trừ hàng trả lại', 'Phòng Kinh doanh'),
  ('0000000e-2e00-4000-8000-000000000008', :tenant, 'doanh_thu_thuan',
   'Doanh thu thuần', 'Giá trị đơn đã hoàn tất sau khi trừ hàng trả lại', 'Phòng Tài chính')
ON CONFLICT (tenant_id, metric_key) DO NOTHING;

INSERT INTO "SemanticMetricVersion"(id, tenant_id, metric_id, version, status, source_table,
    aggregation, expression, time_column, default_grain, unit, dimensions, default_filters,
    approved_by, approved_at, change_note)
VALUES
  ('0000000e-2e00-4000-8000-000000000007', :tenant, '0000000e-2e00-4000-8000-000000000006',
   1, 'approved', 'fact_sales', 'sum', 'amount', 'order_date', 'month', 'VND',
   '[{"key":"vùng","column":"region","label":"Vùng"},{"key":"miền","column":"region","label":"Vùng"},{"key":"region","column":"region","label":"Region"}]'::jsonb,
   '[{"column":"status","op":"eq","value":"done"}]'::jsonb,
   'e2e-seed', now(), 'e2e seed'),
  ('0000000e-2e00-4000-8000-000000000009', :tenant, '0000000e-2e00-4000-8000-000000000008',
   1, 'approved', 'fact_sales', 'sum', 'net_amount', 'order_date', 'month', 'VND',
   '[{"key":"vùng","column":"region","label":"Vùng"},{"key":"miền","column":"region","label":"Vùng"},{"key":"region","column":"region","label":"Region"}]'::jsonb,
   '[{"column":"status","op":"eq","value":"done"}]'::jsonb,
   'e2e-seed', now(), 'e2e seed')
ON CONFLICT (metric_id, version) DO UPDATE SET
    status = EXCLUDED.status, source_table = EXCLUDED.source_table, aggregation = EXCLUDED.aggregation,
    expression = EXCLUDED.expression, time_column = EXCLUDED.time_column, default_grain = EXCLUDED.default_grain,
    unit = EXCLUDED.unit, dimensions = EXCLUDED.dimensions, default_filters = EXCLUDED.default_filters;

-- term_normalized = backend/libs/semantic/resolver.normalize(): casefold, punctuation
-- stripped, whitespace collapsed, diacritics KEPT.
INSERT INTO "SemanticSynonym"(tenant_id, metric_id, term, term_normalized, locale)
VALUES
  (:tenant, '0000000e-2e00-4000-8000-000000000006', 'doanh thu',        'doanh thu',        'vi'),
  (:tenant, '0000000e-2e00-4000-8000-000000000006', 'doanh thu gộp',    'doanh thu gộp',    'vi'),
  (:tenant, '0000000e-2e00-4000-8000-000000000006', 'gross revenue',    'gross revenue',    'en'),
  (:tenant, '0000000e-2e00-4000-8000-000000000008', 'doanh thu thuần',  'doanh thu thuần',  'vi'),
  (:tenant, '0000000e-2e00-4000-8000-000000000008', 'doanh thu ròng',   'doanh thu ròng',   'vi'),
  (:tenant, '0000000e-2e00-4000-8000-000000000008', 'net revenue',      'net revenue',      'en')
ON CONFLICT (tenant_id, term_normalized) DO NOTHING;

COMMIT;

-- ── Demo warehouse (sandbox_dev — the read-only sandbox_reader role) ─────────
\connect sandbox_dev

-- A metric contract aggregates ONE column (backend/libs/semantic validates the
-- expression as an identifier), so "net" is a generated column, not an
-- expression in the contract — exactly how a warehouse would model it.
DROP TABLE IF EXISTS fact_sales;
CREATE TABLE fact_sales (
    order_id        serial PRIMARY KEY,
    region          text          NOT NULL,
    product         text          NOT NULL,
    amount          numeric(14,2) NOT NULL,
    returned_amount numeric(14,2) NOT NULL DEFAULT 0,
    net_amount      numeric(14,2) GENERATED ALWAYS AS (amount - returned_amount) STORED,
    status          text          NOT NULL DEFAULT 'done',
    order_date      date          NOT NULL
);

-- 8 months up to today, four regions, three products, deterministic amounts (no random()).
INSERT INTO fact_sales(region, product, amount, returned_amount, status, order_date)
SELECT
    r.region,
    p.product,
    (1000000 + ((extract(day from d)::int * 37 + r.w * 91 + p.w * 53) % 900000))::numeric(14,2)       AS amount,
    CASE WHEN (extract(day from d)::int + r.w) % 7 = 0
         THEN (50000 + (extract(day from d)::int * 11 % 40000))::numeric(14,2) ELSE 0 END               AS returned_amount,
    CASE WHEN (extract(day from d)::int + p.w) % 13 = 0 THEN 'cancelled' ELSE 'done' END                 AS status,
    d::date
FROM generate_series(date_trunc('month', now()) - interval '7 months', now(), interval '1 day') AS d
CROSS JOIN (VALUES ('Miền Bắc', 1), ('Miền Nam', 2), ('Miền Trung', 3), ('Tây Nguyên', 4)) AS r(region, w)
CROSS JOIN (VALUES ('Áo', 1), ('Quần', 2), ('Giày', 3)) AS p(product, w);

GRANT USAGE ON SCHEMA public TO sandbox_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sandbox_reader;

SELECT count(*) AS fact_sales_rows, min(order_date) AS from_date, max(order_date) AS to_date FROM fact_sales;
