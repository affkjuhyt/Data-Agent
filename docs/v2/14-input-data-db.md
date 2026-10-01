# 14. Thiết kế DB cho luồng dữ liệu đầu vào (nguồn → số liệu hiệu lực)

> Đối tượng: người làm S1-01, S1-02, S2-0x, S3-0x, S4-02 (Đức Long, Quân, Hải Long) và trưởng nhóm.
> Viết ngày 30/09/2026. Quy ước chung (schema theo bounded context, RLS, OCC, append-only, role) ở
> [10-database-design.md](10-database-design.md) mục 3 và **không lặp lại ở đây**. File này chốt
> **bảng, cột, khoá, ràng buộc và cách các bảng nối nhau** cho mọi thứ từ lúc tải một nguồn trong
> [04](04-sources-oil-gold.md) đến lúc số vào ClickHouse `obs` hoặc vào hồ sơ duyệt.
>
> Các task DB trên Linear (NOP-114, 117, 124, 126, 131, 108, 137) mỗi task mô tả một mảnh. Chỗ nào
> file này khác mô tả task, mục 11 ghi rõ phần cần sửa trong task.

## 0. Phạm vi

```text
KBSource/KBSourceConfig (public, có sẵn)      cấu hình nguồn, lịch, profile đọc
        │
MinIO raw/{source_id}/{sha256}                bản gốc bất biến
ingest.artifact · ingest.fetch                 bản gốc đã thấy · mỗi lần tải
ingest.release · ingest.release_processing     nội dung mới · trạng thái xử lý nó
ingest.source_state · ingest.sla_event         độ tươi
ingest.profile · ingest.mapping_version        cấu trúc đọc được · nghĩa đã duyệt
ingest.observation_candidate · parse_error     số chờ kiểm tra · dòng đọc lỗi
knowledge.extraction_run (+ event/claim/evidence của NOP-137)   nhánh văn bản
registry.entity · entity_alias · entity_relation · series · catalog_change
ingest.outbox                                  sự kiện Kafka cùng transaction
        │
governance.dq_result / review_case (NOP-104, NOP-90)   ← cổng vào, chỉ ghi quan hệ ở đây
ClickHouse obs.observation                              ← điểm cuối
```

Ngoài phạm vi: bảng governance chi tiết (NOP-90, 104, 109), alert, capability, forecast.

## 1. Tám quyết định thiết kế

| # | Quyết định | Lý do |
| --- | --- | --- |
| IN-1 | **Nguồn vẫn là `public.KBSource` + `KBSourceConfig`** (có version, `is_active`). Không tạo `registry.source`. Bảng V2 tham chiếu `source_id uuid` **không có FK** sang `public` (10 mục 4). Mỗi release ghi lại `source_config_version` đã dùng để đọc nó | 05 mục 6 "không có registry nguồn thứ hai"; phải tái lập được: release X được đọc bằng profile nào |
| IN-2 | **Tách "cách đọc" khỏi "nghĩa".** `extraction_profile` (sheet, dòng header, locale, mã thiếu, lọc dòng: thuần vật lý) nằm trong `KBSourceConfig.config`. `ingest.mapping_version` (cột thời gian + định dạng kỳ, cột nào → `series_key` nào, measure nào) nằm trong schema `ingest` và phải được duyệt. Danh sách `series:` trong seed YAML trở thành `mapping_version` v1 có `proposed_by = 'seed'` | 03 mục 5 đang để `series:` trong profile, nhưng UC03 bắt mapping phải có version, được duyệt và chống ghi đè. Để một chỗ thì người sửa profile sẽ đổi nghĩa mà không qua duyệt |
| IN-3 | **Ba mức: artifact → fetch → release.** `artifact` là mỗi nội dung khác nhau từng thấy. `fetch` là mỗi lần tải thành công (kể cả khi nội dung không đổi). `release` là khi nội dung đổi so với lần trước | 03 mục 3.3: "release vẫn được ghi để biết ngày nào nguồn đã xác nhận lại". SLA cần phân biệt "nguồn sống nhưng chưa công bố" với "nguồn chết" |
| IN-4 | **`release` bất biến; trạng thái xử lý nằm ở `release_processing`** (có `version`). Bảng này cũng là báo cáo lần chạy (số dòng mới / không đổi / trùng / lỗi) | NOP-124 bắt release append-only, còn NOP-126 lại cần trạng thái "đang chặn" cho từng release. Không thể cùng một bảng |
| IN-5 | **`available_at` gắn với từng số, kèm `time_basis`.** Lần nạp đầu một file có lịch sử (EIA từ 1987, LBMA từ 1968), ngày công bố của các số cũ được **suy từ lịch công bố khai báo** (`release_calendar`), đánh dấu `time_basis = 'calendar_inferred'`. Không đặt ngày công bố của số cũ bằng ngày tải | Nếu không làm vậy, as-of 21/09 sẽ không thấy số nào (UC13 hỏng), `detect_drop_episodes` as-of quá khứ sẽ rỗng, và detector coi 30 năm lịch sử là "dữ liệu mới" |
| IN-6 | **Nội dung candidate bất biến.** Chỉ `status`, `version`, `updated_at` được sửa, và có trigger chặn phần còn lại. Chỉ ghi candidate khi giá trị khác bản hiệu lực hiện hành (đọc qua ObservationStore), không so với candidate trước | Bất biến 2; 03 mục 3.3. So với candidate thì hỏng khi candidate trước bị từ chối trong review |
| IN-7 | **Kỳ lưu là khoảng `[event_time_start, event_time_end)` theo UTC**, ranh giới tính theo `market_timezone`. Số tức thời (tỷ giá, SJC, giá bán lẻ hiệu lực 15h00) có `start = end`. Kèm `period_code` dễ đọc (`2026-09-15`, `2026-M08`, `2026-W38`, `P:2398/QĐ-BCT`) | Một quy tắc cho D/W/M/I/P; ClickHouse không nhận NULL ở `event_time_end` |
| IN-8 | **Mọi sự kiện Kafka của luồng đầu vào đi qua `ingest.outbox`**, cùng transaction với dòng dữ liệu | ADR-V2-34; crash giữa "ghi DB" và "gửi Kafka" hiện là cách mất sự kiện |

Đã chốt ngày 30/09/2026: IN-5 = ADR-V2-43, IN-2 = ADR-V2-44, IN-4 = ADR-V2-45, role `crawler_svc` =
ADR-V2-46 (xem [07](07-decisions-and-risks.md)). IN-1, IN-3, IN-6, IN-7, IN-8 là thiết kế đề xuất, chốt thành ADR khi
nhóm duyệt.

## 2. Sơ đồ quan hệ

```text
public.KBSource 1─n public.KBSourceConfig(version_number, config{…V2 keys…})
   │ source_id (tham chiếu logic, không FK)
   ▼
ingest.artifact ──1─n── ingest.fetch ──n─1── public.KBSourceRun (run_id, logic)
   │ 1
   │ 1
ingest.release ──1─1── ingest.release_processing ──── governance.review_case (SCHEMA_DRIFT…)
   │ 1  │                      │ profile_id            │
   │    │                      ▼                       │
   │    └──1─1── ingest.profile(fingerprint)           │
   │                                                   │
   │   ingest.mapping_version(source_id, version_no, fingerprint, rules) ◄── duyệt qua review
   │ 1               │
   │ n               │ mapping_version_id
ingest.observation_candidate ─────────────► registry.series ──n─1── registry.entity
   │  │ extraction_run_id                        (series_key)          │ 1
   │  └──────── knowledge.extraction_run ◄── KBData(artifact_sha256)   ├─n entity_alias
   │                   └─ event / claim / evidence (NOP-137)           └─n entity_relation
   ▼
governance.dq_result (release, candidate, rule) → review_case → approval
   ▼
ClickHouse obs.observation (candidate_id, release_id, artifact_sha256, source_ref)
```

Mọi bảng dưới đây có `tenant_id uuid NOT NULL` + `ENABLE/FORCE RLS` qua helper của NOP-148, PK
`uuid`, thời gian `timestamptz`. Để ngắn, các dòng đó không lặp lại trong từng bảng.

## 3. Cấu hình nguồn: khoá V2 trong `KBSourceConfig.config`

Bảng có sẵn, không đổi cột. API (NOP-122) validate phần JSON bằng Pydantic `SourceConfigV2`.
Bulk Import và form đều ghi qua cùng model đó.

```yaml
# định danh và quyền
source_key: eia-brent-spot-daily       # slug duy nhất theo tenant (= `id` trong seed YAML); nhập lại không tạo trùng
licence: "US government data (EIA), ghi nguồn"
use_rights: internal_analysis          # internal_analysis | pending_review | blocked  (blocked → worker không chạy)
purpose: production                    # production | evaluation  (evaluation → nhãn "dữ liệu thử")
entity: BRENT_SPOT                     # gợi ý cho reverse schema, không phải mapping
# chạy
rate_limit: {min_interval_s: 300}      # Vietcombank: 1 request/5 phút
follow_link_pattern: null              # regex; Bộ Công Thương: bài "điều hành giá xăng dầu"
freshness_sla: P8D                     # ISO 8601; hết hạn mà không có release mới → source.sla.breached
# thời gian
release_info:                          # đọc publication_time CỦA RELEASE (có cấu trúc, không còn là câu mô tả)
  publication_time: {from: xls_cell, sheet: Contents, label: "Release Date:", time: "10:30", tz: America/New_York}
  next_release_time: {from: xls_cell, sheet: Contents, label: "Next Release Date:", time: "10:30", tz: America/New_York}
release_calendar:                      # suy ngày công bố cho SỐ LỊCH SỬ (IN-5)
  kind: weekly_after                   # weekly_after | daily_lag | monthly_day | none
  weekday: WED
  time: "10:30"
  tz: America/New_York
# đọc vật lý (IN-2)
extraction_profile:
  kind: xlsx_table                     # csv | xlsx_table (.xls và .xlsx) | json_path | sdmx | html_table | xml_path
                                       # html_table: bộ chọn CSS cho bảng (`table`), dòng (`rows`) và ô (`cells`),
                                       # dùng cả cho khối div dạng thẻ (24h.com.vn), không chỉ thẻ <table>
  archive_member: null                 # đọc một file trong zip (JODI: "world_primary.csv")
  sheet: "Data 1"
  header_row: 3
  locale: en-US                        # en-US | vi-VN | …  (T7)
  missing_markers: {"-": missing, "--": missing, "NA": not_published, "W": withheld}
  row_filter: null                     # JODI: {REF_AREA: [...], ENERGY_PRODUCT: CRUDEOIL, FLOW_BREAKDOWN: INDPROD}
```

Các loại nguồn gốc cho `release_info.*.from` (đã đối chiếu với file thật ngày 30/09/2026):
`xls_cell` (`label` khớp đúng hoặc `label_prefix`; EIA `Contents`, Pink Sheet "Updated on"),
`sdmx_dataset_attr` (IMF: `PUBLICATION_DATE` là thuộc tính của `DataSet`, không nằm trong `Header`),
`html_regex`, `json_max_date` (LBMA: ngày `d` lớn nhất), `xml_text` (Vietcombank `/ExrateList/DateTime`),
`fetch_time`.

`mapping` trong seed (Bulk Import tạo mapping v1 `seed`, không lưu vào config) chọn giá trị theo loại
profile: `column` (bảng), `field` (JSON), `select` theo chiều (SDMX), `attr` (XML). Seed
[data/sources-oil-gold.yaml](data/sources-oil-gold.yaml) đã theo dạng này cho 9 nguồn.

Các loại `release_calendar.kind`:

| kind | Quy tắc cho số có kỳ kết thúc `end` | Nguồn |
| --- | --- | --- |
| `weekly_after` | lần `weekday time tz` đầu tiên ≥ `end` | EIA spot, tồn kho |
| `daily_lag` | `end + lag` (mặc định 0) | LBMA PM (`end` = nửa đêm London) |
| `monthly_day` | ngày `day` của tháng sau `end`, giờ `time` | IMF PCPS (`day: 10`), Pink Sheet (`day: 5`) |
| `none` | không suy được → `time_basis = 'first_seen'`, `available_at` = lúc tải | nguồn không có lịch sử hoặc lịch không đều (Vietcombank, SJC, Bộ Công Thương) |

## 4. Bản gốc

### 4.1 MinIO (NOP-114)

- Bucket riêng `raw` (bật versioning + object lock compliance lúc tạo), key `raw/{source_id}/{sha256}`
  (ADR-V2-42). Service chỉ có `PutObject/GetObject/ListBucket`.
- **Không có artifact nào không thuộc nguồn nào.** Tài liệu tải tay cho V2 (OPEC MOMR, LBMA dự phòng,
  EIA futures đã dừng) đi qua một nguồn `connector: manual-upload`, có sẵn trong seed. Tải tay không
  gắn nguồn là việc của KB legacy, không vào V2. Câu hỏi còn mở của NOP-114 được chốt như vậy.

### 4.2 `ingest.artifact`: mỗi nội dung khác nhau từng thấy (append-only)

```sql
CREATE TABLE ingest.artifact (
  artifact_id      uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  source_id        uuid NOT NULL,
  sha256           char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  object_key       text NOT NULL,                 -- raw/{source_id}/{sha256}
  bytes            bigint NOT NULL CHECK (bytes >= 0),
  content_type     text NOT NULL,
  filename         text,
  fetched_from_url text,
  first_seen_at    timestamptz NOT NULL DEFAULT now(),
  first_run_id     uuid,                           -- KBSourceRun.run_id (logic)
  UNIQUE (source_id, sha256)
);
```

Màn Data Layers (tầng bản gốc, NOP-116) liệt kê từ bảng này, không phải từ MinIO.
`KBData.artifact_sha256` (NOP-114) nối bản tìm kiếm của tài liệu về đây.

### 4.3 `ingest.fetch`: mỗi lần tải thành công (append-only)

```sql
CREATE TABLE ingest.fetch (
  fetch_id     uuid PRIMARY KEY,
  tenant_id    uuid NOT NULL,
  source_id    uuid NOT NULL,
  run_id       uuid NOT NULL,                     -- KBSourceRun.run_id
  artifact_id  uuid NOT NULL REFERENCES ingest.artifact,
  fetched_at   timestamptz NOT NULL,
  http_status  smallint,
  etag         text,
  last_modified timestamptz,
  url          text NOT NULL
);
CREATE INDEX ON ingest.fetch (source_id, fetched_at DESC);
```

Khối lượng: khoảng 30 nguồn, tối đa hằng giờ, nên dưới 1.000 dòng/ngày.

## 5. Release và độ tươi

### 5.1 `ingest.release`: nội dung mới (append-only, NOP-124)

```sql
CREATE TABLE ingest.release (
  release_id             uuid PRIMARY KEY,
  tenant_id              uuid NOT NULL,
  source_id              uuid NOT NULL,
  artifact_id            uuid NOT NULL REFERENCES ingest.artifact,
  artifact_sha256        char(64) NOT NULL,
  release_seq            int NOT NULL,              -- 1, 2, 3… theo nguồn
  previous_release_id    uuid REFERENCES ingest.release,
  source_config_version  int NOT NULL,              -- KBSourceConfig.version_number đã dùng (IN-1)
  publication_time       timestamptz NOT NULL,
  publication_time_basis text NOT NULL CHECK (publication_time_basis IN ('source_declared','fetch_time')),
  next_release_time      timestamptz,
  available_at           timestamptz NOT NULL,      -- mốc mặc định cho số MỚI của release này
  system_time            timestamptz NOT NULL DEFAULT now(),
  created_by             text NOT NULL,             -- 'web_crawler' | user id (tải tay)
  UNIQUE (source_id, artifact_sha256),
  UNIQUE (source_id, release_seq)
);
```

- Web_crawler chạy mỗi nguồn một luồng (`RunPool.is_running`). `release_seq = max + 1` được
  `UNIQUE (source_id, release_seq)` bắt nếu có tranh chấp.
- Giới hạn đã biết: nội dung quay lại đúng bản cũ (A → B → A) không tạo release mới vì trùng
  `(source_id, sha256)`. Trường hợp này chỉ ghi `fetch` và một `sla_event` kind `content_reverted`
  để người vận hành xem.
- Với Bộ Công Thương (`available_at` = thời điểm hiệu lực, 04 mục 4): `release.available_at` là giờ
  đăng bài, còn thời điểm hiệu lực của giá bán nằm ở từng candidate (mục 7).

### 5.2 `ingest.release_processing`: trạng thái xử lý + báo cáo (có `version`)

```sql
CREATE TABLE ingest.release_processing (
  release_id          uuid PRIMARY KEY REFERENCES ingest.release,
  tenant_id           uuid NOT NULL,
  source_id           uuid NOT NULL,
  path                text NOT NULL CHECK (path IN ('structured','document')),
  status              text NOT NULL CHECK (status IN (
                        'received','profiled','blocked_drift','normalizing',
                        'candidates_written','parse_error','extracting','no_change')),
  profile_id          uuid,
  mapping_version_id  uuid,
  review_case_id      uuid,                          -- SCHEMA_DRIFT; các release đến khi đang chặn dùng chung case
  rows_read           int, candidates_new int, candidates_revision int,
  unchanged int, duplicates int, parse_errors int,
  error               text,
  started_at          timestamptz NOT NULL DEFAULT now(),
  finished_at         timestamptz,
  version             int NOT NULL DEFAULT 1,
  updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON ingest.release_processing (source_id, status) WHERE status IN ('blocked_drift','parse_error');
```

Chuyển trạng thái chỉ qua một hàm trong `series_extractor` (ADR-V2-32):
`received → profiled → (blocked_drift | normalizing) → candidates_written | no_change | parse_error`.
Mapping mới được duyệt thì `blocked_drift → normalizing` và các release đang chặn của nguồn được
chạy lại theo `release_seq`.

### 5.3 Độ tươi: `ingest.source_state` (có `version`) + `ingest.sla_event` (append-only)

```sql
CREATE TABLE ingest.source_state (
  source_id               uuid PRIMARY KEY,
  tenant_id               uuid NOT NULL,
  last_release_id         uuid,
  last_publication_time   timestamptz,
  last_fetch_ok_at        timestamptz,
  expected_next_release_at timestamptz,             -- next_release_time hoặc last + freshness_sla
  sla_status              text NOT NULL CHECK (sla_status IN ('on_time','overdue','not_applicable')),
  overdue_since           timestamptz,
  version                 int NOT NULL DEFAULT 1,
  updated_at              timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE ingest.sla_event (
  sla_event_id  uuid PRIMARY KEY,
  tenant_id     uuid NOT NULL,
  source_id     uuid NOT NULL,
  kind          text NOT NULL CHECK (kind IN ('breached','acknowledged','recovered','content_reverted')),
  overdue_since timestamptz,
  release_id    uuid,                                -- 'recovered' do release nào
  actor         text NOT NULL,
  note          text,
  created_at    timestamptz NOT NULL DEFAULT now()
);
```

Mỗi lần vi phạm chỉ sinh một `breached`, vì hàm chuyển `on_time → overdue` là hàm duy nhất ghi
`breached` và cùng transaction ghi outbox `source.sla.breached`. Case T3 (EIA futures dừng
05/04/2024) sẽ `overdue` ngay sau lần nạp đầu.

## 6. Cấu trúc và nghĩa

### 6.1 `ingest.profile` (append-only, NOP-126)

```sql
CREATE TABLE ingest.profile (
  profile_id        uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  release_id        uuid NOT NULL UNIQUE REFERENCES ingest.release,
  source_id         uuid NOT NULL,
  profile_kind      text NOT NULL,                  -- theo extraction_profile.kind
  columns           jsonb NOT NULL,                 -- [{position, name_raw, name_norm, dtype, locale_guess,
                                                    --   unit_guess, missing_markers_seen, null_ratio, samples[≤5]}]
  row_count         int NOT NULL,
  period_min        text, period_max text,
  fingerprint       char(64) NOT NULL,
  fingerprint_input jsonb NOT NULL,                 -- danh sách chuẩn hoá đã băm, để UI hiện diff cột
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON ingest.profile (source_id, created_at DESC);
```

`fingerprint = sha256(json.dumps({cols: sorted([(name_norm, dtype, unit_guess)]), locale,
missing_markers}))`, trong đó `locale` và `missing_markers` là giá trị **khai báo** trong
`extraction_profile`. Cột được sắp theo tên, nên đổi thứ tự cột không bị coi là drift (mapping đọc
theo tên). Đổi `price` → `settle` là drift. Sửa locale trong config (`en-US` → `vi-VN` biến
`25,760.00` thành `25,76`) cũng là drift. Nhờ vậy mọi thay đổi config làm đổi cách đọc số đều phải
qua cổng duyệt, dù config không cần duyệt (IN-2).

### 6.2 `ingest.mapping_version` (có `version`, nội dung bất biến, NOP-126)

```sql
CREATE TABLE ingest.mapping_version (
  mapping_version_id uuid PRIMARY KEY,
  tenant_id          uuid NOT NULL,
  source_id          uuid NOT NULL,
  version_no         int NOT NULL,
  fingerprint        char(64) NOT NULL,             -- áp cho profile có fingerprint này
  based_on_profile_id uuid NOT NULL REFERENCES ingest.profile,
  rules              jsonb NOT NULL,                -- xem dưới
  status             text NOT NULL CHECK (status IN
                       ('proposed','validator_rejected','approved','rejected','superseded')),
  proposed_by        text NOT NULL CHECK (proposed_by IN ('llm','human','seed')),
  proposal_meta      jsonb,                          -- {model, prompt_version, gateway_call_id} (AI-04)
  validator_result   jsonb,                          -- lỗi NOT_EQUIVALENT, kiểu, miền, đơn điệu thời gian
  review_case_id     uuid,
  created_by         text NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  approved_by        text,
  approved_at        timestamptz,
  version            int NOT NULL DEFAULT 1,
  UNIQUE (source_id, version_no),
  CHECK (status <> 'approved' OR proposed_by = 'seed' OR approved_by <> created_by)   -- maker ≠ checker
);
CREATE UNIQUE INDEX mapping_one_active ON ingest.mapping_version (source_id) WHERE status = 'approved';
```

Trigger chặn sửa `rules`, `fingerprint`, `based_on_profile_id` sau khi insert. Chỉ `status`,
`approved_*`, `review_case_id`, `version` được sửa, và luôn `WHERE version = :expected_version`.

`rules` (Pydantic `MappingRules`, IN-2):

```yaml
time:   {column: "Date", format: excel_serial, frequency: D, market_timezone: America/New_York}
values:
  - {column: "Europe Brent Spot Price FOB (Dollars per Barrel)", series_key: "EIA:RBRTE:D",
     measure: value, unit_multiplier: 0}
side_columns: []          # cột giữ lại trong source_ref nhưng không thành số (chg, pct của fixture)
```

Khi đơn vị ở nguồn khác đơn vị của chuỗi, mỗi giá trị khai báo `source_unit` + `source_multiplier` và
bước chuẩn hoá quy đổi bằng `libs/indicator` (không quy đổi trong mapping bằng tay). Ví dụ đọc thật ngày
30/09/2026: PNJ và DOJI niêm yết vàng miếng SJC theo **nghìn đồng/chỉ** (`14,110`), 24h.com.vn theo **nghìn
đồng/lượng** (`141,100`); chuỗi lưu triệu đồng/lượng (141,1). Cần đơn vị `chỉ` (1 lượng = 10 chỉ) trong thư
viện. Chọn dòng theo giá trị ô dùng `select` (`{product: "VÀNG MIẾNG SJC"}`), như chọn theo chiều của SDMX.

Bước 6 → 13 của kịch bản 27 bước chính là ba dòng: v1 `seed` `price → FIXTURE:BRENT_FEED_B:D` (approved) ·
v2 `llm` `settle → FIXTURE:BRENT_FEED_B:D` (proposed, hoặc `validator_rejected` nếu validator bắt được) ·
v3 `human` `settle → FIXTURE:BRENT_FEED_B:FUT_M1:D` measure `settlement_price` (approved bởi checker
khác người sửa). v1 → `superseded`.

## 7. Số chờ kiểm tra

### 7.1 `ingest.observation_candidate` (NOP-131, mở rộng)

```sql
CREATE TABLE ingest.observation_candidate (
  candidate_id        uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL,
  source_id           uuid NOT NULL,
  release_id          uuid NOT NULL REFERENCES ingest.release,
  series_key          text NOT NULL,
  origin              text NOT NULL CHECK (origin IN ('structured_mapping','document_extraction','manual_entry')),
  mapping_version_id  uuid REFERENCES ingest.mapping_version,
  extraction_run_id   uuid,                           -- knowledge.extraction_run
  -- kỳ (IN-7)
  event_time_start    timestamptz NOT NULL,
  event_time_end      timestamptz NOT NULL,
  market_timezone     text NOT NULL,
  period_code         text NOT NULL,
  -- bốn trục (IN-5)
  publication_time    timestamptz NOT NULL,
  available_at        timestamptz NOT NULL,
  time_basis          text NOT NULL CHECK (time_basis IN
                        ('release_declared','calendar_inferred','first_seen','document_stated')),
  system_time         timestamptz NOT NULL DEFAULT now(),
  -- giá trị
  value               numeric,                        -- không bao giờ float, không bao giờ 0 thay cho thiếu
  unit                text NOT NULL,
  unit_multiplier     smallint NOT NULL DEFAULT 0,    -- số mũ 10, như Quantity
  obs_status          text NOT NULL CHECK (obs_status IN
                        ('value','missing','not_published','withheld','confidential')),
  raw_text            text,                            -- ô gốc: '25,760.00', '25.226', '…' (T7, T9)
  confidence          numeric CHECK (confidence BETWEEN 0 AND 1),   -- chỉ nhánh văn bản/OCR
  source_ref          jsonb NOT NULL,                  -- {artifact_sha256, sheet,row,col | json_path | page,bbox,quote_id}
  content_hash        char(64) NOT NULL,               -- sha256(value, obs_status, unit, unit_multiplier)
  change_kind         text NOT NULL CHECK (change_kind IN ('new','revision')),
  previous_effective_version int,                      -- bản hiệu lực bị sửa, khi revision
  -- vòng đời
  status              text NOT NULL DEFAULT 'pending_dq' CHECK (status IN
                        ('pending_dq','dq_passed','dq_blocked','in_review','approved','rejected','published','superseded')),
  version             int NOT NULL DEFAULT 1,
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CHECK ((obs_status = 'value') = (value IS NOT NULL)),
  CHECK (event_time_end >= event_time_start),
  CHECK ((origin = 'structured_mapping') = (mapping_version_id IS NOT NULL)),
  CHECK ((origin = 'document_extraction') = (extraction_run_id IS NOT NULL)),
  UNIQUE (source_id, series_key, event_time_start, release_id, content_hash)
);
CREATE INDEX ON ingest.observation_candidate (tenant_id, series_key, event_time_start);
CREATE INDEX ON ingest.observation_candidate (release_id);
CREATE INDEX ON ingest.observation_candidate (status) WHERE status IN ('pending_dq','in_review');
```

Quy tắc ghi:

- `INSERT … ON CONFLICT DO NOTHING` theo khoá idempotency. Retry 3 lần vẫn chỉ ra 1 dòng.
- Chỉ ghi khi `content_hash` khác bản hiệu lực hiện hành của `(series_key, event_time_start)`. Bản
  hiệu lực đọc **một lần mỗi release** qua `ObservationStore.latest_by_event(series_key, range)`.
- `time_basis` và `available_at`:

| Trường hợp | `available_at` | `time_basis` |
| --- | --- | --- |
| Số mới xuất hiện ở release thường | `release.available_at` | `release_declared`, hoặc `first_seen` nếu `publication_time_basis = fetch_time` |
| Lần nạp đầu, số có kỳ trước release đầu | `min(release_calendar(end), release.available_at)` | `calendar_inferred` |
| Revision (nguồn sửa số cũ) | `release.available_at` | như dòng 1; **không** suy lịch, vì bản sửa chỉ biết được từ release này |
| Số trích từ văn bản (Bộ Công Thương) | giá bán: thời điểm hiệu lực trong bài; giá thế giới bình quân: giờ đăng | `document_stated` |

- Trigger chặn UPDATE mọi cột trừ `status`, `version`, `updated_at`. Không có DELETE.

### 7.2 `ingest.parse_error` (append-only)

```sql
CREATE TABLE ingest.parse_error (
  parse_error_id uuid PRIMARY KEY,
  tenant_id      uuid NOT NULL,
  release_id     uuid NOT NULL REFERENCES ingest.release,
  location       jsonb NOT NULL,          -- {sheet,row,col} | {json_path} | {xpath}
  raw_text       text,
  error_code     text NOT NULL,           -- AMBIGUOUS_NUMBER | BAD_PERIOD | UNKNOWN_MARKER | UNIT_MISMATCH …
  message        text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now()
);
```

Mỗi release ghi tối đa 1.000 dòng lỗi, phần còn lại chỉ đếm ở `release_processing.parse_errors`.
Lỗi `AMBIGUOUS_NUMBER` (vd `1.234` khi locale không rõ) lấy từ `libs/indicator/numeric.py` và không
bao giờ được tự chọn một cách đọc.

## 8. Danh mục và nhánh văn bản

### 8.1 `registry` (NOP-117, sửa ba điểm)

```sql
CREATE TABLE registry.entity (
  entity_id   uuid PRIMARY KEY,
  tenant_id   uuid NOT NULL,
  entity_code text NOT NULL,                        -- BRENT_SPOT, GOLD_SJC_BAR, VN_FUEL_E10RON95_III
  kind        text NOT NULL CHECK (kind IN ('commodity_price','fx','fundamental','index','fuel_retail','other')),
  name_vi text NOT NULL, name_en text NOT NULL,
  attributes  jsonb NOT NULL DEFAULT '{}',
  status      text NOT NULL CHECK (status IN ('active','retired')),
  version     int NOT NULL DEFAULT 1,
  UNIQUE (tenant_id, entity_code)
);
CREATE TABLE registry.entity_alias (                -- tách khỏi mảng aliases để ràng buộc được "không trùng"
  entity_alias_id uuid PRIMARY KEY,
  tenant_id  uuid NOT NULL,
  entity_id  uuid NOT NULL REFERENCES registry.entity ON DELETE RESTRICT,
  alias      text NOT NULL,
  alias_norm text GENERATED ALWAYS AS (lower(regexp_replace(btrim(alias), '\s+', ' ', 'g'))) STORED,
  lang       text NOT NULL CHECK (lang IN ('vi','en')),
  UNIQUE (tenant_id, alias_norm)
);
CREATE TABLE registry.entity_relation (             -- như NOP-117: EXCLUDE USING gist chống chồng khoảng
  entity_relation_id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
  from_id uuid NOT NULL REFERENCES registry.entity, to_id uuid NOT NULL REFERENCES registry.entity,
  predicate text NOT NULL CHECK (predicate IN ('SAME_UNDERLYING','SUCCEEDS','NOT_EQUIVALENT','COMPONENT_OF')),
  valid_from timestamptz NOT NULL, valid_to timestamptz,
  reason text NOT NULL, created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (from_id <> to_id), CHECK (valid_to IS NULL OR valid_from < valid_to)
);
CREATE TABLE registry.series (
  series_id        uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  series_key       text NOT NULL,                   -- EIA:RBRTE:D; ClickHouse dùng khoá này
  entity_id        uuid NOT NULL REFERENCES registry.entity ON DELETE RESTRICT,
  source_id        uuid NOT NULL,                   -- nguồn chính; nguồn nạp lịch sử ở registry.series_source
  measure          text NOT NULL CHECK (measure IN ('price','stock','production','position','index','rate','spread','return','gap')),
  price_type       text CHECK (price_type IN ('spot','futures_settlement','close','fixing','period_average','retail_cap','quote')),
  contract         text,                            -- M1
  price_side       text NOT NULL DEFAULT 'na' CHECK (price_side IN ('buy','sell','mid','na')),
  unit             text NOT NULL,                   -- mã đơn vị của libs/indicator (usd_per_barrel, vnd_per_luong…)
  unit_multiplier  smallint NOT NULL DEFAULT 0,
  currency         char(3),
  frequency        text NOT NULL CHECK (frequency IN ('D','W','M','Q','I','P')),
  market_timezone  text NOT NULL,
  decimal_precision smallint NOT NULL,
  purpose          text NOT NULL CHECK (purpose IN ('production','evaluation')),
  derived_formula  jsonb,                           -- S7-02; NULL với chuỗi gốc
  status           text NOT NULL CHECK (status IN ('draft','approved','retired')),
  version          int NOT NULL DEFAULT 1,
  UNIQUE (tenant_id, series_key)
);
CREATE TABLE registry.catalog_change (              -- append-only: mọi sửa danh mục
  change_id uuid PRIMARY KEY, tenant_id uuid NOT NULL,
  object_type text NOT NULL, object_id uuid NOT NULL,
  before jsonb, after jsonb NOT NULL, reason text NOT NULL,
  changed_by text NOT NULL, changed_at timestamptz NOT NULL DEFAULT now()
);
```

Ba điểm khác NOP-117:

1. PK là `entity_id uuid` và mã nằm ở `entity_code` (ADR-V2-27). 03 mục 3.2 đang dùng text PK,
   cần sửa 03.
2. Alias là bảng riêng. Mảng `aliases text[]` không ràng buộc được "một alias chỉ thuộc một mặt hàng".
3. Series thêm `measure`, `price_type`, `contract`, `market_timezone`, `purpose`. Không lưu `locale`:
   locale là thuộc tính của nguồn, nằm trong `extraction_profile`.

**Nguồn nạp lịch sử (thêm 30/09/2026).** Một chuỗi có đúng một nguồn chính (`series.source_id`) và có
thể có thêm một nguồn `backfill` chỉ để nạp lịch sử. Trường hợp thật: LBMA đóng file lịch sử đầy đủ
ngày 30/09/2026, nên crawl web chỉ lấy được phiên gần nhất, còn lịch sử phải tải tay:

```sql
CREATE TABLE registry.series_source (
  series_id  uuid NOT NULL REFERENCES registry.series,
  tenant_id  uuid NOT NULL,
  source_id  uuid NOT NULL,
  role       text NOT NULL CHECK (role IN ('primary','backfill')),
  PRIMARY KEY (series_id, source_id)
);
CREATE UNIQUE INDEX ON registry.series_source (series_id) WHERE role = 'primary';
```

Candidate từ nguồn `backfill` chỉ được nhận cho kỳ **trước** release đầu tiên của nguồn chính. Kỳ trùng
với số của nguồn chính thì bị bỏ, kèm lý do trong `parse_error` (`BACKFILL_OVERLAP`). Không có cơ chế
"nguồn nào ghi sau thắng".

Mã đơn vị: `libs/indicator` là nguồn duy nhất. API validate `unit` với danh sách của thư viện, DB
không giữ bảng đơn vị thứ hai.

### 8.2 Nhánh văn bản: `knowledge.extraction_run` (mới) + NOP-137

```sql
CREATE TABLE knowledge.extraction_run (
  extraction_run_id uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  release_id      uuid NOT NULL,                    -- ingest.release (logic, khác schema)
  data_id         uuid NOT NULL,                    -- KBData có artifact_sha256; page/bbox lấy từ Silver/Gold
  extractor       text NOT NULL,                    -- 'fuel_price_announcement'
  model           text NOT NULL, prompt_version text NOT NULL, gateway_call_id text,
  status          text NOT NULL CHECK (status IN ('running','succeeded','failed','rejected_quotes')),
  rejected_count  int NOT NULL DEFAULT 0,           -- proposal có quote không khớp nguyên văn → bị loại
  started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
```

`event`, `claim`, `evidence` giữ như NOP-137, thêm `extraction_run_id` (NULL khi research nhập tay,
EV-01). Candidate số từ văn bản nằm ở `ingest.observation_candidate` với `origin =
'document_extraction'`, và `evidence.subject` trỏ về `candidate_id`. Mọi thứ đi ReviewCase (UC04).

## 9. Outbox, ClickHouse và quyền

### 9.1 `ingest.outbox`

Cùng dạng với `public."OutboxEvents"` đã có (migration `a9c2e4f6b8d1`, relay `libs/shared/outbox.py`:
`FOR UPDATE SKIP LOCKED`, theo thứ tự `id`, dừng sau 20 lần lỗi), thêm `tenant_id` và nằm ở schema
`ingest` vì bảng V2 không ở `public` (ADR-V2-26). `libs/shared/outbox.py` nhận tên bảng làm tham số,
không viết relay thứ hai.

```sql
CREATE TABLE ingest.outbox (
  id           bigserial PRIMARY KEY,               -- thứ tự phát; không dùng uuid
  tenant_id    uuid NOT NULL,
  producer     text NOT NULL,                        -- 'web_crawler' | 'series_extractor' | 'semantic_extractor'
  topic        text NOT NULL CHECK (topic IN ('artifact.stored','source.sla.breached',
                 'extraction.completed','mapping.proposed')),
  event_key    text NOT NULL,                        -- source_id | release_id (03 mục 13)
  payload      jsonb NOT NULL,                       -- envelope Pydantic (ADR-V2-40)
  created_at   timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  attempts     int NOT NULL DEFAULT 0,
  last_error   text,
  dead_at      timestamptz
);
CREATE INDEX ON ingest.outbox (producer, id) WHERE published_at IS NULL AND dead_at IS NULL;
```

Relay dùng role `outbox_relay`: có policy RLS riêng `USING (true)` chỉ trên bảng này và chỉ được
`UPDATE (published_at, attempts, last_error, dead_at)`. Không role nào có `BYPASSRLS`.

### 9.2 ClickHouse `obs.observation` (NOP-108, thêm 4 cột)

Giữ bảng của 03 mục 3.2 / NOP-108, thêm:

| Cột | Kiểu | Lý do |
| --- | --- | --- |
| `time_basis` | `LowCardinality(String)` | Verifier và UI phải biết `available_at` là suy từ lịch hay là số công bố thật (IN-5) |
| `source_id` | `UUID` | Lọc theo nguồn, SLA trong câu trả lời, không phải join sang Postgres |
| `candidate_id` | `Nullable(UUID)` | Lineage về candidate; `NULL` với override không có candidate |
| `period_code` | `String` | Hiển thị và trích dẫn, không phải tính lại |

Nạp lịch sử lần đầu ghi `version_kind = 'machine'` nhưng **không** phát `observation.published`
cho từng điểm. Publisher chỉ phát một sự kiện `backfill=true` cho mỗi series, detector bỏ qua sự
kiện này (bất biến 11).

### 9.3 Ai ghi gì

| Bảng | web_crawler (`crawler_svc`) | series_extractor, semantic_extractor (`ingest_svc`) | indicator-api (`registry_svc`, `governance_svc`) | relay |
| --- | --- | --- | --- | --- |
| `KBSource*` (public) | đọc | đọc | ghi (API nguồn) | |
| `artifact`, `fetch`, `release` | INSERT | SELECT | SELECT | |
| `source_state`, `sla_event` | INSERT/UPDATE | | SELECT, INSERT `acknowledged` | |
| `release_processing` | INSERT `received` | UPDATE | SELECT | |
| `profile`, `parse_error` | | INSERT | SELECT | |
| `mapping_version` | | INSERT (`proposed`) | UPDATE trạng thái (duyệt) | |
| `observation_candidate` | | INSERT | UPDATE `status` | |
| `registry.*` | | SELECT | ghi | |
| `knowledge.extraction_run` + NOP-137 | | INSERT | UPDATE trạng thái | |
| `outbox` | INSERT | INSERT | INSERT | UPDATE `published_at` |
| ClickHouse `obs` | | | `obs_writer` INSERT, `obs_reader` SELECT | |

Role **`crawler_svc`** tách khỏi `ingest_svc` (ADR-V2-46, chốt 30/09/2026). Web_crawler có Internet
(bề mặt tấn công lớn nhất) nên không được ghi candidate hay mapping. Worker xử lý nhiều
tenant đặt `app.tenant_id` theo `tenant_id` của nguồn (lấy từ envelope hoặc `KBSource`) trước mỗi
transaction.

## 10. Đi qua bảng với năm nguồn thật

| Nguồn | Mỗi lần chạy | Release | `available_at` | Candidate |
| --- | --- | --- | --- | --- |
| EIA Brent `RBRTEd.xls` | 1 `fetch`/ngày; `artifact` mới mỗi thứ Tư | `publication_time` từ `Contents!Release Date` 10:30 ET, basis `source_declared` | lần đầu: lịch sử 1987→ theo `weekly_after WED 10:30`; sau đó = release | lần đầu khoảng 9.900 dòng `new`; mỗi tuần khoảng 5 `new` + các `revision` nếu EIA sửa |
| Fixture "Brent feed B" | tải theo tay khi demo | v1 (14 dòng), v2 (`period,settle,chg,pct`) | `fetch_time` | v1 nạp qua mapping v1; v2: `profile.fingerprint` khác → `release_processing = blocked_drift`, ReviewCase `SCHEMA_DRIFT`, 0 candidate cho tới khi mapping v3 được duyệt rồi chạy lại |
| LBMA `gold_pm.json` | 1 `fetch`/ngày | `publication_time` = `json_max_date` + nửa đêm London | lần đầu: lịch sử 1968→ theo `daily_lag`; sau đó = release | USD: 14.691 dòng lần đầu (GBP/EUR là hai series riêng nếu bật), rồi 1/ngày |
| Vietcombank XML | 1 `fetch`/giờ, sha đổi mỗi lần (`DateTime`) | mỗi giờ một release, `xml_attr DateTime` | `release_declared`; không có lịch sử (`none`) | chỉ khi mua/bán đổi; release không đổi số thì `no_change` |
| Bộ Công Thương | theo link: mỗi bài một `artifact` | giờ đăng bài (`html_regex`) | giá bán = 15h00 hiệu lực, giá TG bình quân = giờ đăng; `document_stated` | `document_extraction`: 4 giá TG (`P`, kỳ = cửa sổ 17/9–24/9) + 4 giá bán (tức thời lúc hiệu lực), mỗi số một evidence có quote; luôn `in_review` |

## 11. Sửa gì trong các task Linear

| Issue | Sửa |
| --- | --- |
| NOP-147 | Thêm role `crawler_svc`, `outbox_relay`; bật extension `btree_gist` trong migration khung |
| NOP-114 | Chốt: mọi artifact V2 thuộc một nguồn (tải tay qua nguồn `manual-upload`). Thêm bảng `ingest.artifact` + `ingest.fetch` (mục 4) |
| NOP-124 | Thêm `release_seq`, `previous_release_id`, `source_config_version`, `publication_time_basis`; độ tươi = `source_state` + `sla_event` (mục 5.3) |
| NOP-126 | Profile chỉ lưu cấu trúc; mapping có `rules` theo IN-2, `proposed_by`, `proposal_meta`, CHECK maker ≠ checker, trigger nội dung bất biến; trạng thái nạp chuyển sang `release_processing` |
| NOP-131 | Thêm `origin`, `extraction_run_id`, `time_basis`, `period_code`, `raw_text`, `confidence`, `change_kind`, `previous_effective_version`; báo cáo lần chạy = `release_processing` + `parse_error` |
| NOP-117 | PK uuid + `entity_code`; bảng `entity_alias`; thêm cột series (mục 8.1) |
| NOP-108 | Thêm `time_basis`, `source_id`, `candidate_id`, `period_code` |
| NOP-137 | Thêm `knowledge.extraction_run` và `extraction_run_id` trên event/claim |
| NOP-122 | Pydantic `SourceConfigV2` gồm `source_key`, `release_info` có cấu trúc, `release_calendar`, `extraction_profile` (mục 3) |
| Đã làm 30/09 | ADR-V2-43/44/45; 03 mục 3.2 (`time_basis`), 4, 5 (tách profile/mapping, `xml_path`, `archive_member`). Còn: 03 mục 3.2 entity PK uuid (theo Q của NOP-117) |

## 12. Thứ tự nạp lần đầu (runbook)

Nạp sai thứ tự thì hoặc số vào mà không có chuỗi để gắn, hoặc detector nhận hàng chục nghìn sự kiện
"mới". Mỗi bước có điều kiện kiểm trước khi sang bước sau.

| Bước | Làm gì | Kiểm |
| --- | --- | --- |
| 1 | Migration M0–M5 (mục 13) | `alembic check` sạch; DDL `obs` áp hai lần không đổi |
| 2 | Danh mục khởi tạo: mặt hàng, tên gọi khác, quan hệ, chuỗi (kể cả `series_source`) | Mọi `series_key` trong seed YAML có trong `registry.series` |
| 3 | Bulk Import `sources-oil-gold.yaml` với **lịch tắt** (chỉ tạo nguồn + mapping v1 `seed`) | 25 nguồn tạo, 4 bỏ qua; mỗi nguồn có profile có mapping v1 `approved` |
| 4 | Tắt phát sự kiện `observation.published` từng điểm cho lần nạp đầu (cờ `backfill` của publisher) | Detector không nhận sự kiện nào trong bước 5–7 |
| 5 | Chạy tay từng nguồn một lần, theo thứ tự: EIA → IMF → Pink Sheet (tải tay) → LBMA tải tay (lịch sử) → LBMA crawl → Vietcombank → PNJ/DOJI/24h → GPR | Mỗi release `candidates_written`; `time_basis = calendar_inferred` cho số lịch sử EIA/IMF/LBMA |
| 6 | DQ chạy trên candidate; hồ sơ duyệt phát sinh được xử lý | Không còn candidate `pending_dq`; số hồ sơ duyệt được ghi lại làm mốc |
| 7 | Publish lịch sử vào `obs` | As-of 21/09/2026 trả Brent 15/09 = 130,80; as-of 22/09 18:00 ET trả Brent 22/09 chưa có (công bố 23/09) |
| 8 | Bật lại phát sự kiện, bật lịch cho các nguồn | Release kế tiếp của EIA chỉ phát sự kiện cho các ngày mới |

Nguồn thử Brent feed B **không** nạp ở đây; nó đi theo kịch bản nghiệm thu trên tenant đánh giá riêng.

## 13. Thứ tự migration

```text
M0 NOP-147/148   schema, role, btree_gist (migration 5b7d9f1a3c6e); RLS helper (NOP-148)
M1 NOP-117       registry.entity, entity_alias, entity_relation, series, series_source, catalog_change + seed
M2 NOP-114/124   ingest.artifact, fetch, release, source_state, sla_event, outbox;  KBData.artifact_sha256
M3 NOP-126       ingest.profile, mapping_version, release_processing
M4 NOP-131       ingest.observation_candidate, parse_error
M5 NOP-108       ClickHouse obs (Job DDL riêng, không qua Alembic)
M6 NOP-137       knowledge.extraction_run, event, claim, evidence   (C2)
```

M1 và M2 chạy song song được. M3 cần M2. M4 cần M1–M3. Test chung cho mọi migration: lên → xuống →
lên sạch; UPDATE/DELETE vào bảng append-only bị từ chối **bằng role service thật**; tenant A không
đọc được B.

## 14. Cần chốt

| # | Câu hỏi | Đề xuất |
| --- | --- | --- |
| Q1 | Chấp nhận `available_at` suy từ lịch cho số lịch sử (IN-5)? Phương án khác là chỉ trả lời as-of từ ngày hệ thống bắt đầu nạp, nhưng như vậy mất UC13 trên dữ liệu thật và mất 32 đợt giảm | Chấp nhận, gắn nhãn `calendar_inferred` trên UI và trong câu trả lời |
| Q3 | LBMA | **Chốt 30/09/2026:** chỉ Gold PM USD. File lịch sử đầy đủ không còn công khai (Apache 403); phiên gần nhất lấy bằng crawl trang giá qua trình duyệt headless (bắt phản hồi `today.json`), lịch sử tải tay qua nguồn `backfill` (mục 8.1) |

Đã chốt 30/09/2026: Q2 → ADR-V2-46; Q4 → seed YAML đã đổi dạng (Bulk Import parser cho kết quả y
như cũ: 24 tạo, 4 bỏ qua, chỉ khác `notes`/`variables`).
