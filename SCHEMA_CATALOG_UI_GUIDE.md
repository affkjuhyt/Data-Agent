# Schema Catalog UI — hướng dẫn test nhanh

Xem trang **Knowledge → Schema Catalog** (`src/components/knowledge/schema-catalog/SchemaCatalogView.tsx`)
mà **không cần Snowflake thật** — seed thẳng bảng `"SchemaCatalog"` trong Postgres để giả lập kết quả
`schema-crawler-worker` đã crawl xong (worker/Qdrant/LiteLLM/Kafka không cần chạy cho việc xem UI).

## 1. Bật service

Cần: Postgres, Keycloak, Kong (đã thêm route `/api/schema-catalog` → `workflow-runtime` trong
`infra/kong/kong-setup.sh`), `workflow-runtime`, frontend. Dùng đúng stack đầy đủ (xem `README.md` §Full
Stack). `workflow-runtime`/`kb-backend`/`schema-crawler-worker` nằm dưới profile `runtime`, frontend
nằm dưới profile `authoring` — cần bật cả 2 profile (không có profile nào thì `up -d` chỉ bật hạ tầng
nền: Postgres/Kong/Keycloak/..., các service ứng dụng vẫn ở trạng thái `Created`, chưa chạy):

```powershell
Copy-Item .env.example .env   # nếu chưa có — điền POSTGRES_PASSWORD/KEYCLOAK_ADMIN_PASSWORD tối thiểu
docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml --profile runtime --profile authoring up -d --build

# Chú ý dấu cách: "--profile setup" và "run" là 2 token riêng — viết liền
# ("setuprun") sẽ bị hiểu thành 1 profile không tồn tại → "no such service: kong-setup"
docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml --profile setup run --rm kong-setup
```

Mở **http://localhost:5173** (frontend chạy trong container, map cổng `5173:3000` — không phải
`:3000` như khi chạy `npm run dev` trực tiếp trên máy). Nếu muốn chạy frontend ngoài Docker để có
hot-reload nhanh hơn thay vì rebuild container mỗi lần sửa code, dừng service `frontend` rồi:
```powershell
npm install
npm run dev   # http://localhost:3000, mặc định đã trỏ đúng Keycloak (:8080) + Kong (:8000)
```

Đăng nhập `helios-admin` / `H3lios@Admin01!` (dev tenant có sẵn:
`a0000000-0000-0000-0000-000000000001`, cùng warehouse Snowflake seed sẵn
`40000000-0000-0001-0000-000000000001` — xem `infra/postgres/seed.sql`). Vào **Knowledge → Schema
Catalog**.

> **Lưu ý**: `infra/docker-compose.app.yml`'s service `frontend` set biến `VITE_API_BASE_URL`, nhưng
> `src/lib/apiClient.ts` đọc `VITE_API_URL` — tên biến không khớp. Hiện không ảnh hưởng vì giá trị đặt
> trùng với default cứng trong code (`http://localhost:8000`), nhưng nếu đổi port Kong thì biến này
> sẽ không có tác dụng — chưa sửa trong lần này, để riêng nếu cần.

## 2. Seed dữ liệu (thay Snowflake bằng Postgres)

```powershell
docker exec -i dataagent-postgres psql -U admin -d dataagent
```

```sql
-- Case: catalog rỗng — không insert gì, chỉ vào tab để thấy empty state.

-- Case: có data — 2 bảng trên warehouse dev đã seed sẵn
INSERT INTO "SchemaCatalog"
  (id, tenant_id, warehouse_id, schema_name, table_name, columns, row_count,
   text_description, join_hints, schema_hash, qdrant_point_id, last_crawled_at)
VALUES
  (gen_random_uuid(), 'a0000000-0000-0000-0000-000000000001',
   '40000000-0000-0001-0000-000000000001', 'PUBLIC', 'CUSTOMERS',
   '[{"name":"customer_id","type":"NUMBER","nullable":false,"comment":"PK"},
     {"name":"email","type":"VARCHAR","nullable":false,"comment":null},
     {"name":"signup_date","type":"DATE","nullable":true,"comment":null}]'::jsonb,
   12000,
   E'Name: CUSTOMERS\nKeywords: customer, signup, email\nUse Cases:\n  - lookup customer by email',
   '["ORDERS.customer_id"]'::jsonb, 'fakehash-customers', gen_random_uuid(), now()),

  (gen_random_uuid(), 'a0000000-0000-0000-0000-000000000001',
   '40000000-0000-0001-0000-000000000001', 'PUBLIC', 'ORDERS',
   '[{"name":"order_id","type":"NUMBER","nullable":false,"comment":"PK"},
     {"name":"customer_id","type":"NUMBER","nullable":false,"comment":"FK -> CUSTOMERS"},
     {"name":"total","type":"FLOAT","nullable":false,"comment":null}]'::jsonb,
   48200,
   E'Name: ORDERS\nKeywords: order, revenue, total\nJoin hints: CUSTOMERS.customer_id',
   '["CUSTOMERS.customer_id"]'::jsonb, 'fakehash-orders', gen_random_uuid(), now());
```

Không có `SNOWFLAKE_CRAWLER_PASSWORD`/warehouse thật nào bị đụng tới — đây chỉ là ghi thẳng vào bảng mà
`process_table()` (`services/schema-crawler-worker/main.py`) lẽ ra sẽ ghi sau khi crawl.

## 3. Test case

| # | Việc làm | Lệnh / thao tác | Kỳ vọng |
|---|---|---|---|
| 1 | Empty state | Vào tab Schema Catalog khi bảng `SchemaCatalog` rỗng cho tenant này (`DELETE FROM "SchemaCatalog";`) | Bảng hiện "No tables in the schema catalog yet…" |
| 2 | Hiện danh sách | Chạy INSERT ở mục 2, reload tab | 2 dòng `PUBLIC.CUSTOMERS` / `PUBLIC.ORDERS`, đúng row count, tên warehouse hiện đúng (tra ngược từ `/api/knowledge/warehouses`) |
| 3 | Filter theo warehouse | Chọn warehouse trong dropdown | List không đổi (chỉ 1 warehouse seed sẵn) — thêm 1 `KBWarehouse` khác + 1 `SchemaCatalog` row khác `warehouse_id` để thấy filter loại bớt |
| 4 | Search | Gõ `orders` vào ô search | Chỉ còn dòng `ORDERS` |
| 5 | Chi tiết bảng | Click dòng `CUSTOMERS` | Drawer hiện đủ: row count, schema hash, mô tả, join hints, bảng cột (3 cột, đúng comment/nullable) |
| 6 | Giả lập re-crawl (đổi schema) | `UPDATE "SchemaCatalog" SET columns = columns || '[{"name":"phone","type":"VARCHAR","nullable":true,"comment":null}]'::jsonb, schema_hash = 'fakehash-customers-v2', last_crawled_at = now() WHERE table_name = 'CUSTOMERS';` rồi reload + click lại | Cột `phone` xuất hiện trong drawer, `Last Crawled` cập nhật thời gian mới |
| 7 | Cách ly tenant | Insert 1 row với `tenant_id` khác (không phải `a0000000-...0001`) | Row đó **không** hiện trong UI (endpoint lọc theo `X-Tenant-ID` của user đăng nhập) |

## 4. Dọn dẹp

```sql
DELETE FROM "SchemaCatalog" WHERE warehouse_id = '40000000-0000-0001-0000-000000000001';
```

```powershell
docker compose -f infra/docker-compose.yml -f infra/docker-compose.local.yml down   # giữ volume
```
