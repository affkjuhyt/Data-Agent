-- Screen-audit fixtures for DS-AW: one (or few) rows so empty / no_seed_id screens open.
-- Complements v3-agent-seed.sql. Idempotent. Figures and names stay on AdventureWorks themes.
-- Ids: a0000000-…-000000aa1cNN / aa1dNN / aa1eNN.

\set org '''a0000000-0000-0000-0000-000000000001'''
\set engineer '''a0000000-0000-0000-0000-000000000022'''
\set space '''a0000000-0000-0000-0000-000000000a01'''

-- ── Cần bạn: proposal + HITL (and the checkpoint hit already pointed at aa0c82) ──

INSERT INTO inbox.proposal (
    id, org_id, data_space_id, kind, target_service, target_ref, title, diff, reason,
    evidence, impact, impact_level, proposed_by, assignee_id, status, expires_at,
    created_by, updated_by
)
SELECT
    'a0000000-0000-0000-0000-000000aa1c01', :org::uuid, :space::uuid,
    'metric_change', 'context', NULL,
    'Đổi mô tả biên gộp Internet (seed)',
    '{"from": "Biên = (doanh thu - giá vốn) / doanh thu", "to": "Biên có trọng số doanh thu theo nhóm"}'::jsonb,
    'Khớp định nghĩa AdventureWorksDW năm 2013.',
    '[]'::jsonb, '{"agents": 1}'::jsonb, 'medium',
    :engineer::uuid, a.id, 'pending_review', now() + interval '20 days',
    :engineer::uuid, :engineer::uuid
FROM org.app_user a
WHERE a.org_id = :org::uuid AND a.username = 'helios-admin'
ON CONFLICT (id) DO UPDATE SET
    title = EXCLUDED.title, status = EXCLUDED.status, expires_at = EXCLUDED.expires_at;

-- Assignee = engineer so /needs-you and /v1/inbox show content for the primary
-- screen-audit account (inbox is filtered by assignee).
INSERT INTO inbox.hitl_request (
    id, org_id, data_space_id, kind, risk, title, context, options,
    source_service, source_ref, assignee_id, assignee_capability, due_at, status,
    created_by, updated_by
)
SELECT v.id, :org::uuid, :space::uuid, v.kind, v.risk, v.title, v.context::jsonb, v.options::jsonb,
       v.source_service, v.source_ref::uuid, :engineer::uuid, v.assignee_capability,
       now() + v.due, 'open', :engineer::uuid, :engineer::uuid
FROM (VALUES
    ('a0000000-0000-0000-0000-000000aa0c82'::uuid, 'checkpoint', 'medium',
     'Gửi kết quả biên gộp ra ngoài (seed)',
     '{"run_id": "a0000000-0000-0000-0000-000000aa0c62"}',
     '[{"id": "send", "label": "Gửi"}, {"id": "hold", "label": "Giữ lại"}]',
     'agent', 'a0000000-0000-0000-0000-000000aa0c62'::uuid, 'approve', interval '2 days'),
    ('a0000000-0000-0000-0000-000000aa1c02'::uuid, 'approve_proposal', 'medium',
     'Duyệt đổi mô tả biên gộp Internet (seed)',
     '{"proposal_id": "a0000000-0000-0000-0000-000000aa1c01"}',
     '[]', 'inbox', 'a0000000-0000-0000-0000-000000aa1c01'::uuid, 'approve', interval '5 days'),
    ('a0000000-0000-0000-0000-000000aa1c03'::uuid, 'conflict', 'high',
     'Hai nguồn doanh thu Internet lệch (seed)',
     '{"left": "fact_internet_sales", "right": "báo cáo quý", "delta_pct": 3.2}',
     '[{"id": "trust_dw", "label": "Tin kho"}, {"id": "trust_report", "label": "Tin báo cáo"}]',
     'conflict', NULL::uuid, 'steward', interval '3 days')
) AS v(id, kind, risk, title, context, options, source_service, source_ref, assignee_capability, due)
ON CONFLICT (id) DO UPDATE SET
    title = EXCLUDED.title, status = EXCLUDED.status, due_at = EXCLUDED.due_at,
    context = EXCLUDED.context, assignee_id = EXCLUDED.assignee_id;

INSERT INTO inbox.hitl_approval_step (
    id, org_id, hitl_request_id, step, approver_role, approver_id
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1c04', :org::uuid,
    'a0000000-0000-0000-0000-000000aa1c02', 1, 'approve', :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

-- ── Glossary / knowledge / period ──

INSERT INTO context.glossary_term (
    id, org_id, data_space_id, term, term_norm, definition, meanings, status,
    created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1d01', :org::uuid, :space::uuid,
    'Biên gộp', 'bien gop',
    'Tỷ lệ (doanh thu − giá vốn) / doanh thu trên fact_internet_sales.',
    '[{"label": "Internet", "note": "Theo nhóm sản phẩm"}]'::jsonb, 'active',
    :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET definition = EXCLUDED.definition, status = EXCLUDED.status;

-- active pages require current_version_id (knowledge_page_check1); insert draft →
-- version → activate in one deferred transaction.
BEGIN;
INSERT INTO context.knowledge_page (
    id, org_id, data_space_id, kind, subject_kind, subject_ref, slug, title,
    index_summary, tags, owner_id, status, latest_version_no, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1d02', :org::uuid, :space::uuid,
    'topic', 'topic', NULL, 'bien-gop-internet', 'Biên gộp Internet',
    'Định nghĩa và bẫy trung bình biên trên AdventureWorks.',
    ARRAY['margin', 'internet'], :engineer::uuid, 'draft', 0,
    :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, index_summary = EXCLUDED.index_summary;

INSERT INTO context.knowledge_page_version (
    id, org_id, page_id, version, origin, body, body_sha256, summary, created_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1d03', :org::uuid,
    'a0000000-0000-0000-0000-000000aa1d02', 1, 'manual',
    E'# Biên gộp Internet\n\n(doanh thu − giá vốn) / doanh thu trên fact_internet_sales.',
    encode(
        sha256(convert_to(
            E'# Biên gộp Internet\n\n(doanh thu − giá vốn) / doanh thu trên fact_internet_sales.',
            'UTF8'
        )),
        'hex'
    ),
    'Định nghĩa biên trên AdventureWorks.', :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

UPDATE context.knowledge_page
SET status = 'active',
    current_version_id = 'a0000000-0000-0000-0000-000000aa1d03',
    latest_version_no = GREATEST(latest_version_no, 1),
    updated_by = :engineer::uuid,
    updated_at = now()
WHERE id = 'a0000000-0000-0000-0000-000000aa1d02'
  AND (status <> 'active' OR current_version_id IS DISTINCT FROM 'a0000000-0000-0000-0000-000000aa1d03'::uuid);
COMMIT;

INSERT INTO period.period_calendar (
    id, org_id, data_space_id, period_kind, close_rule, dataset_ids,
    reconciliation_items, report_codes, status, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1d10', :org::uuid, :space::uuid,
    'month', 'Ngày làm việc thứ 3 của tháng sau', '{}'::uuid[],
    '[]'::jsonb, '{}'::text[], 'active', :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET close_rule = EXCLUDED.close_rule, status = EXCLUDED.status;

INSERT INTO period.period_close (
    id, org_id, data_space_id, period_calendar_id, period_label, business_date,
    version, status, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1d11', :org::uuid, :space::uuid,
    'a0000000-0000-0000-0000-000000aa1d10', '2013-12', DATE '2013-12-31',
    1, 'open', :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, period_label = EXCLUDED.period_label;

-- ── Crawl / pipeline / mart / product (dataset id optional when scan not run) ──

INSERT INTO acquire.crawl_source (
    id, org_id, data_space_id, name, goal, kind, seed_urls, allowed_domains,
    schedule_cron, legal_status, trust_level, status, owner_id, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1d20', :org::uuid, :space::uuid,
    'Bảng giá phụ tùng công khai (seed)',
    'Thu giá phụ tùng xe đạp từ trang demo công khai.',
    'web_page',
    '["https://example.com/adventureworks/parts"]'::jsonb,
    ARRAY['example.com'],
    '0 6 * * *', 'pending', 0.50, 'draft', :engineer::uuid, :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, status = EXCLUDED.status;

INSERT INTO pipeline.pipeline (
    id, org_id, data_space_id, name, owner_id, input_dataset_ids, steps, scope, status,
    created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1d30', :org::uuid, :space::uuid,
    'Làm sạch doanh thu Internet (seed)', :engineer::uuid, '{}'::uuid[],
    '[{"kind": "filter", "label": "Bỏ đơn hủy", "sql": "WHERE 1=1"}]'::jsonb,
    'data_space', 'draft', :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, status = EXCLUDED.status, steps = EXCLUDED.steps;

-- Data marts are not seeded: POST /v1/marts bootstraps dimensions from active
-- context.relationship rows of the chosen fact table (UC-06-06). The bad LIMIT-1
-- seed that pointed at dim_account was removed on 09/11/2026.
DELETE FROM pipeline.data_product WHERE id = 'a0000000-0000-0000-0000-000000aa1d32'::uuid;
DELETE FROM pipeline.data_mart WHERE id = 'a0000000-0000-0000-0000-000000aa1d31'::uuid;

-- ── Monitor / alert / report / digest (needs an active metric when semantic-import ran) ──

INSERT INTO monitor.monitor (
    id, org_id, data_space_id, owner_id, metric_id, scope_filter, rule_kind, rule,
    schedule_cron, notify_mode, recipients, status, created_by, updated_by
)
SELECT
    'a0000000-0000-0000-0000-000000aa1e01'::uuid, :org::uuid, :space::uuid, :engineer::uuid,
    m.id, '{}'::jsonb, 'threshold', '{"op": "<", "value": 0.40}'::jsonb,
    '0 8 * * *', 'alert_only',
    jsonb_build_array(jsonb_build_object('user_id', :engineer::uuid::text, 'channel', 'in_app')),
    'active', :engineer::uuid, :engineer::uuid
FROM context.metric m
WHERE m.data_space_id = :space::uuid AND m.status = 'active'
ORDER BY m.code
LIMIT 1
ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, rule = EXCLUDED.rule;

INSERT INTO monitor.alert (
    id, org_id, monitor_id, period_label, value, threshold_value, summary, dedup_key,
    kind, status, created_by, updated_by
)
SELECT
    'a0000000-0000-0000-0000-000000aa1e02'::uuid, :org::uuid, mon.id,
    '2013-12', 0.382, 0.40,
    'Biên gộp Internet tháng 12/2013 dưới ngưỡng 40%.',
    'seed:margin:2013-12', 'metric', 'open', :engineer::uuid, :engineer::uuid
FROM monitor.monitor mon
WHERE mon.id = 'a0000000-0000-0000-0000-000000aa1e01'
ON CONFLICT (id) DO UPDATE SET summary = EXCLUDED.summary, status = EXCLUDED.status;

INSERT INTO monitor.report_schedule (
    id, org_id, data_space_id, kind, owner_id, title, cron, as_of_rule, formats, status,
    created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1e10', :org::uuid, :space::uuid, 'digest',
    :engineer::uuid, 'Bản tin biên gộp tuần (seed)', '0 9 * * 1', 'end_of_previous_week',
    ARRAY['html'], 'active', :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title, status = EXCLUDED.status;

INSERT INTO monitor.report_run (
    id, org_id, report_schedule_id, kind, as_of, trigger, status, finished_at
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1e11', :org::uuid,
    'a0000000-0000-0000-0000-000000aa1e10', 'digest', CURRENT_DATE, 'run_now', 'sent', now()
)
ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, finished_at = EXCLUDED.finished_at;

INSERT INTO notify.notification (
    id, org_id, user_id, data_space_id, kind, title, body, link, source_ref, priority, created_at
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1e12', :org::uuid, :engineer::uuid, :space::uuid,
    'digest', 'Bản tin biên gộp tuần (seed)', '06/10 · Agent Báo cáo',
    '/analysis/digest/a0000000-0000-0000-0000-000000aa1e11',
    'a0000000-0000-0000-0000-000000aa1e11', 'normal', now()
)
ON CONFLICT (id, created_at) DO NOTHING;

-- ── Access request (viewer asks for view_detail) + extraction schema on DS-AW collection ──

INSERT INTO org.access_request (
    id, org_id, requester_id, data_space_id, capability, reason, requested_until, status
)
SELECT
    'a0000000-0000-0000-0000-000000aa1e20'::uuid, :org::uuid, u.id, :space::uuid,
    'view_detail', 'Screen-audit: xem chi tiết DS-AW.',
    (CURRENT_DATE + 30), 'pending'
FROM org.app_user u
WHERE u.org_id = :org::uuid AND u.username = 'helios-viewer'
ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, reason = EXCLUDED.reason;

INSERT INTO document.extraction_schema (
    id, org_id, collection_id, version, fields, status, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1e30', :org::uuid,
    'a0000000-0000-0000-0000-0000d1001000', 1,
    '[{"name": "metric_code", "type": "text", "required": true}]'::jsonb,
    'draft', :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET fields = EXCLUDED.fields, status = EXCLUDED.status;

-- Quality rule when a dataset exists (QUALITY list).
INSERT INTO ingest.quality_rule (
    id, org_id, data_space_id, dataset_id, column_name, kind, params, severity, status,
    created_by, updated_by
)
SELECT
    'a0000000-0000-0000-0000-000000aa1e40'::uuid, :org::uuid, :space::uuid, d.id,
    'sales_amount', 'range', '{"min": 0}'::jsonb, 'medium', 'active',
    :engineer::uuid, :engineer::uuid
FROM catalog.dataset d
WHERE d.data_space_id = :space::uuid AND d.deleted_at IS NULL
ORDER BY d.table_name
LIMIT 1
ON CONFLICT (id) DO UPDATE SET params = EXCLUDED.params, status = EXCLUDED.status;

-- ── Forecast / crawl-run / context data product / report / policy / error book ──
-- Clears the remaining easy empty + no_seed_id rows from screen-audit.
-- Approver / steward = helios-admin (≠ engineer) for two-people checks.

INSERT INTO forecast.forecast_model (
    id, org_id, data_space_id, metric_code, scope, frequency, owner_id, status,
    created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1f01', :org::uuid, :space::uuid,
    'DOANH_THU_INTERNET', '{}'::jsonb, 'month', :engineer::uuid, 'active',
    :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO forecast.model_version (
    id, org_id, model_id, version, algorithm, features, training_from, training_to,
    n_points, n_exog, status, model_card, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1f02', :org::uuid,
    'a0000000-0000-0000-0000-000000aa1f01', 1, 'ets',
    '[{"name": "season", "future_assumption": "declared"}]'::jsonb,
    DATE '2011-01-01', DATE '2013-12-01', 36, 0, 'draft',
    '{"target": "DOANH_THU_INTERNET", "limits": "chuỗi tháng AdventureWorks"}'::jsonb,
    :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO forecast.backtest_run (
    id, org_id, model_version_id, frequency, cutoffs, horizon, metrics, baseline_metrics,
    mase, in_sample_mase, started_at, finished_at
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1f03', :org::uuid,
    'a0000000-0000-0000-0000-000000aa1f02', 'month',
    ARRAY[
        DATE '2013-01-01', DATE '2013-02-01', DATE '2013-03-01',
        DATE '2013-04-01', DATE '2013-05-01', DATE '2013-06-01'
    ],
    3, '{"h1": {"mase": 0.85}}'::jsonb, '{"h1": {"mase": 1}}'::jsonb,
    0.85, 0.70, now() - interval '2 hours', now() - interval '1 hour'
)
ON CONFLICT (id) DO NOTHING;

UPDATE forecast.model_version
SET status = 'pending_approval'
WHERE id = 'a0000000-0000-0000-0000-000000aa1f02' AND status = 'draft';

UPDATE forecast.model_version v
SET status = 'published',
    approved_by = a.id,
    approved_at = now(),
    backtest_id = 'a0000000-0000-0000-0000-000000aa1f03'
FROM org.app_user a
WHERE v.id = 'a0000000-0000-0000-0000-000000aa1f02'
  AND v.status = 'pending_approval'
  AND a.org_id = :org::uuid AND a.username = 'helios-admin';

UPDATE forecast.forecast_model
SET current_version_id = 'a0000000-0000-0000-0000-000000aa1f02'
WHERE id = 'a0000000-0000-0000-0000-000000aa1f01'
  AND current_version_id IS DISTINCT FROM 'a0000000-0000-0000-0000-000000aa1f02'::uuid;

INSERT INTO forecast.forecast_job (
    id, org_id, data_space_id, requested_by, metric_code, scope, frequency, horizon,
    cutoff_at, status, progress, model_version_id, finished_at, created_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1f04', :org::uuid, :space::uuid, :engineer::uuid,
    'DOANH_THU_INTERNET', '{}'::jsonb, 'month', 3,
    now() - interval '1 day', 'ready', '{"step": "ready"}'::jsonb,
    'a0000000-0000-0000-0000-000000aa1f02', now(), :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO forecast.prediction (
    id, org_id, model_version_id, job_id, cutoff_at, period, point, lo80, hi80, lo95, hi95,
    interval_method, exog_uncertainty, contributions
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1f05', :org::uuid,
    'a0000000-0000-0000-0000-000000aa1f02', 'a0000000-0000-0000-0000-000000aa1f04',
    now() - interval '1 day', DATE '2014-01-01',
    1000000, 900000, 1100000, 800000, 1200000,
    'conformal', 'simulated', '{"season": 1.2}'::jsonb
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO forecast.ledger_entry (
    id, org_id, forecast_model_id, model_version_id, prediction_id, job_id, period, scope,
    issued_at, issued_via, baseline_value, point, quantiles, prob_up, fund_probs, contributions,
    status, created_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1f06', :org::uuid,
    'a0000000-0000-0000-0000-000000aa1f01', 'a0000000-0000-0000-0000-000000aa1f02',
    'a0000000-0000-0000-0000-000000aa1f05', 'a0000000-0000-0000-0000-000000aa1f04',
    DATE '2014-01-01', '{}'::jsonb, now() - interval '2 days', 'manual',
    950000, 1000000,
    '{"p02_5": 800000, "p10": 900000, "p50": 1000000, "p90": 1100000, "p97_5": 1200000}'::jsonb,
    0.65, '{}'::jsonb, '{"season": 1.2}'::jsonb, 'open', :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

-- Formula model (fuel forecast list): separate forecast_model + formula_mc version.
INSERT INTO forecast.forecast_model (
    id, org_id, data_space_id, metric_code, scope, frequency, owner_id, status,
    created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1f07', :org::uuid, :space::uuid,
    'DOANH_THU_INTERNET', '{"product": "bike"}'::jsonb, 'month', :engineer::uuid, 'active',
    :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO forecast.model_version (
    id, org_id, model_id, version, algorithm, features, training_from, training_to,
    n_points, status, model_card, created_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1f08', :org::uuid,
    'a0000000-0000-0000-0000-000000aa1f07', 1, 'formula_mc', '[]'::jsonb,
    DATE '2011-01-01', DATE '2013-12-01', 36, 'draft',
    '{"target": "formula"}'::jsonb, :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO forecast.formula_model (
    id, org_id, model_version_id, metric_version_id, product_code, formula, owner_id, created_by
)
SELECT
    'a0000000-0000-0000-0000-000000aa1f09'::uuid, :org::uuid,
    'a0000000-0000-0000-0000-000000aa1f08', mv.id, 'internet_bike',
    '{"terms": [{"code": "base", "op": "formula", "inputs": ["sales", "cost"]}]}'::jsonb,
    :engineer::uuid, :engineer::uuid
FROM context.metric_version mv
JOIN context.metric m ON m.id = mv.metric_id
WHERE m.data_space_id = :space::uuid AND m.code = 'DOANH_THU_INTERNET'
ORDER BY mv.version DESC
LIMIT 1
ON CONFLICT (id) DO NOTHING;

INSERT INTO acquire.crawl_schema_version (
    id, org_id, source_id, version, status, record_selector, fields, value_rules, created_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1f10', :org::uuid,
    'a0000000-0000-0000-0000-000000aa1d20', 1, 'draft',
    'table.price-row',
    '[{"name": "sku", "type": "text"}, {"name": "price", "type": "money", "value_constraints": {"min": 0, "max": 100000, "unit": "USD"}}]'::jsonb,
    '[]'::jsonb, :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO acquire.crawl_run (
    id, org_id, source_id, schema_version_id, trigger, status,
    pages_fetched, pages_changed, records_extracted, started_at, finished_at
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1f11', :org::uuid,
    'a0000000-0000-0000-0000-000000aa1d20',
    'a0000000-0000-0000-0000-000000aa1f10', 'manual', 'failed',
    3, 1, 0, now() - interval '1 hour', now() - interval '50 minutes'
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO acquire.schema_drift (
    id, org_id, source_id, run_id, from_version_id, drift_kind, severity, action, signals
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1f12', :org::uuid,
    'a0000000-0000-0000-0000-000000aa1d20',
    'a0000000-0000-0000-0000-000000aa1f11', 'a0000000-0000-0000-0000-000000aa1f10',
    'structural', 'minor', 'auto_remap',
    '{"added_fields": ["currency"]}'::jsonb
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO acquire.consolidation_spec (
    id, org_id, data_space_id, name, entity_type, grain, source_ids,
    owner_id, status, last_edited_by, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1f13', :org::uuid, :space::uuid,
    'Gộp giá phụ tùng (seed)', 'product', 'sku',
    ARRAY['a0000000-0000-0000-0000-000000aa1d20']::uuid[],
    :engineer::uuid, 'draft', :engineer::uuid, :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

-- /v1/data-products reads context.data_product (not pipeline.data_product).
INSERT INTO context.data_product (
    id, org_id, data_space_id, code, name, description, sample_questions,
    period_grain, period_from, owner_id, steward_id, freshness_statement, status,
    created_by, updated_by
)
SELECT
    'a0000000-0000-0000-0000-000000aa1f14'::uuid, :org::uuid, :space::uuid,
    'aw_internet_sales', 'Doanh thu Internet (seed)',
    'Sản phẩm ngữ nghĩa doanh thu Internet trên DS-AW cho screen-audit.',
    '["Doanh thu Internet tháng 12/2013?", "Biên gộp theo nhóm?"]'::jsonb,
    'month', DATE '2011-01-01', :engineer::uuid, a.id,
    'Cập nhật sau mỗi lần nạp AdventureWorksDW.', 'draft',
    :engineer::uuid, :engineer::uuid
FROM org.app_user a
WHERE a.org_id = :org::uuid AND a.username = 'helios-admin'
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description;

-- Session reports are not seeded: a draft row without the worker's MinIO body and
-- session_report.created outbox stuck "Tạo báo cáo" on an empty review screen. Create
-- via POST /v1/workspaces/{id}/reports (worker drafts, then :check).

INSERT INTO governance.behavior_policy (
    id, org_id, scope, data_space_id, kind, description, condition, action, status,
    created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000aa1f16', :org::uuid, 'data_space', :space::uuid,
    'require_approval',
    'Duyệt trước khi xuất số liệu chi tiết DS-AW (seed).',
    '{"when": "export_detail"}'::jsonb,
    '{"require": "approve"}'::jsonb,
    'draft', :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET description = EXCLUDED.description, status = EXCLUDED.status;

INSERT INTO eval.error_book_entry (
    id, org_id, entry_no, data_space_id, source_kind, answer_id, title, detail, severity,
    status, created_by
)
SELECT
    'a0000000-0000-0000-0000-000000aa1f17'::uuid, :org::uuid, '', :space::uuid,
    'number_check', a.id,
    'Biên gộp Internet lệch so với báo cáo quý (seed)',
    '{"expected": 0.40, "got": 0.38}'::jsonb,
    'medium', 'open', :engineer::uuid
FROM qa.answer a
WHERE a.org_id = :org::uuid
ORDER BY a.created_at
LIMIT 1
ON CONFLICT (id) DO NOTHING;
