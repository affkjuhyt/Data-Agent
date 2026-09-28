-- Seed cho 4 trụ cột dữ liệu — phần Catalog / Lineage / Freshness.
--
--   docker exec -i dataagent-postgres psql -U admin -d dataagent -v ON_ERROR_STOP=1 \
--       < tests/e2e/seed-governance.sql
--
-- Chạy SAU tests/e2e/seed.sql (cần fact_sales và tenant Dev đã có).
--
-- Mục đích: kho demo có sẵn CẢ BA trạng thái độ tươi, để màn hình Hỏi dữ liệu
-- chứng minh được rằng hệ thống hạ mức khẳng định thay vì im lặng cho qua:
--
--   fact_sales      updated_at tới hôm nay,  SLA 48h  -> fresh
--   stg_orders_raw  loaded_at  5 ngày trước, SLA 24h  -> stale
--   dim_product     không khai freshness_column       -> unknown
--   dim_region      không khai freshness_column       -> unknown  (manager NULL 1/4 hàng
--                                                                  = nguyên liệu cho trụ Data Quality)
--
-- Lineage khai báo hai bậc: kafka:orders.cdc -> stg_orders_raw -> fact_sales.
--
-- Idempotent: chạy lại được, các bảng dim/stg được dựng lại nguyên vẹn.

\set tenant '''a0000000-0000-0000-0000-000000000001'''
\set warehouse '''0000000e-2e00-4000-8000-00000000000a'''
\set cfg '''0000000e-2e00-4000-8000-00000000000b'''
-- Kho demo nhìn từ crawler: job seed trên k8s truyền DW_HOST / DW_PORT (FQDN trong
-- cluster); mặc định chỉ dùng khi chạy tay. \getenv giữ nguyên giá trị mặc định khi biến môi trường không có.
\set dw_host postgres
\set dw_port 5432
\getenv dw_host DW_HOST
\getenv dw_port DW_PORT

-- ── 1. Đăng ký kho demo làm KBWarehouse ──────────────────────────────────────
-- Không có hàng này thì schema-crawler không biết kho demo tồn tại, SchemaCatalog
-- rỗng, và data_provenance.provenance_for_sql() luôn trả None — tức là câu trả
-- lời trên chat không bao giờ mang được "as of".
BEGIN;

INSERT INTO "KBWarehouse"(warehouse_id, tenant_id, service, description, is_deleted)
VALUES (:warehouse, :tenant, 'Postgres', 'Kho demo (sandbox_dev) — dữ liệu bán hàng', false)
ON CONFLICT (warehouse_id) DO UPDATE
    SET service = EXCLUDED.service, tenant_id = EXCLUDED.tenant_id, is_deleted = false;

-- config.connection là địa chỉ kho mà crawler thấy từ trong container (dw_host /
-- dw_port ở trên). psql không thay biến bên trong chuỗi $json$, nên host/port
-- được ghi đè bằng jsonb_set sau khi dựng JSON.
--
-- config.governance là phần KHAI BÁO: owner / nhãn / lineage / SLA. Crawler chỉ
-- đọc, không bao giờ đoán (backend/services/platform_workers/workers/schema_crawler/governance.py).
-- Chỉ freshness_column là đổi hành vi crawler — nó bật truy vấn watermark.
INSERT INTO "KBWarehouseConfig"(config_id, warehouse_id, version_number, is_active, config, created_by, is_deleted)
VALUES (:cfg, :warehouse, 1, true, jsonb_set(jsonb_set($json${
  "connection": {
    "host": "postgres",
    "port": 5432,
    "username": "admin",
    "database": "sandbox_dev",
    "schema": "public"
  },
  "selected_tables": [
    {"name": "fact_sales"},
    {"name": "stg_orders_raw"},
    {"name": "dim_region"},
    {"name": "dim_product"}
  ],
  "governance": {
    "defaults": {"owner": "Phòng Dữ liệu", "classification": "internal"},
    "tables": {
      "public.fact_sales": {
        "owner": "Phòng Tài chính",
        "classification": "confidential",
        "freshness_column": "updated_at",
        "freshness_sla_seconds": 172800,
        "upstream": ["sandbox_dev.stg_orders_raw"]
      },
      "public.stg_orders_raw": {
        "owner": "Phòng Dữ liệu",
        "classification": "internal",
        "freshness_column": "loaded_at",
        "freshness_sla_seconds": 86400,
        "upstream": ["kafka:orders.cdc"]
      },
      "public.dim_region":  {"owner": "Phòng Dữ liệu", "classification": "internal"},
      "public.dim_product": {"owner": "Phòng Dữ liệu", "classification": "internal"}
    }
  }
}$json$::jsonb,
    '{connection,host}', to_jsonb(:'dw_host'::text)),
    '{connection,port}', to_jsonb(:'dw_port'::int)),
  'seed-governance', false)
ON CONFLICT (config_id) DO UPDATE
    SET config = EXCLUDED.config, is_active = true, is_deleted = false;

COMMIT;

-- ── 2. Kho demo: thêm chiều, bảng nguồn thô, và cột watermark ────────────────
\connect sandbox_dev

-- fact_sales đã tồn tại (tests/e2e/seed.sql). Thêm cột watermark và kéo dữ liệu
-- tới hôm nay, giữ nguyên công thức tất định cũ để các con số không đổi.
ALTER TABLE fact_sales ADD COLUMN IF NOT EXISTS updated_at timestamptz;

INSERT INTO fact_sales(region, product, amount, returned_amount, status, order_date)
SELECT
    r.region,
    p.product,
    (1000000 + ((extract(day from d)::int * 37 + r.w * 91 + p.w * 53) % 900000))::numeric(14,2),
    CASE WHEN (extract(day from d)::int + r.w) % 7 = 0
         THEN (50000 + (extract(day from d)::int * 11 % 40000))::numeric(14,2) ELSE 0 END,
    CASE WHEN (extract(day from d)::int + p.w) % 13 = 0 THEN 'cancelled' ELSE 'done' END,
    d::date
FROM generate_series(
        (SELECT max(order_date) + 1 FROM fact_sales)::timestamp,
        now(), interval '1 day') AS d
CROSS JOIN (VALUES ('Miền Bắc', 1), ('Miền Nam', 2), ('Miền Trung', 3), ('Tây Nguyên', 4)) AS r(region, w)
CROSS JOIN (VALUES ('Áo', 1), ('Quần', 2), ('Giày', 3)) AS p(product, w);

-- Thời điểm hàng được nạp vào kho — đây mới là thứ trả lời "dữ liệu tới bao giờ",
-- khác với order_date (ngày nghiệp vụ) và khác last_altered (ngày đổi DDL).
UPDATE fact_sales SET updated_at = order_date::timestamptz + interval '2 hours';
ALTER TABLE fact_sales ALTER COLUMN updated_at SET NOT NULL;

COMMENT ON TABLE  fact_sales            IS 'Bảng sự kiện bán hàng đã hợp nhất, một hàng mỗi đơn.';
COMMENT ON COLUMN fact_sales.updated_at IS 'Thời điểm hàng được nạp vào kho (watermark độ tươi).';

-- Bảng nguồn thô, CỐ Ý để cũ: pipeline "hỏng" 5 ngày trước.
DROP TABLE IF EXISTS stg_orders_raw;
CREATE TABLE stg_orders_raw (
    raw_id     serial PRIMARY KEY,
    order_ref  text          NOT NULL,
    region     text          NOT NULL,
    product    text          NOT NULL,
    amount     numeric(14,2) NOT NULL,
    raw_status text          NOT NULL,
    loaded_at  timestamptz   NOT NULL
);
COMMENT ON TABLE stg_orders_raw IS 'Đơn hàng thô nhận từ CDC, chưa chuẩn hoá.';

INSERT INTO stg_orders_raw(order_ref, region, product, amount, raw_status, loaded_at)
SELECT
    'ORD-' || to_char(d, 'YYYYMMDD') || '-' || r.w || p.w,
    r.region, p.product,
    (900000 + ((extract(day from d)::int * 41 + r.w * 77) % 800000))::numeric(14,2),
    CASE WHEN (extract(day from d)::int + p.w) % 11 = 0 THEN 'PENDING' ELSE 'DONE' END,
    d::timestamptz + interval '3 hours'
FROM generate_series(now() - interval '35 days', now() - interval '5 days', interval '1 day') AS d
CROSS JOIN (VALUES ('Miền Bắc', 1), ('Miền Nam', 2), ('Miền Trung', 3), ('Tây Nguyên', 4)) AS r(region, w)
CROSS JOIN (VALUES ('Áo', 1), ('Quần', 2), ('Giày', 3)) AS p(product, w);

-- Chiều vùng. manager để NULL ở đúng một vùng — không phải lỗi seed, mà là
-- hiện tượng có sẵn cho trụ cột Data Quality (null ratio) đo được về sau.
DROP TABLE IF EXISTS dim_region;
CREATE TABLE dim_region (
    region_code text PRIMARY KEY,
    region_name text NOT NULL,
    manager     text
);
COMMENT ON TABLE dim_region IS 'Chiều vùng địa lý, khớp fact_sales.region theo tên.';
INSERT INTO dim_region(region_code, region_name, manager) VALUES
    ('MB', 'Miền Bắc',   'Nguyễn Văn An'),
    ('MN', 'Miền Nam',   'Trần Thị Bình'),
    ('MT', 'Miền Trung', 'Lê Hoàng Cường'),
    ('TN', 'Tây Nguyên', NULL);

DROP TABLE IF EXISTS dim_product;
CREATE TABLE dim_product (
    product_code text PRIMARY KEY,
    product_name text NOT NULL,
    category     text NOT NULL
);
COMMENT ON TABLE dim_product IS 'Chiều sản phẩm, khớp fact_sales.product theo tên.';
INSERT INTO dim_product(product_code, product_name, category) VALUES
    ('AO',   'Áo',   'Thời trang'),
    ('QUAN', 'Quần', 'Thời trang'),
    ('GIAY', 'Giày', 'Phụ kiện');

GRANT USAGE ON SCHEMA public TO sandbox_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sandbox_reader;

SELECT 'fact_sales'     AS bang, count(*) AS so_hang, max(updated_at)::text AS moi_nhat FROM fact_sales
UNION ALL SELECT 'stg_orders_raw', count(*), max(loaded_at)::text FROM stg_orders_raw
UNION ALL SELECT 'dim_region',     count(*), NULL FROM dim_region
UNION ALL SELECT 'dim_product',    count(*), NULL FROM dim_product;
