-- Local V3 credit demo: the "Tín dụng" Data Space the credit-risk scenario is shown on.
--
-- A synthetic loan book (no real customer): 2 400 loans at six month-ends (30/04 to 30/09/2026),
-- by customer group, region, product, debt group (Thông tư 11/2021/TT-NHNN: groups 3 to 5 are
-- bad debt) and, for individuals, gender and age band. Values come from md5 of the loan and
-- month, so every rebuild gives the same numbers: SME sits above the 3% NPL threshold, individuals
-- in the early-warning band, large corporates well below.
--
-- Its semantic layer (debt, bad debt, NPL ratio, certified) and the NPL threshold watch are the
-- rows the product itself would write; this file only stands in for the steps of the UI.
-- Idempotent: re-applied by db-init, it rebuilds the table and updates the rows.

\set org '''a0000000-0000-0000-0000-000000000001'''
\set engineer '''a0000000-0000-0000-0000-000000000022'''
\set space '''a0000000-0000-0000-0000-000000000901'''
\set dataset '''a0000000-0000-0000-0000-000000000902'''
\set entity '''a0000000-0000-0000-0000-000000000903'''

INSERT INTO org.data_space (
    id, org_id, code, name, description, steward_id, status, opened_at, created_by, updated_by
)
VALUES (
    :space::uuid, :org::uuid, 'DS-TD', 'Tín dụng',
    'Dư nợ và nợ xấu theo nhóm khách hàng (dữ liệu mẫu, không phải khách hàng thật).',
    :engineer::uuid, 'active', now(), :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name, description = EXCLUDED.description, status = EXCLUDED.status;

INSERT INTO org.data_space_member (
    id, org_id, data_space_id, principal_type, principal_id, role_id, source
)
VALUES (
    'a0000000-0000-0000-0000-000000000904', :org::uuid, :space::uuid, 'user', :engineer::uuid,
    'a0000000-0000-0000-0000-000000000022', 'manual'
)
ON CONFLICT (id) DO NOTHING;

-- The loan book, materialised in the workspace like an ingested table.
DROP TABLE IF EXISTS workspace.credit_loan_snapshot;
CREATE TABLE workspace.credit_loan_snapshot (
    loan_id text,
    customer_id text,
    customer_segment text,
    gender text,
    age_band text,
    region text,
    product text,
    debt_group integer,
    balance numeric,
    as_of_date date
);
ALTER TABLE workspace.credit_loan_snapshot OWNER TO da_owner;
GRANT SELECT ON TABLE workspace.credit_loan_snapshot TO svc_query;

WITH loans AS (
    SELECT
        n,
        -- u(k): a fixed number in [0, 1) per loan and purpose.
        ('x' || substr(md5(n || ':seg'), 1, 8))::bit(32)::bigint / 4294967296.0 AS u_seg,
        ('x' || substr(md5(n || ':reg'), 1, 8))::bit(32)::bigint / 4294967296.0 AS u_reg,
        ('x' || substr(md5(n || ':prd'), 1, 8))::bit(32)::bigint / 4294967296.0 AS u_prd,
        ('x' || substr(md5(n || ':gen'), 1, 8))::bit(32)::bigint / 4294967296.0 AS u_gen,
        ('x' || substr(md5(n || ':age'), 1, 8))::bit(32)::bigint / 4294967296.0 AS u_age,
        ('x' || substr(md5(n || ':bal'), 1, 8))::bit(32)::bigint / 4294967296.0 AS u_bal
    FROM generate_series(1, 2400) AS n
),
typed AS (
    SELECT
        n, u_reg, u_prd, u_gen, u_age, u_bal,
        CASE WHEN u_seg < 0.62 THEN 'Cá nhân' WHEN u_seg < 0.92 THEN 'SME'
             ELSE 'Doanh nghiệp lớn' END AS segment
    FROM loans
),
months AS (
    SELECT m, (date '2026-05-01' + (m || ' month')::interval - interval '1 day')::date AS as_of
    FROM generate_series(0, 5) AS m
)
INSERT INTO workspace.credit_loan_snapshot
SELECT
    'L' || lpad(t.n::text, 5, '0'),
    'C' || lpad(((t.n * 7) % 2000 + 1)::text, 5, '0'),
    t.segment,
    -- Individuals only; 3% of them were onboarded without it (a gap for the data audit).
    CASE WHEN t.segment <> 'Cá nhân' THEN NULL
         WHEN t.u_gen < 0.03 THEN NULL
         WHEN t.u_gen < 0.57 THEN 'Nam' ELSE 'Nữ' END,
    CASE WHEN t.segment <> 'Cá nhân' THEN NULL
         WHEN t.u_age < 0.18 THEN '18-25' WHEN t.u_age < 0.48 THEN '26-35'
         WHEN t.u_age < 0.75 THEN '36-45' WHEN t.u_age < 0.95 THEN '46-60' ELSE 'Trên 60' END,
    CASE WHEN t.u_reg < 0.45 THEN 'Miền Bắc' WHEN t.u_reg < 0.65 THEN 'Miền Trung'
         ELSE 'Miền Nam' END,
    CASE t.segment
        WHEN 'Cá nhân' THEN CASE WHEN t.u_prd < 0.35 THEN 'Thẻ tín dụng'
                                 WHEN t.u_prd < 0.75 THEN 'Vay tiêu dùng' ELSE 'Vay mua nhà' END
        WHEN 'SME' THEN CASE WHEN t.u_prd < 0.7 THEN 'Vay sản xuất kinh doanh' ELSE 'Thấu chi' END
        ELSE CASE WHEN t.u_prd < 0.5 THEN 'Vay dự án' ELSE 'Vay sản xuất kinh doanh' END
    END,
    -- Bad debt (groups 3-5) by segment, rising each month: a loan's draw is fixed, so a loan that
    -- turned bad stays bad. Group 2 is watch-listed.
    CASE
        WHEN q.u_npl < (CASE t.segment WHEN 'SME' THEN 0.042 WHEN 'Cá nhân' THEN 0.015 ELSE 0.006 END)
                       * (1 + 0.08 * mo.m)
            THEN 3 + floor(q.u_grp * 3)::int
        WHEN q.u_npl < 0.09 THEN 2
        ELSE 1
    END,
    round(
        CASE t.segment
            WHEN 'Cá nhân' THEN 50 + t.u_bal * 1950
            WHEN 'SME' THEN 500 + t.u_bal * 9500
            ELSE 20000 + t.u_bal * 80000
        END * (1 - 0.01 * mo.m)
    ) * 1000000,
    mo.as_of
FROM typed t
CROSS JOIN months mo
CROSS JOIN LATERAL (
    SELECT
        ('x' || substr(md5(t.n || ':npl'), 1, 8))::bit(32)::bigint / 4294967296.0 AS u_npl,
        ('x' || substr(md5(t.n || ':grp'), 1, 8))::bit(32)::bigint / 4294967296.0 AS u_grp
) q;

-- Catalog: the dataset and its columns as the ingest writes them.
INSERT INTO catalog.dataset (
    id, org_id, source_id, data_space_id, origin, schema_name, table_name, object_kind,
    display_name, description, row_estimate, history_kind, business_date_col, enabled, scope,
    quality_state, created_by, updated_by
)
VALUES (
    :dataset::uuid, :org::uuid, NULL, :space::uuid, 'derived', 'workspace', 'credit_loan_snapshot', 'table',
    'Dư nợ khoản vay cuối tháng', 'Một dòng mỗi khoản vay mỗi ngày cuối tháng (dữ liệu mẫu).',
    14400, 'snapshot', 'as_of_date', true, 'data_space', 'ok', :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET row_estimate = EXCLUDED.row_estimate, enabled = true;

INSERT INTO catalog.column_meta (
    id, org_id, dataset_id, dataset_version, name, ordinal, data_type, normalized_type,
    display_name, is_key, created_by, updated_by
)
SELECT
    ('a0000000-0000-0000-0000-0000000009' || lpad((10 + c.ordinal)::text, 2, '0'))::uuid,
    :org::uuid, :dataset::uuid, 1, c.name, c.ordinal, c.data_type, c.normalized, c.display, c.is_key,
    :engineer::uuid, :engineer::uuid
FROM (VALUES
    (0, 'loan_id', 'text', 'string', 'Mã khoản vay', true),
    (1, 'customer_id', 'text', 'string', 'Mã khách hàng', false),
    (2, 'customer_segment', 'text', 'string', 'Nhóm khách hàng', false),
    (3, 'gender', 'text', 'string', 'Giới tính', false),
    (4, 'age_band', 'text', 'string', 'Độ tuổi', false),
    (5, 'region', 'text', 'string', 'Vùng', false),
    (6, 'product', 'text', 'string', 'Sản phẩm', false),
    (7, 'debt_group', 'integer', 'integer', 'Nhóm nợ', false),
    (8, 'balance', 'numeric', 'decimal', 'Dư nợ', false),
    (9, 'as_of_date', 'date', 'date', 'Ngày số liệu', true)
) AS c(ordinal, name, data_type, normalized, display, is_key)
ON CONFLICT (id) DO NOTHING;

-- Semantic layer.
INSERT INTO context.semantic_entity (
    id, org_id, data_space_id, code, name, description, primary_dataset_id, key_columns,
    created_by, updated_by
)
VALUES (
    :entity::uuid, :org::uuid, :space::uuid, 'khoan_vay', 'Khoản vay', 'Một khoản vay tại một ngày cuối tháng.',
    :dataset::uuid, ARRAY['loan_id', 'as_of_date'], :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO context.semantic_measure (
    id, org_id, data_space_id, entity_id, code, name, expression, aggregation, unit, additivity,
    created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000000921', :org::uuid, :space::uuid, :entity::uuid, 'du_no', 'Dư nợ',
     'SUM(balance)', 'sum', 'VND', 'semi', :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000922', :org::uuid, :space::uuid, :entity::uuid, 'no_xau', 'Nợ xấu',
     'SUM(CASE WHEN debt_group >= 3 THEN balance ELSE 0 END)', 'sum', 'VND', 'semi',
     :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO NOTHING;

INSERT INTO context.semantic_dimension (
    id, org_id, data_space_id, entity_id, code, name, expression, value_index_enabled, is_time,
    created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000000931', :org::uuid, :space::uuid, :entity::uuid, 'nhom_khach_hang',
     'Nhóm khách hàng', 'customer_segment', true, false, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000932', :org::uuid, :space::uuid, :entity::uuid, 'vung', 'Vùng', 'region',
     true, false, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000933', :org::uuid, :space::uuid, :entity::uuid, 'san_pham', 'Sản phẩm',
     'product', true, false, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000934', :org::uuid, :space::uuid, :entity::uuid, 'nhom_no', 'Nhóm nợ',
     'debt_group', false, false, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000935', :org::uuid, :space::uuid, :entity::uuid, 'gioi_tinh', 'Giới tính',
     'gender', true, false, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000936', :org::uuid, :space::uuid, :entity::uuid, 'do_tuoi', 'Độ tuổi',
     'age_band', true, false, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000937', :org::uuid, :space::uuid, :entity::uuid, 'ngay', 'Ngày',
     'as_of_date', false, true, :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO NOTHING;

INSERT INTO context.value_index (
    id, org_id, data_space_id, dimension_id, value_code, value_label, label_norm
)
SELECT
    ('a0000000-0000-0000-0000-0000000009' || lpad((40 + row_number() OVER ())::text, 2, '0'))::uuid,
    :org::uuid, :space::uuid, v.dimension_id::uuid, v.code, v.code, v.label_norm
FROM (VALUES
    ('a0000000-0000-0000-0000-000000000931', 'Cá nhân', 'ca nhan'),
    ('a0000000-0000-0000-0000-000000000931', 'SME', 'sme'),
    ('a0000000-0000-0000-0000-000000000931', 'Doanh nghiệp lớn', 'doanh nghiep lon'),
    ('a0000000-0000-0000-0000-000000000932', 'Miền Bắc', 'mien bac'),
    ('a0000000-0000-0000-0000-000000000932', 'Miền Trung', 'mien trung'),
    ('a0000000-0000-0000-0000-000000000932', 'Miền Nam', 'mien nam'),
    ('a0000000-0000-0000-0000-000000000933', 'Thẻ tín dụng', 'the tin dung'),
    ('a0000000-0000-0000-0000-000000000933', 'Vay tiêu dùng', 'vay tieu dung'),
    ('a0000000-0000-0000-0000-000000000933', 'Vay mua nhà', 'vay mua nha'),
    ('a0000000-0000-0000-0000-000000000933', 'Vay sản xuất kinh doanh', 'vay san xuat kinh doanh'),
    ('a0000000-0000-0000-0000-000000000933', 'Thấu chi', 'thau chi'),
    ('a0000000-0000-0000-0000-000000000933', 'Vay dự án', 'vay du an'),
    ('a0000000-0000-0000-0000-000000000935', 'Nam', 'nam'),
    ('a0000000-0000-0000-0000-000000000935', 'Nữ', 'nu')
) AS v(dimension_id, code, label_norm)
ON CONFLICT (id) DO NOTHING;

-- Words people use for the same dimension.
INSERT INTO context.synonym (
    id, org_id, data_space_id, phrase, phrase_norm, target_kind, target_id, origin, status
)
VALUES
    ('a0000000-0000-0000-0000-000000000961', :org::uuid, :space::uuid, 'khu vực', 'khu vuc', 'dimension',
     'a0000000-0000-0000-0000-000000000932', 'manual', 'active'),
    ('a0000000-0000-0000-0000-000000000962', :org::uuid, :space::uuid, 'phân khúc', 'phan khuc', 'dimension',
     'a0000000-0000-0000-0000-000000000931', 'manual', 'active'),
    ('a0000000-0000-0000-0000-000000000963', :org::uuid, :space::uuid, 'loại khách hàng', 'loai khach hang',
     'dimension', 'a0000000-0000-0000-0000-000000000931', 'manual', 'active'),
    ('a0000000-0000-0000-0000-000000000964', :org::uuid, :space::uuid, 'NPL', 'npl', 'metric',
     'a0000000-0000-0000-0000-000000000971', 'manual', 'active')
ON CONFLICT (id) DO NOTHING;

-- Metrics, certified by the steward.
INSERT INTO context.metric (id, org_id, data_space_id, code, owner_id, current_version, status,
                            created_by, updated_by)
VALUES
    ('a0000000-0000-0000-0000-000000000971', :org::uuid, :space::uuid, 'NPL_RATIO', :engineer::uuid, 1, 'active',
     :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000972', :org::uuid, :space::uuid, 'DU_NO', :engineer::uuid, 1, 'active',
     :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000973', :org::uuid, :space::uuid, 'NO_XAU', :engineer::uuid, 1, 'active',
     :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO NOTHING;

INSERT INTO context.metric_version (
    id, org_id, metric_id, version, display_name, description, expression, filters, unit,
    decimals, direction, period_semantics, time_grain, allowed_dimensions, additivity,
    legal_basis, status, certified_by, certified_at, valid_from
)
SELECT
    v.id::uuid, :org::uuid, v.metric_id::uuid, 1, v.name, v.description, v.expression, '[]'::jsonb,
    v.unit, v.decimals, v.direction, 'end_of_period', 'month',
    ARRAY[
        'a0000000-0000-0000-0000-000000000931', 'a0000000-0000-0000-0000-000000000932',
        'a0000000-0000-0000-0000-000000000933', 'a0000000-0000-0000-0000-000000000934',
        'a0000000-0000-0000-0000-000000000935', 'a0000000-0000-0000-0000-000000000936'
    ]::uuid[],
    'semi', 'Thông tư 11/2021/TT-NHNN: nợ xấu là nợ nhóm 3, 4, 5', 'certified', :engineer::uuid,
    '2026-06-01T00:00:00Z', '2026-01-01T00:00:00Z'
FROM (VALUES
    ('a0000000-0000-0000-0000-000000000981', 'a0000000-0000-0000-0000-000000000971',
     'Tỷ lệ nợ xấu', 'Nợ xấu (nhóm 3-5) trên tổng dư nợ, tại ngày cuối kỳ.',
     'no_xau / du_no * 100', '%', 2, 'lower_better'),
    ('a0000000-0000-0000-0000-000000000982', 'a0000000-0000-0000-0000-000000000972',
     'Dư nợ', 'Tổng dư nợ tại ngày cuối kỳ.', 'du_no', 'VND', 0, 'neutral'),
    ('a0000000-0000-0000-0000-000000000983', 'a0000000-0000-0000-0000-000000000973',
     'Nợ xấu', 'Dư nợ nhóm 3-5 tại ngày cuối kỳ.', 'no_xau', 'VND', 0, 'lower_better')
) AS v(id, metric_id, name, description, expression, unit, decimals, direction)
ON CONFLICT (id) DO NOTHING;

-- The NPL threshold the risk team watches: above 3%, early warning from 2%.
INSERT INTO monitor.monitor (
    id, org_id, data_space_id, owner_id, metric_id, scope_filter, rule_kind, rule,
    schedule_cron, notify_mode, recipients, status, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000000991', :org::uuid, :space::uuid, :engineer::uuid,
    'a0000000-0000-0000-0000-000000000971', '{}', 'threshold',
    '{"direction": "above", "value": 3, "early": 2, "window": "month_end",
      "text": "> 3% (cảnh báo sớm từ 2%) tại ngày cuối tháng"}',
    '0 8 * * *', 'with_analysis', '[]', 'active', :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET rule = EXCLUDED.rule, status = EXCLUDED.status;

-- The "Phân rã biến động" skill on NPL (worker-agent runs it, skill category decomposition): the
-- Ask screen's "vì sao" and Explore's "Agent giải thích" find it via /v1/skills?category=decomposition.
-- A published version needs an eval run and an approver (CHECK). The eval run below is the seed's
-- placeholder, not an evaluation: no item was run, so it claims no pass rate.
INSERT INTO skill.skill (
    id, org_id, data_space_id, code, name, description, category, scope, owner_id, origin,
    current_version, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000000995', :org::uuid, :space::uuid, 'phan_ra_bien_dong',
    'Phân rã biến động', 'Tách thay đổi của tỷ lệ nợ xấu giữa hai kỳ thành phần đóng góp theo chiều.',
    'decomposition', 'data_space', :engineer::uuid, 'manual', 1, :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO eval.eval_set (
    id, org_id, data_space_id, subject_type, subject_id, name, status, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000000996', :org::uuid, :space::uuid, 'skill',
    'a0000000-0000-0000-0000-000000000995', 'Phân rã biến động (seed)', 'active',
    :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO eval.eval_run (
    id, org_id, eval_set_id, subject_version, trigger, item_count, passed, failed, status,
    started_at, finished_at
)
VALUES (
    'a0000000-0000-0000-0000-000000000997', :org::uuid, 'a0000000-0000-0000-0000-000000000996',
    'v1 (seed)', 'manual', 0, 0, 0, 'completed', now(), now()
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO skill.skill_version (
    id, org_id, skill_id, version, params_schema, steps, metric_version_ids, status, eval_run_id,
    approved_by, approved_at
)
VALUES (
    'a0000000-0000-0000-0000-000000000998', :org::uuid, 'a0000000-0000-0000-0000-000000000995', 1,
    '{"type": "object", "required": ["metric", "dimension", "period"],
      "properties": {
        "metric": {"title": "Chỉ số", "enum": ["NPL_RATIO"]},
        "dimension": {"title": "Chiều", "enum": ["nhom_khach_hang", "vung", "san_pham"]},
        "period": {"title": "Kỳ", "default": "2026-09"},
        "base_period": {"title": "So với kỳ"}}}',
    '[{"kind": "decompose"}]',
    ARRAY['a0000000-0000-0000-0000-000000000981']::uuid[],
    'published', 'a0000000-0000-0000-0000-000000000997', :engineer::uuid, now()
)
ON CONFLICT (id) DO NOTHING;
