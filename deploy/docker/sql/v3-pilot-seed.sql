-- Pilot Data Space "Bán hàng": sales, gross margin, receivables, inventory.
--
-- Synthetic, no real customer. Numbers come from the row keys, so every rebuild matches.
-- Five traps sit in the data on purpose, for the sales / gross-margin / receivables pilot
-- (R1-16, docs/v3/02-market-solution.md §4.3):
--
--   1. Join fan-out. One in four orders has two header rows. Joining lines to headers
--      on order_id and summing line revenue counts those orders twice.
--   2. Average of ratios. In September, fashion margin at Đà Nẵng stays high on a tiny
--      book while Hà Nội and TP. Hồ Chí Minh reprice. The unweighted mean of the three
--      branch rates hides the drop that the revenue-weighted rate shows.
--   3. Summing stock. pilot_inventory is a month-end snapshot. Summing qty across months
--      multiplies it. The retired code "Kho Hà Nội" repeats the Hà Nội quantity of TT-01
--      on 30/09/2026.
--   4. An expired document. QD-KM-2025/12 cut fashion prices in December 2025 only.
--   5. Two editions of the same period. September is stored as preliminary and final.
--      The preliminary book hides the fashion margin drop. The policy says to use final.
--
-- Certified metrics follow the policy in force (QD-GV-2026/02), not the traps.
-- Idempotent: db-init rebuilds the tables and upserts the rows.

\set org '''a0000000-0000-0000-0000-000000000001'''
\set engineer '''a0000000-0000-0000-0000-000000000022'''
\set space '''a0000000-0000-0000-0000-000000000b01'''

INSERT INTO org.data_space (
    id, org_id, code, name, description, steward_id, status, opened_at, created_by, updated_by
)
VALUES (
    :space::uuid, :org::uuid, 'DS-BH', 'Bán hàng',
    'Doanh thu, giá vốn, biên lợi nhuận gộp, công nợ và tồn kho (dữ liệu mẫu, không phải khách hàng thật).',
    :engineer::uuid, 'active', now(), :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name, description = EXCLUDED.description, status = EXCLUDED.status;

INSERT INTO org.data_space_member (
    id, org_id, data_space_id, principal_type, principal_id, role_id, source
)
VALUES (
    'a0000000-0000-0000-0000-000000000b02', :org::uuid, :space::uuid, 'user', :engineer::uuid,
    'a0000000-0000-0000-0000-000000000022', 'manual'
)
ON CONFLICT (id) DO NOTHING;

-- Lines are the grain of revenue. One line per order, so a header join is the only way to double it.
DROP TABLE IF EXISTS workspace.pilot_sales_line;
CREATE TABLE workspace.pilot_sales_line (
    line_id text PRIMARY KEY,
    order_id text NOT NULL,
    sold_on date NOT NULL,
    branch text NOT NULL,
    category text NOT NULL,
    qty integer NOT NULL,
    net_revenue numeric NOT NULL,
    cogs numeric NOT NULL
);
ALTER TABLE workspace.pilot_sales_line OWNER TO da_owner;
GRANT SELECT ON TABLE workspace.pilot_sales_line TO svc_query;

INSERT INTO workspace.pilot_sales_line (
    line_id, order_id, sold_on, branch, category, qty, net_revenue, cogs
)
SELECT
    s.order_id || '-1',
    s.order_id,
    s.sold_on,
    s.branch,
    s.category,
    s.qty,
    s.qty * s.unit_price,
    round(s.qty * s.unit_price * s.cogs_rate, 0)
FROM (
    SELECT
        'O-' || b.code || '-' || c.code || '-' || to_char(m.month_start, 'YYYYMM') || '-' || n AS order_id,
        (m.month_start + ((n * 3) || ' day')::interval)::date AS sold_on,
        b.name AS branch,
        c.name AS category,
        (b.qty_base * n)::integer AS qty,
        c.unit_price,
        CASE
            WHEN c.name = 'Thời trang' AND m.month_start >= date '2026-09-01' AND b.name <> 'Đà Nẵng'
                THEN 0.82
            WHEN c.name = 'Thời trang' AND m.month_start >= date '2026-09-01'
                THEN 0.50
            ELSE 0.68
        END AS cogs_rate
    FROM (VALUES
        ('HN', 'Hà Nội', 10),
        ('HCM', 'TP. Hồ Chí Minh', 5),
        ('DN', 'Đà Nẵng', 1)
    ) AS b(code, name, qty_base)
    CROSS JOIN (VALUES
        ('TP', 'Thực phẩm', 80000),
        ('DM', 'Điện máy', 4000000),
        ('TT', 'Thời trang', 350000)
    ) AS c(code, name, unit_price)
    CROSS JOIN generate_series(date '2026-04-01', date '2026-09-01', interval '1 month') AS m(month_start)
    CROSS JOIN generate_series(1, 4) AS n
) AS s;

-- Headers. The order whose id ends in "-4" is stored twice, with the same amount.
DROP TABLE IF EXISTS workspace.pilot_order_header;
CREATE TABLE workspace.pilot_order_header (
    header_id text PRIMARY KEY,
    order_id text NOT NULL,
    status text NOT NULL,
    order_amount numeric NOT NULL
);
ALTER TABLE workspace.pilot_order_header OWNER TO da_owner;
GRANT SELECT ON TABLE workspace.pilot_order_header TO svc_query;

INSERT INTO workspace.pilot_order_header (header_id, order_id, status, order_amount)
SELECT order_id || '-issued', order_id, 'issued', sum(net_revenue)
FROM workspace.pilot_sales_line
GROUP BY order_id
UNION ALL
SELECT order_id || '-adjusted', order_id, 'adjusted', sum(net_revenue)
FROM workspace.pilot_sales_line
WHERE order_id LIKE '%-4'
GROUP BY order_id;

-- Open invoices as of 30/09/2026. August is still unpaid.
DROP TABLE IF EXISTS workspace.pilot_receivable;
CREATE TABLE workspace.pilot_receivable (
    invoice_id text PRIMARY KEY,
    customer text NOT NULL,
    branch text NOT NULL,
    issued_on date NOT NULL,
    due_on date NOT NULL,
    amount numeric NOT NULL,
    paid numeric NOT NULL,
    as_of_date date NOT NULL
);
ALTER TABLE workspace.pilot_receivable OWNER TO da_owner;
GRANT SELECT ON TABLE workspace.pilot_receivable TO svc_query;

INSERT INTO workspace.pilot_receivable (
    invoice_id, customer, branch, issued_on, due_on, amount, paid, as_of_date
)
SELECT
    'IV-' || b.code || '-' || to_char(m.month_start, 'YYYYMM'),
    'Khách mẫu ' || b.code,
    b.name,
    m.month_start::date,
    (m.month_start + interval '30 day')::date,
    b.amount,
    CASE WHEN m.month_start < date '2026-08-01' THEN b.amount ELSE 0 END,
    date '2026-09-30'
FROM (VALUES
    ('HN', 'Hà Nội', 120000000),
    ('HCM', 'TP. Hồ Chí Minh', 80000000),
    ('DN', 'Đà Nẵng', 15000000)
) AS b(code, name, amount)
CROSS JOIN generate_series(date '2026-04-01', date '2026-09-01', interval '1 month') AS m(month_start);

-- Month-end stock. Semi-additive: one date, and never both warehouse codes.
DROP TABLE IF EXISTS workspace.pilot_inventory;
CREATE TABLE workspace.pilot_inventory (
    sku text NOT NULL,
    sku_name text NOT NULL,
    branch text NOT NULL,
    as_of_date date NOT NULL,
    qty_on_hand integer NOT NULL,
    unit_cost numeric NOT NULL,
    PRIMARY KEY (sku, branch, as_of_date)
);
ALTER TABLE workspace.pilot_inventory OWNER TO da_owner;
GRANT SELECT ON TABLE workspace.pilot_inventory TO svc_query;

INSERT INTO workspace.pilot_inventory (sku, sku_name, branch, as_of_date, qty_on_hand, unit_cost)
SELECT s.sku, s.sku_name, b.name, mo.as_of, 100, s.unit_cost
FROM (VALUES
    ('TP-01', 'Gạo mẫu', 18000),
    ('DM-01', 'Nồi mẫu', 900000),
    ('TT-01', 'Áo mẫu', 120000)
) AS s(sku, sku_name, unit_cost)
CROSS JOIN (VALUES ('Hà Nội'), ('TP. Hồ Chí Minh'), ('Đà Nẵng')) AS b(name)
CROSS JOIN (
    SELECT (date '2026-05-01' + (m || ' month')::interval - interval '1 day')::date AS as_of
    FROM generate_series(0, 5) AS m
) AS mo;

INSERT INTO workspace.pilot_inventory (sku, sku_name, branch, as_of_date, qty_on_hand, unit_cost)
VALUES ('TT-01', 'Áo mẫu', 'Kho Hà Nội', date '2026-09-30', 100, 120000);

-- September twice. Preliminary revenue for fashion is higher, so the drop disappears.
DROP TABLE IF EXISTS workspace.pilot_period_edition;
CREATE TABLE workspace.pilot_period_edition (
    period_month date NOT NULL,
    edition text NOT NULL,
    category text NOT NULL,
    net_revenue numeric NOT NULL,
    cogs numeric NOT NULL,
    PRIMARY KEY (period_month, edition, category)
);
ALTER TABLE workspace.pilot_period_edition OWNER TO da_owner;
GRANT SELECT ON TABLE workspace.pilot_period_edition TO svc_query;

INSERT INTO workspace.pilot_period_edition (period_month, edition, category, net_revenue, cogs)
SELECT date_trunc('month', sold_on)::date, 'final', category, sum(net_revenue), sum(cogs)
FROM workspace.pilot_sales_line
GROUP BY 1, 3;

INSERT INTO workspace.pilot_period_edition (period_month, edition, category, net_revenue, cogs)
SELECT period_month, 'preliminary', category,
       CASE WHEN category = 'Thời trang' THEN round(net_revenue * 1.25, 0) ELSE net_revenue END,
       cogs
FROM workspace.pilot_period_edition
WHERE period_month = date '2026-09-01' AND edition = 'final';

-- Catalog rows the ingest would have written.
INSERT INTO catalog.dataset (
    id, org_id, source_id, data_space_id, origin, schema_name, table_name, object_kind,
    display_name, description, row_estimate, history_kind, business_date_col, enabled, scope,
    quality_state, created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000000b10', :org::uuid, NULL, :space::uuid, 'derived', 'workspace',
     'pilot_sales_line', 'table', 'Dòng bán',
     'Một dòng một đơn. Doanh thu và giá vốn cộng được theo kỳ.',
     (SELECT count(*) FROM workspace.pilot_sales_line), 'none', 'sold_on', true, 'data_space', 'ok',
     :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b11', :org::uuid, NULL, :space::uuid, 'derived', 'workspace',
     'pilot_order_header', 'table', 'Tiêu đề đơn',
     'Một đơn có thể có hai dòng tiêu đề. Không cộng doanh thu qua bảng này.',
     (SELECT count(*) FROM workspace.pilot_order_header), 'none', NULL, true, 'data_space', 'ok',
     :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b12', :org::uuid, NULL, :space::uuid, 'derived', 'workspace',
     'pilot_receivable', 'table', 'Công nợ phải thu',
     'Số còn phải thu tại ngày 30/09/2026.',
     (SELECT count(*) FROM workspace.pilot_receivable), 'snapshot', 'as_of_date', true, 'data_space', 'ok',
     :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b13', :org::uuid, NULL, :space::uuid, 'derived', 'workspace',
     'pilot_inventory', 'table', 'Tồn kho cuối kỳ',
     'Số cuối mỗi tháng. Không cộng các tháng. Mã Kho Hà Nội trùng chi nhánh Hà Nội.',
     (SELECT count(*) FROM workspace.pilot_inventory), 'snapshot', 'as_of_date', true, 'data_space', 'ok',
     :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b14', :org::uuid, NULL, :space::uuid, 'derived', 'workspace',
     'pilot_period_edition', 'table', 'Bản chốt kỳ',
     'Mỗi kỳ một bản final. Tháng 09/2026 còn bản preliminary, không được cộng vào final.',
     (SELECT count(*) FROM workspace.pilot_period_edition), 'snapshot', 'period_month', true, 'data_space', 'ok',
     :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO UPDATE SET
    row_estimate = EXCLUDED.row_estimate, description = EXCLUDED.description, enabled = true;

INSERT INTO catalog.column_meta (
    id, org_id, dataset_id, dataset_version, name, ordinal, data_type, normalized_type,
    display_name, is_key, created_by, updated_by
)
SELECT
    ('a0000000-0000-0000-0000-000000000b' || lpad(c.n::text, 2, '0'))::uuid,
    :org::uuid, c.dataset_id::uuid, 1, c.name, c.ordinal, c.data_type, c.normalized, c.display, c.is_key,
    :engineer::uuid, :engineer::uuid
FROM (VALUES
    (30, 'a0000000-0000-0000-0000-000000000b10', 0, 'line_id', 'text', 'string', 'Mã dòng', true),
    (31, 'a0000000-0000-0000-0000-000000000b10', 1, 'order_id', 'text', 'string', 'Mã đơn', false),
    (32, 'a0000000-0000-0000-0000-000000000b10', 2, 'sold_on', 'date', 'date', 'Ngày bán', true),
    (33, 'a0000000-0000-0000-0000-000000000b10', 3, 'branch', 'text', 'string', 'Chi nhánh', false),
    (34, 'a0000000-0000-0000-0000-000000000b10', 4, 'category', 'text', 'string', 'Nhóm hàng', false),
    (35, 'a0000000-0000-0000-0000-000000000b10', 5, 'qty', 'integer', 'integer', 'Số lượng', false),
    (36, 'a0000000-0000-0000-0000-000000000b10', 6, 'net_revenue', 'numeric', 'decimal', 'Doanh thu thuần', false),
    (37, 'a0000000-0000-0000-0000-000000000b10', 7, 'cogs', 'numeric', 'decimal', 'Giá vốn', false),
    (38, 'a0000000-0000-0000-0000-000000000b13', 0, 'sku', 'text', 'string', 'Mã hàng', true),
    (39, 'a0000000-0000-0000-0000-000000000b13', 1, 'branch', 'text', 'string', 'Chi nhánh', true),
    (40, 'a0000000-0000-0000-0000-000000000b13', 2, 'as_of_date', 'date', 'date', 'Ngày tồn', true),
    (41, 'a0000000-0000-0000-0000-000000000b13', 3, 'qty_on_hand', 'integer', 'integer', 'Số tồn', false)
) AS c(n, dataset_id, ordinal, name, data_type, normalized, display, is_key)
ON CONFLICT (id) DO NOTHING;

-- Semantic layer. Margin is revenue-weighted. Stock is its own entity: end of one day, one warehouse code.
INSERT INTO context.semantic_entity (
    id, org_id, data_space_id, code, name, description, primary_dataset_id, key_columns,
    created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000000b20', :org::uuid, :space::uuid, 'dong_ban', 'Dòng bán',
     'Một dòng hàng của một đơn.',
     'a0000000-0000-0000-0000-000000000b10', ARRAY['line_id'], :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b21', :org::uuid, :space::uuid, 'ton', 'Tồn kho',
     'Số tồn một mã tại một chi nhánh, một ngày cuối kỳ.',
     'a0000000-0000-0000-0000-000000000b13', ARRAY['sku', 'branch', 'as_of_date'],
     :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO NOTHING;

INSERT INTO context.semantic_measure (
    id, org_id, data_space_id, entity_id, code, name, expression, aggregation, unit, additivity,
    created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000000b30', :org::uuid, :space::uuid,
     'a0000000-0000-0000-0000-000000000b20', 'doanh_thu', 'Doanh thu thuần',
     'SUM(net_revenue)', 'sum', 'VND', 'full', :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b31', :org::uuid, :space::uuid,
     'a0000000-0000-0000-0000-000000000b20', 'gia_von', 'Giá vốn',
     'SUM(cogs)', 'sum', 'VND', 'full', :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b32', :org::uuid, :space::uuid,
     'a0000000-0000-0000-0000-000000000b20', 'bien_gop', 'Lãi gộp',
     'SUM(net_revenue - cogs)', 'sum', 'VND', 'full', :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b33', :org::uuid, :space::uuid,
     'a0000000-0000-0000-0000-000000000b21', 'ton_kho', 'Tồn kho',
     'SUM(qty_on_hand)', 'sum', 'cái', 'semi', :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO NOTHING;

INSERT INTO context.semantic_dimension (
    id, org_id, data_space_id, entity_id, code, name, expression, value_index_enabled, is_time,
    created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000000b40', :org::uuid, :space::uuid,
     'a0000000-0000-0000-0000-000000000b20', 'chi_nhanh', 'Chi nhánh', 'branch',
     true, false, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b41', :org::uuid, :space::uuid,
     'a0000000-0000-0000-0000-000000000b20', 'nhom_hang', 'Nhóm hàng', 'category',
     true, false, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b42', :org::uuid, :space::uuid,
     'a0000000-0000-0000-0000-000000000b20', 'ngay_ban', 'Ngày bán', 'sold_on',
     false, true, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b43', :org::uuid, :space::uuid,
     'a0000000-0000-0000-0000-000000000b21', 'kho', 'Kho', 'branch',
     true, false, :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO NOTHING;

INSERT INTO context.value_index (
    id, org_id, data_space_id, dimension_id, value_code, value_label, label_norm
)
SELECT
    ('a0000000-0000-0000-0000-000000000b' || lpad((50 + row_number() OVER ())::text, 2, '0'))::uuid,
    :org::uuid, :space::uuid, v.dimension_id::uuid, v.code, v.code, v.label_norm
FROM (VALUES
    ('a0000000-0000-0000-0000-000000000b40', 'Hà Nội', 'ha noi'),
    ('a0000000-0000-0000-0000-000000000b40', 'TP. Hồ Chí Minh', 'tp ho chi minh'),
    ('a0000000-0000-0000-0000-000000000b40', 'Đà Nẵng', 'da nang'),
    ('a0000000-0000-0000-0000-000000000b41', 'Thực phẩm', 'thuc pham'),
    ('a0000000-0000-0000-0000-000000000b41', 'Điện máy', 'dien may'),
    ('a0000000-0000-0000-0000-000000000b41', 'Thời trang', 'thoi trang')
) AS v(dimension_id, code, label_norm)
ON CONFLICT (id) DO NOTHING;

INSERT INTO context.synonym (
    id, org_id, data_space_id, phrase, phrase_norm, target_kind, target_id, origin, status
)
VALUES
    ('a0000000-0000-0000-0000-000000000b70', :org::uuid, :space::uuid, 'biên lợi nhuận gộp',
     'bien loi nhuan gop', 'metric', 'a0000000-0000-0000-0000-000000000b83', 'manual', 'active'),
    ('a0000000-0000-0000-0000-000000000b71', :org::uuid, :space::uuid, 'lợi nhuận gộp',
     'loi nhuan gop', 'metric', 'a0000000-0000-0000-0000-000000000b82', 'manual', 'active'),
    ('a0000000-0000-0000-0000-000000000b72', :org::uuid, :space::uuid, 'cửa hàng',
     'cua hang', 'dimension', 'a0000000-0000-0000-0000-000000000b40', 'manual', 'active')
ON CONFLICT (id) DO NOTHING;

INSERT INTO context.metric (
    id, org_id, data_space_id, code, owner_id, current_version, status, created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000000b80', :org::uuid, :space::uuid, 'DOANH_THU', :engineer::uuid, 1,
     'active', :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b81', :org::uuid, :space::uuid, 'GIA_VON', :engineer::uuid, 1,
     'active', :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b82', :org::uuid, :space::uuid, 'BIEN_GOP', :engineer::uuid, 1,
     'active', :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b83', :org::uuid, :space::uuid, 'BIEN_GOP_TY_LE', :engineer::uuid, 1,
     'active', :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000b84', :org::uuid, :space::uuid, 'TON_KHO', :engineer::uuid, 1,
     'active', :engineer::uuid, :engineer::uuid)
ON CONFLICT (id) DO NOTHING;

INSERT INTO context.metric_version (
    id, org_id, metric_id, version, display_name, description, expression, filters, unit,
    decimals, direction, period_semantics, time_grain, allowed_dimensions, additivity,
    legal_basis, status, certified_by, certified_at, valid_from
)
SELECT
    v.id::uuid, :org::uuid, v.metric_id::uuid, 1, v.name, v.description, v.expression, '[]'::jsonb,
    v.unit, v.decimals, v.direction, v.period_semantics, 'month',
    ARRAY[
        'a0000000-0000-0000-0000-000000000b40',
        'a0000000-0000-0000-0000-000000000b41'
    ]::uuid[],
    v.additivity, 'QD-GV-2026/02', 'certified', :engineer::uuid,
    '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z'
FROM (VALUES
    ('a0000000-0000-0000-0000-000000000b90', 'a0000000-0000-0000-0000-000000000b80',
     'Doanh thu thuần', 'Tổng doanh thu thuần trong kỳ, lấy từ dòng bán.',
     'doanh_thu', 'VND', 0, 'higher_better', 'sum_over_period', 'full'),
    ('a0000000-0000-0000-0000-000000000b91', 'a0000000-0000-0000-0000-000000000b81',
     'Giá vốn', 'Tổng giá vốn trong kỳ, cùng grain với doanh thu.',
     'gia_von', 'VND', 0, 'lower_better', 'sum_over_period', 'full'),
    ('a0000000-0000-0000-0000-000000000b92', 'a0000000-0000-0000-0000-000000000b82',
     'Lãi gộp', 'Doanh thu thuần trừ giá vốn.',
     'bien_gop', 'VND', 0, 'higher_better', 'sum_over_period', 'full'),
    ('a0000000-0000-0000-0000-000000000b93', 'a0000000-0000-0000-0000-000000000b83',
     'Biên lợi nhuận gộp', 'Lãi gộp trên doanh thu thuần. Không lấy trung bình tỷ lệ từng chi nhánh.',
     'bien_gop / doanh_thu * 100', '%', 1, 'higher_better', 'sum_over_period', 'none')
) AS v(id, metric_id, name, description, expression, unit, decimals, direction, period_semantics, additivity)
ON CONFLICT (id) DO NOTHING;

INSERT INTO context.metric_version (
    id, org_id, metric_id, version, display_name, description, expression, filters, unit,
    decimals, direction, period_semantics, time_grain, allowed_dimensions, additivity,
    legal_basis, status, certified_by, certified_at, valid_from
)
VALUES (
    'a0000000-0000-0000-0000-000000000b94', :org::uuid, 'a0000000-0000-0000-0000-000000000b84', 1,
    'Tồn kho', 'Số tồn tại ngày cuối kỳ. Không cộng các ngày, không cộng mã Kho Hà Nội.',
    'ton_kho', '[]'::jsonb, 'cái', 0, 'neutral', 'end_of_period', 'month',
    ARRAY['a0000000-0000-0000-0000-000000000b43']::uuid[],
    'semi', 'QD-GV-2026/02', 'certified', :engineer::uuid,
    '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z'
)
ON CONFLICT (id) DO NOTHING;

-- Policy documents. The December 2025 promotion is expired. The July 2026 decision replaces January.
-- The originals are deploy/docker/pilot-docs/*.html, which minio-init puts in da-raw-docs; sha256
-- and size_bytes are theirs, so the file a reader opens is the one the pages were read from.
INSERT INTO document.collection (
    id, org_id, data_space_id, code, name, media_kinds, purpose, pii_policy,
    qdrant_collection, embed_model, status, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000000c01', :org::uuid, :space::uuid, 'chinh-sach-ban-hang',
    'Chính sách bán hàng', ARRAY['html'], 'Trả lời về giá vốn, biên và tồn kho', 'mask',
    'pilot-sales-policy', 'multilingual-e5-small', 'active', :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO document.document_source (
    id, org_id, collection_id, kind, location, sync_cron, status, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000000c02', :org::uuid, 'a0000000-0000-0000-0000-000000000c01',
    'object_store', 's3://da-raw-docs/pilot/sales-policy/', '0 0 1 1 *', 'active',
    :engineer::uuid, :engineer::uuid
)
ON CONFLICT (id) DO NOTHING;

DELETE FROM document.document WHERE id = 'a0000000-0000-0000-0000-000000000c12';
DELETE FROM document.document WHERE id = 'a0000000-0000-0000-0000-000000000c11';
DELETE FROM document.document WHERE id = 'a0000000-0000-0000-0000-000000000c13';

INSERT INTO document.document (
    id, org_id, collection_id, document_source_id, sha256, media, file_name, object_key,
    size_bytes, title, doc_no, issued_on, effective_from, effective_to, supersedes_id,
    page_count, status, created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000000c11', :org::uuid, 'a0000000-0000-0000-0000-000000000c01',
     'a0000000-0000-0000-0000-000000000c02',
     '0808647e3df0bec3dfa5b3f9db02475a7bad7e30294c612b6252902b4924cc49',
     'html', 'QD-GV-2026-01.html', 'pilot/sales-policy/QD-GV-2026-01.html', 328,
     'Quy định giá vốn tháng 01/2026', 'QD-GV-2026/01', date '2026-01-02',
     date '2026-01-01', date '2026-06-30', NULL, 1, 'ready', :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000c13', :org::uuid, 'a0000000-0000-0000-0000-000000000c01',
     'a0000000-0000-0000-0000-000000000c02',
     '1fdccb7eaf347b0645d64911cffc9be8d62adf40568c8c0294529eb215fa35ec',
     'html', 'QD-KM-2025-12.html', 'pilot/sales-policy/QD-KM-2025-12.html', 333,
     'Khuyến mại thời trang tháng 12/2025', 'QD-KM-2025/12', date '2025-11-20',
     date '2025-12-01', date '2025-12-31', NULL, 1, 'ready', :engineer::uuid, :engineer::uuid);

INSERT INTO document.document (
    id, org_id, collection_id, document_source_id, sha256, media, file_name, object_key,
    size_bytes, title, doc_no, issued_on, effective_from, effective_to, supersedes_id,
    page_count, status, created_by, updated_by
)
VALUES (
    'a0000000-0000-0000-0000-000000000c12', :org::uuid, 'a0000000-0000-0000-0000-000000000c01',
    'a0000000-0000-0000-0000-000000000c02',
    'a1f06ab7586060d83b9836c7e75edcf9594788482f137213f02fd537b532563c',
    'html', 'QD-GV-2026-02.html', 'pilot/sales-policy/QD-GV-2026-02.html', 669,
    'Quy định giá vốn và tồn kho', 'QD-GV-2026/02', date '2026-06-25',
    date '2026-07-01', NULL, 'a0000000-0000-0000-0000-000000000c11', 1, 'ready',
    :engineer::uuid, :engineer::uuid
);

INSERT INTO document.document_page (
    id, org_id, document_id, page_no, page_hash, text_layer, text, usable, created_by, updated_by
)
VALUES
    ('a0000000-0000-0000-0000-000000000c21', :org::uuid, 'a0000000-0000-0000-0000-000000000c11',
     1, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', true,
     'Quy định QD-GV-2026/01. Giá vốn lấy theo bảng giá nhà cung cấp tháng 01/2026. Hết hiệu lực ngày 30/06/2026.',
     true, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000c22', :org::uuid, 'a0000000-0000-0000-0000-000000000c12',
     1, 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', true,
     'Quy định QD-GV-2026/02, có hiệu lực từ 01/07/2026, thay QD-GV-2026/01. Từ 01/09/2026 giá nhập nhóm Thời trang tăng. Biên lợi nhuận gộp bằng tổng lãi gộp chia tổng doanh thu thuần, không lấy trung bình tỷ lệ từng chi nhánh. Dùng bản chốt final của kỳ, không cộng bản preliminary. Tồn kho chỉ lấy số ngày cuối kỳ. Mã Kho Hà Nội đã gộp vào chi nhánh Hà Nội, không được cộng thêm.',
     true, :engineer::uuid, :engineer::uuid),
    ('a0000000-0000-0000-0000-000000000c23', :org::uuid, 'a0000000-0000-0000-0000-000000000c13',
     1, 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc', true,
     'Quyết định QD-KM-2025/12. Giảm giá 20% nhóm Thời trang trong tháng 12/2025. Hết hiệu lực ngày 31/12/2025.',
     true, :engineer::uuid, :engineer::uuid);

INSERT INTO document.chunk (
    id, org_id, document_id, collection_id, page_from, page_to, section_path, text, tokens,
    effective_from, effective_to, pii_masked
)
VALUES
    ('a0000000-0000-0000-0000-000000000c31', :org::uuid, 'a0000000-0000-0000-0000-000000000c11',
     'a0000000-0000-0000-0000-000000000c01', 1, 1, 'QD-GV-2026/01',
     'Quy định QD-GV-2026/01. Giá vốn lấy theo bảng giá nhà cung cấp tháng 01/2026. Hết hiệu lực ngày 30/06/2026.',
     40, date '2026-01-01', date '2026-06-30', true),
    ('a0000000-0000-0000-0000-000000000c32', :org::uuid, 'a0000000-0000-0000-0000-000000000c12',
     'a0000000-0000-0000-0000-000000000c01', 1, 1, 'QD-GV-2026/02',
     'Quy định QD-GV-2026/02, có hiệu lực từ 01/07/2026, thay QD-GV-2026/01. Từ 01/09/2026 giá nhập nhóm Thời trang tăng. Biên lợi nhuận gộp bằng tổng lãi gộp chia tổng doanh thu thuần, không lấy trung bình tỷ lệ từng chi nhánh. Dùng bản chốt final của kỳ, không cộng bản preliminary. Tồn kho chỉ lấy số ngày cuối kỳ. Mã Kho Hà Nội đã gộp vào chi nhánh Hà Nội, không được cộng thêm.',
     120, date '2026-07-01', NULL, true),
    ('a0000000-0000-0000-0000-000000000c33', :org::uuid, 'a0000000-0000-0000-0000-000000000c13',
     'a0000000-0000-0000-0000-000000000c01', 1, 1, 'QD-KM-2025/12',
     'Quyết định QD-KM-2025/12. Giảm giá 20% nhóm Thời trang trong tháng 12/2025. Hết hiệu lực ngày 31/12/2025.',
     40, date '2025-12-01', date '2025-12-31', true);

-- The seed fails closed when a trap is missing, so a later edit cannot silently flatten the book.
DO $$
DECLARE
    truth numeric;
    doubled numeric;
    august numeric;
    september_weighted numeric;
    september_unweighted numeric;
    stock_latest numeric;
    stock_with_alias numeric;
    stock_all_dates numeric;
    september_final numeric;
    september_both numeric;
BEGIN
    SELECT sum(net_revenue) INTO truth FROM workspace.pilot_sales_line;
    SELECT sum(l.net_revenue) INTO doubled
    FROM workspace.pilot_sales_line AS l
    JOIN workspace.pilot_order_header AS h ON h.order_id = l.order_id;
    IF doubled <= truth * 1.1 THEN
        RAISE EXCEPTION 'join fan-out missing: lines % joined %', truth, doubled;
    END IF;

    SELECT (sum(net_revenue) - sum(cogs)) / sum(net_revenue) INTO august
    FROM workspace.pilot_sales_line
    WHERE category = 'Thời trang' AND sold_on >= date '2026-08-01' AND sold_on < date '2026-09-01';
    SELECT (sum(net_revenue) - sum(cogs)) / sum(net_revenue) INTO september_weighted
    FROM workspace.pilot_sales_line
    WHERE category = 'Thời trang' AND sold_on >= date '2026-09-01' AND sold_on < date '2026-10-01';
    SELECT avg(margin) INTO september_unweighted FROM (
        SELECT (sum(net_revenue) - sum(cogs)) / sum(net_revenue) AS margin
        FROM workspace.pilot_sales_line
        WHERE category = 'Thời trang' AND sold_on >= date '2026-09-01' AND sold_on < date '2026-10-01'
        GROUP BY branch
    ) AS branch_margin;
    IF august - september_weighted < 0.08 THEN
        RAISE EXCEPTION 'fashion margin did not drop: august % september %', august, september_weighted;
    END IF;
    IF september_unweighted - september_weighted < 0.05 THEN
        RAISE EXCEPTION 'unweighted margin does not hide the drop: unweighted % weighted %',
            september_unweighted, september_weighted;
    END IF;

    SELECT sum(qty_on_hand) INTO stock_latest
    FROM workspace.pilot_inventory
    WHERE as_of_date = date '2026-09-30' AND branch <> 'Kho Hà Nội';
    SELECT sum(qty_on_hand) INTO stock_with_alias
    FROM workspace.pilot_inventory
    WHERE as_of_date = date '2026-09-30';
    SELECT sum(qty_on_hand) INTO stock_all_dates FROM workspace.pilot_inventory;
    IF stock_with_alias <= stock_latest THEN
        RAISE EXCEPTION 'retired warehouse code did not repeat stock';
    END IF;
    IF stock_all_dates < stock_latest * 5 THEN
        RAISE EXCEPTION 'summing stock across months did not inflate it';
    END IF;

    SELECT sum(net_revenue) INTO september_final
    FROM workspace.pilot_period_edition
    WHERE period_month = date '2026-09-01' AND edition = 'final';
    SELECT sum(net_revenue) INTO september_both
    FROM workspace.pilot_period_edition
    WHERE period_month = date '2026-09-01';
    IF september_both <= september_final THEN
        RAISE EXCEPTION 'September does not have two editions';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM document.document
        WHERE doc_no = 'QD-KM-2025/12' AND effective_to < date '2026-01-01' AND status = 'ready'
    ) THEN
        RAISE EXCEPTION 'expired promotion is missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM document.document
        WHERE doc_no = 'QD-GV-2026/02' AND supersedes_id IS NOT NULL AND effective_from = date '2026-07-01'
    ) THEN
        RAISE EXCEPTION 'the policy in force does not supersede the January decision';
    END IF;
END $$;
