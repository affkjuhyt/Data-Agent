-- Semantic Layer — worked example.
--
-- Two metrics that differ ONLY in their default filter. Before the Semantic
-- Layer these were the same question to the agent, and "doanh thu" returned
-- gross or net depending on what the model happened to write that turn.
--
-- Run against the app database AFTER `alembic upgrade head`, replacing the
-- tenant id with your own:
--   psql -U admin -d dataagent -v tenant="'a0000000-0000-0000-0000-000000000001'" \
--        -f infra/sql/seed-semantic-example.sql
--
-- The example assumes an `orders(amount numeric, status text, customer_id uuid,
-- transaction_date date, region_name text)` table on the DB Connector's target
-- database. Adjust source_table/expression/time_column to your own schema —
-- these are physical names and nothing validates them against the warehouse
-- until a question actually runs (see "What is still missing", below).

\set tenant :tenant

BEGIN;

-- ── 1. Gross revenue ───────────────────────────────────────────────────────
INSERT INTO "SemanticMetric" (tenant_id, metric_key, name, description, owner)
VALUES (:tenant::uuid, 'revenue_gross', 'Doanh thu gộp',
        'Tổng giá trị đơn hàng trước khi trừ hoàn/huỷ.', 'finance@example.com')
ON CONFLICT (tenant_id, metric_key) DO NOTHING;

INSERT INTO "SemanticMetricVersion" (
    tenant_id, metric_id, version, status, source_table, aggregation, expression,
    time_column, default_grain, unit, dimensions, default_filters,
    approved_by, approved_at, change_note)
SELECT :tenant::uuid, m.id, 1, 'approved', 'orders', 'sum', 'amount',
       'transaction_date', 'none', 'VND',
       '[{"key": "miền", "column": "region_name", "label": "Miền"}]'::jsonb,
       '[]'::jsonb,
       'finance@example.com', now(), 'Initial definition.'
FROM "SemanticMetric" m
WHERE m.tenant_id = :tenant::uuid AND m.metric_key = 'revenue_gross'
ON CONFLICT (metric_id, version) DO NOTHING;

-- ── 2. Net revenue — same table, same column, one filter apart ─────────────
INSERT INTO "SemanticMetric" (tenant_id, metric_key, name, description, owner)
VALUES (:tenant::uuid, 'revenue_net', 'Doanh thu thuần',
        'Doanh thu sau khi loại đơn hoàn tiền và đơn huỷ.', 'finance@example.com')
ON CONFLICT (tenant_id, metric_key) DO NOTHING;

INSERT INTO "SemanticMetricVersion" (
    tenant_id, metric_id, version, status, source_table, aggregation, expression,
    time_column, default_grain, unit, dimensions, default_filters,
    approved_by, approved_at, change_note)
SELECT :tenant::uuid, m.id, 1, 'approved', 'orders', 'sum', 'amount',
       'transaction_date', 'none', 'VND',
       '[{"key": "miền", "column": "region_name", "label": "Miền"}]'::jsonb,
       -- THE distinction. Applied on every question that resolves here,
       -- whether or not the asker thought to mention refunds.
       '[{"column": "status", "op": "not_in", "value": ["refunded", "cancelled"]}]'::jsonb,
       'finance@example.com', now(), 'Initial definition.'
FROM "SemanticMetric" m
WHERE m.tenant_id = :tenant::uuid AND m.metric_key = 'revenue_net'
ON CONFLICT (metric_id, version) DO NOTHING;

-- ── 3. Order count vs customer count — the other classic confusion ─────────
INSERT INTO "SemanticMetric" (tenant_id, metric_key, name, description, owner)
VALUES (:tenant::uuid, 'order_count', 'Số đơn hàng',
        'Số dòng đơn hàng.', 'ops@example.com')
ON CONFLICT (tenant_id, metric_key) DO NOTHING;

INSERT INTO "SemanticMetricVersion" (
    tenant_id, metric_id, version, status, source_table, aggregation, expression,
    time_column, default_grain, unit, dimensions, default_filters,
    approved_by, approved_at, change_note)
SELECT :tenant::uuid, m.id, 1, 'approved', 'orders', 'count', '*',
       'transaction_date', 'none', 'đơn', '[]'::jsonb, '[]'::jsonb,
       'ops@example.com', now(), 'Initial definition.'
FROM "SemanticMetric" m
WHERE m.tenant_id = :tenant::uuid AND m.metric_key = 'order_count'
ON CONFLICT (metric_id, version) DO NOTHING;

INSERT INTO "SemanticMetric" (tenant_id, metric_key, name, description, owner)
VALUES (:tenant::uuid, 'customer_count', 'Số khách hàng',
        'Số khách hàng duy nhất có phát sinh đơn.', 'ops@example.com')
ON CONFLICT (tenant_id, metric_key) DO NOTHING;

INSERT INTO "SemanticMetricVersion" (
    tenant_id, metric_id, version, status, source_table, aggregation, expression,
    time_column, default_grain, unit, dimensions, default_filters,
    approved_by, approved_at, change_note)
SELECT :tenant::uuid, m.id, 1, 'approved', 'orders', 'count_distinct', 'customer_id',
       'transaction_date', 'none', 'khách', '[]'::jsonb, '[]'::jsonb,
       'ops@example.com', now(), 'Initial definition.'
FROM "SemanticMetric" m
WHERE m.tenant_id = :tenant::uuid AND m.metric_key = 'customer_count'
ON CONFLICT (metric_id, version) DO NOTHING;

-- ── 4. Synonyms — every phrasing that should reach each metric ─────────────
-- term_normalized must equal libs.semantic.resolver.normalize(term): casefolded,
-- punctuation stripped, whitespace collapsed, Vietnamese tones KEPT. A toneless
-- spelling is a separate row on purpose — folding tones automatically would let
-- one metric answer for another.
INSERT INTO "SemanticSynonym" (tenant_id, metric_id, term, term_normalized, locale)
SELECT :tenant::uuid, m.id, t.term, t.norm, t.locale
FROM "SemanticMetric" m
JOIN (VALUES
    ('revenue_gross',  'doanh thu',            'doanh thu',            'vi'),
    ('revenue_gross',  'doanh số',             'doanh số',             'vi'),
    ('revenue_gross',  'revenue',              'revenue',              'en'),
    -- Longer phrases win over shorter ones, so these never collide with the
    -- gross terms above (resolver sorts by length, descending).
    ('revenue_net',    'doanh thu thuần',      'doanh thu thuần',      'vi'),
    ('revenue_net',    'doanh thu thuan',      'doanh thu thuan',      'vi'),
    ('revenue_net',    'net revenue',          'net revenue',          'en'),
    ('order_count',    'số đơn hàng',          'số đơn hàng',          'vi'),
    ('order_count',    'số đơn',               'số đơn',               'vi'),
    ('customer_count', 'số khách hàng',        'số khách hàng',        'vi'),
    ('customer_count', 'bao nhiêu khách hàng', 'bao nhiêu khách hàng', 'vi')
) AS t(metric_key, term, norm, locale) ON t.metric_key = m.metric_key
WHERE m.tenant_id = :tenant::uuid
ON CONFLICT (tenant_id, term_normalized) DO NOTHING;

COMMIT;

-- Verify:
--   SELECT m.metric_key, v.aggregation, v.expression, v.default_filters
--   FROM "SemanticMetric" m
--   JOIN "SemanticMetricVersion" v ON v.metric_id = m.id AND v.status = 'approved';
--
-- Then ask the agent "doanh thu thuần tháng 6" and check the answer's metadata:
-- `generated_by` should be "semantic_layer" and `semantic.metric` "revenue_net@1".
