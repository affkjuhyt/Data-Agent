# 03. Thiết kế đích trên nền mã hiện có

> Hiện thực hoá [00-architecture-v2.md](00-architecture-v2.md) trên repo này. Mỗi mục trả lời: ở
> service nào, bảng nào, topic nào, màn hình nào. Tên chung cho mọi miền; phần riêng của dầu/vàng
> chỉ là dữ liệu cấu hình (mục 13). Thứ tự mục theo STEP 1–13 của 00.

## 1. Lớp kiến trúc → nơi hiện thực

| Lớp (00 mục 2) | Thành phần | Nơi hiện thực | Trạng thái |
| --- | --- | --- | --- |
| Ingestion | Source Registry, Connector Runtime | `KBSource*`, `platform_workers/workers/web_crawler` | Có, mở rộng |
| Ingestion | Immutable Raw | MinIO + `KBData` Bronze, khoá theo SHA-256 | Một phần (re-crawl đang xoá; sửa ở S2-02, ADR-V2-21) |
| Ingestion | Profile, Reverse Schema, Mapping, Normalize | Consumer mới `platform_workers/workers/series_extractor` | Mới |
| Ingestion | Layout/OCR, Document Structure | `kb_promote_silver/gold`, `libs/ingestion/docling_parser.py`, `PageRegion` | Có, mở rộng |
| Ingestion | Entity/Claim/Event extraction | Consumer mới `semantic_extractor` | Mới |
| Governance | DQ, Conflict, ReviewCase, HITL, Effective | Module `governance` trong `indicator-api` + `libs/dq` | Mới |
| Semantic & Knowledge Fabric | Entity, Series, Claim, Event, Evidence | Postgres schema `registry`, `knowledge`; Qdrant; Neo4j (bản chiếu) | Mới |
| Intelligence / Data API | Observation, Semantic Query, Knowledge, Graph, Forecast, Alert, Lineage | Deployable `indicator-api` (FastAPI) | Mới |
| Agent Core | Resolution, Capability Graph, Planner, Task Graph, Hybrid Executor, Verifier | `workflow_runtime` (LangGraph, executor DAG, checkpointer có sẵn) | Mở rộng |
| Agent Core | MCP | MCP Gateway (module trong `indicator-api` cho POC) + MCP client trong runtime | Mới |
| Experience | Ask, Analyst Workspace, Data Studio, Review Inbox, Alert, Report | `frontend/src/features/*` | Một phần |

Deployable mới trong POC: `indicator-api`, `forecast-worker`. Consumer mới chạy trong image
`platform_workers` (cùng quy ước loop + Kafka như `schema_crawler`). Không tách thêm microservice
trong POC (Bảng 5.6 của thiết kế v1 vẫn đúng); ranh giới module giữ để tách sau.

Bố cục mã của `indicator_api` (ADR-V2-36): một deployable, mã chia theo domain để tách sau không phải
viết lại.

```text
backend/services/indicator_api/
  api/            router FastAPI: observations, series, entities, query, governance, review, capabilities
  domains/
    registry/     entity, entity_relation, series, capability, operator (bảng registry.*)
    observation/  effective version, truy vấn as-of
    knowledge/    claim, event, evidence, relation
    governance/   dq, conflict, review_case, correction/override/approval, authority
    query/        QuerySpec, compiler (SQLGlot), operator (mục 9)
    capability/   nạp YAML, thực thi, MCP Gateway (mục 10)
  adapters/       postgres, clickhouse, kafka (envelope + outbox), minio
  capabilities/   file YAML khai báo capability (mục 10.1)
```

`domains/` không import FastAPI hay client kho; chỉ `api/` và `adapters/` được. `libs/indicator` là thư
viện tính toán thuần, không sở hữu I/O. Consumer trong `platform_workers` chỉ parse envelope rồi gọi
handler của domain (ADR-V2-37). `.importlinter` chặn package V2 import `kb_structured_data` và
MongoDB (ADR-V2-38).

## 2. Lưu trữ

| Dữ liệu | Kho | Chủ sở hữu | Lý do |
| --- | --- | --- | --- |
| Artifact gốc | MinIO, key `raw/{source_id}/{sha256}` | web_crawler | Bất biến, không bao giờ xoá |
| Source, entity, series, operator, capability | Postgres `registry` | indicator-api | Cấu hình có duyệt |
| Release, profile, mapping, candidate | Postgres `ingest` | series_extractor | Trạng thái quy trình |
| DQ result, observation_conflict, review_case, correction, override, approval | Postgres `governance` | indicator-api | Giao dịch, OCC, audit |
| Claim, event, evidence, relation | Postgres `knowledge` (nguồn sự thật) → Neo4j (bản chiếu), Qdrant (index) | indicator-api | OCC, RLS trên Postgres |
| **Effective observation (mọi version)** | ClickHouse database `obs` (tách khỏi database của Langfuse) | indicator-api | Chuỗi thời gian, truy vấn as-of |
| Feature snapshot | MinIO Parquet theo SHA-256 | forecast-worker | Tái lập backtest |
| Forecast, model registry | Postgres `forecast` (+ MLflow nếu kịp) | forecast-worker | |
| Alert | Postgres `alert` | indicator-api | Vòng đời, dedup |

Observation **candidate** (máy trích, chưa duyệt) nằm ở Postgres `ingest`; chỉ bản được duyệt
(PASS hoặc qua ReviewCase) mới vào ClickHouse. Agent chỉ đọc ClickHouse.

### 2.1 Ranh giới với đường legacy (NOP-163)

| Kho legacy | Chỉ dùng cho | Không bao giờ nhận |
| --- | --- | --- |
| Qdrant / KB (Silver → Gold, chunk + embedding) | Tài liệu: PDF, trang web, thông cáo | File của nguồn có cấu trúc V2 |
| MongoDB | Canvas, Silver staging legacy | Bất kỳ dữ liệu nghiệp vụ V2 nào; không phải nguồn sự thật |
| Postgres `kb_structured` (`kb_structured_data`) | Bảng tải lên tay ở chế độ `structured_table` | ObservationCandidate / Observation V2 |

**Nguồn có cấu trúc V2** là nguồn mà `KBSourceConfig.config` đang hiệu lực có `extraction_profile`
(mục 5). File của nguồn này vẫn vào Bronze (bản gốc), nhưng không đi Silver/Gold và không đi
`kb.structured.propose`: số liệu do `series_extractor` đọc, cắt đoạn sẽ tạo ra một bản số thứ hai
không đơn vị, không `available_at` (bất biến 4).

Cưỡng chế ở mọi lối vào, không chỉ ở nút bấm:

- `libs/shared/storage_ownership.py`: `is_v2_structured_source`, danh sách topic / module legacy.
- `libs/ingestion/storage_boundary.py`: tra từ `KBData.metadata.source_id` sang config nguồn.
- web_crawler: nguồn có profile luôn `promotion_mode = manual`, không tự đẩy Silver.
- kb_backend: promote Silver/Gold và propose bảng trả **409** `V2_STRUCTURED_SOURCE_LEGACY_PATH`.
- Consumer Silver, Gold và `kb_structured_data`: tự bỏ qua, để sự kiện phát lại hoặc bản build cũ
  cũng không lọt.
- `.importlinter` (`v2-data-boundary`) chặn import; test quét mã nguồn
  (`tests/unit/kb_backend/test_v2_storage_boundary.py`) chặn tên topic viết cứng trong package V2.

## 3. STEP 1: Canonical Data Model

### 3.1 Thời gian (bất biến 8)

Mọi observation, claim, event mang:

| Trường | Ý nghĩa | Ví dụ EIA Brent 15/09/2026 |
| --- | --- | --- |
| `event_time_start`, `event_time_end` | Kỳ mà giá trị mô tả | 2026-09-15 (ngày giao dịch) |
| `market_timezone` | Múi giờ chốt | America/New_York |
| `publication_time` | Nguồn công bố | 2026-09-16 (release thứ Tư) |
| `available_at` | Sớm nhất có thể biết (dùng cho point-in-time) | = publication_time |
| `system_time` | Hệ thống ghi nhận | thời điểm insert |

`valid_from/valid_to` dùng cho claim/event/ngưỡng (khoảng hiệu lực).

### 3.2 Bảng chính (phác thảo)

```sql
-- Postgres registry
registry.entity (entity_id text PK, kind, name_vi, name_en, aliases text[], attributes jsonb)
  -- 'BRENT_SPOT', 'BRENT_FUTURES_M1', 'GOLD_SJC_BAR', 'VN_FUEL_E10RON95_III'
registry.entity_relation (from_id, predicate, to_id, valid_from, valid_to)
  -- SAME_UNDERLYING, SUCCEEDS, COMPONENT_OF, NOT_EQUIVALENT
registry.series (series_key PK, entity_id, measure, unit, unit_multiplier, currency,
  grain, frequency, price_side, decimal_precision, locale, source_id, status, version)

-- ClickHouse obs: mọi version effective, chỉ ghi thêm
CREATE TABLE obs.observation (
  tenant_id LowCardinality(String), series_key String,
  event_time_start DateTime64(3), event_time_end DateTime64(3), market_timezone LowCardinality(String),
  value Decimal(38,10), unit LowCardinality(String), obs_status LowCardinality(String),
  publication_time DateTime64(3), available_at DateTime64(3), system_time DateTime64(3),
  release_id UUID, artifact_sha256 FixedString(64),
  version_kind LowCardinality(String),  -- machine | correction | override | revision
  effective_version UInt32, review_case_id Nullable(UUID),
  source_ref String                     -- JSON {artifact, sheet,row,col | page,bbox | url,params}
) ENGINE = ReplacingMergeTree(system_time)
PARTITION BY toYear(event_time_start)
ORDER BY (tenant_id, series_key, event_time_start, available_at, effective_version);
```

Truy vấn point-in-time luôn dùng `argMax(value, (available_at, effective_version))` với
`available_at <= :as_of AND system_time <= :as_of_system`, không dựa vào việc ReplacingMergeTree
đã gộp. Test bắt buộc: hai version chưa gộp, as_of ở giữa trả version cũ.

### 3.3 Chỉ ghi phần thay đổi

File EIA chứa toàn bộ lịch sử từ 1987 và được tải lại mỗi ngày. Chỉ ghi observation khi giá trị
khác effective version hiện hành của `(series_key, event_time)`; release vẫn được ghi để biết ngày
nào nguồn đã xác nhận lại.

### 3.4 Lõi số

`libs/indicator` (cứu từ `feat/add-task`, P0-01): `Quantity` (Decimal + đơn vị + bội số), `numeric`
(đọc số theo locale, giữ mọi cách đọc khi mơ hồ), `formula` (DAG có kiểu), `threshold`, `verifier`.
Mở rộng: `Period` thêm D, W, I (intraday), P (kỳ điều hành, lấy từ văn bản); đơn vị giá
`money_per(barrel|troy_oz|luong|litre|tonne|gallon)`; 1 lượng = 1,20565 troy oz.

## 4. STEP 2: Source + Immutable Raw

- `KBSourceConfig.config` thêm: `entity`, `licence`, `use_rights` (`internal_analysis|pending_review|blocked`),
  `release_info` (cách đọc ngày công bố), `locale`, `rate_limit`, `follow_link_pattern`,
  `freshness_sla`, `extraction_profile`. Bulk Import đọc các trường này.
- Artifact: key MinIO theo SHA-256, không ghi đè, không xoá. Re-crawl vẫn được thay **bản tìm kiếm**
  của KB (hành vi hiện tại) nhưng artifact, release, observation cũ giữ nguyên (ADR-V2-21). Chỉ tài
  liệu mới có bản tìm kiếm; nguồn có `extraction_profile` không có (mục 2.1).
- Release: một dòng `ingest.release` khi hash đổi; `publication_time` đọc theo `release_info`
  (EIA sheet `Contents` → `Release Date`; Pink Sheet "Updated on"; SDMX `PUBLICATION_DATE`; Bộ
  Công Thương: thời điểm hiệu lực trong bài).
- Theo link từ landing (`follow_link_pattern`) dùng `KBSourceCrawlQueue` có sẵn.
- Freshness: quá `freshness_sla` không có release mới → `source.sla.breached` (EIA futures dừng từ
  04/2024 là case kiểm thử).

## 5. STEP 3: Structured pipeline

```text
artifact.stored → series_extractor:
  profile (cột, kiểu, locale, đơn vị suy luận, fingerprint)
    ├ fingerprint = bản đã duyệt → chạy mapping_version hiện hành
    └ khác → CHẶN nạp, ReviewCase SCHEMA_DRIFT, gọi reverse_schema đề xuất mapping mới
  normalize (locale, đơn vị, múi giờ, mã thiếu → obs_status)
  candidate (khoá idempotency: source_id, series_key, event_time, release_id, content_hash)
  → extraction.completed → mapping.proposed → DQ (mục 8)
```

Reverse schema: LLM trả JSON theo Pydantic `MappingProposal`; validator tất định kiểm kiểu, miền
giá trị theo đơn vị, tính đơn điệu thời gian, và danh sách khái niệm không tương đương trong
`registry.entity_relation(NOT_EQUIVALENT)` (settle ≠ close, spot ≠ futures, bid ≠ ask ≠ mid). LLM chỉ
chạy khi thêm nguồn hoặc khi drift; release thường chạy mapping tất định.

Extraction profile (lưu trong config, có version) cho năm loại: `csv`, `xlsx_table`, `json_path`,
`sdmx`, `html_table`. Ví dụ Pink Sheet:

```yaml
extraction_profile:
  kind: xlsx_table
  sheet: Monthly Prices
  header_row: 5
  unit_row: 6
  period_column: 0
  period_format: "%YM%m"      # '2026M08'
  frequency: M
  missing_markers: ["…"]
  series:
    - {column: "Crude oil, Brent", series_key: "WB:PINK:BRENT:M", unit: usd_per_barrel, decimal_precision: 2}
    - {column: "Gold", series_key: "WB:PINK:GOLD:M", unit: usd_per_troy_oz, decimal_precision: 2}
```

## 6. STEP 4: Unstructured pipeline

- Silver/Gold lưu `PageRegion` (page, bbox gốc trên-trái, kích thước trang) cho đoạn và bảng.
- Cấu trúc tài liệu: gộp heading "(continued)" theo số hiệu mục; nối bảng qua trang khi trang sau
  không có header và số cột khớp; hình không có bảng lưu ảnh vùng làm evidence.
- `semantic_extractor` (sau Gold, nguồn bật `extract_knowledge`): trả Claim/Event/ObservationCandidate
  theo JSON Schema với `quote`, `page`, `bbox`, `modality` (fact/expectation/conditional/reported),
  `attributed_to`, `event_time` tách khỏi `publication_time`. Proposal không có quote khớp nguyên văn
  passage bị loại. ObservationCandidate từ tài liệu nhập pipeline structured từ bước DQ.
- Chunk và embedding giữ nguyên cho retrieval (bất biến 4).

## 7. STEP 5: Semantic Fabric

- Entity registry với alias (BRENT_SPOT: "Brent", "dầu Brent", "Europe Brent Spot"…) phục vụ
  Entity Resolution của agent.
- Quan hệ có kiểu trong `registry.entity_relation` và `knowledge.relation`; Neo4j dựng lại từ
  Postgres khi có `knowledge.verified`.
- Cross-modal join: `claim.subject` và `event.affects` trỏ `entity_id`; `series.entity_id` cùng
  không gian → từ một đợt giảm của `BRENT_SPOT` tìm được event/claim cùng entity trong cửa sổ.

## 8. STEP 6–7: Governance → Effective Data

### 8.1 DQ năm tầng

| Tầng | Ví dụ luật |
| --- | --- |
| Schema | Cột bắt buộc có mặt; kiểu đúng; mã thiếu hợp lệ theo nguồn |
| Semantic | Đơn vị khớp series; phía giá (mua/bán) có mặt với giá vàng trong nước |
| Numeric | Miền giá trị (vàng > 0); bước nhảy theo MAD cửa sổ, có khai báo regime |
| Temporal | `event_time ≤ publication_time` với giá quan sát; lịch giao dịch |
| Cross-source | Lệch so series `SAME_UNDERLYING` vượt ngưỡng → chuyển Conflict |

Luật khai báo trong `governance.dq_rule`, mức `block|warn|info`.

### 8.2 Conflict

Kiểm lần lượt: benchmark, hợp đồng, spot/futures, mốc thời gian, múi giờ, tiền tệ, đơn vị, bản
công bố. Kết quả ∈ {`NO_CONFLICT`, `REVISION`, `TIME_DIFFERENCE`, `DEFINITION_DIFFERENCE`,
`UNIT_DIFFERENCE`, `SOURCE_CONFLICT`, `UNKNOWN`}. Chỉ `SOURCE_CONFLICT`, `UNKNOWN` mở ReviewCase mặc
định; LLM chỉ được gọi để gợi ý cho `UNKNOWN`.

Kết quả lưu ở `governance.observation_conflict`, tách khỏi `kb_conflict_resolution` (xung đột **tài
liệu** trong KB: giữ bản cũ, bản mới hay gộp), là thuật toán và luồng duyệt khác (ADR-V2-41).

### 8.3 ReviewCase và effective version

```sql
governance.review_case (case_id, kind, subject_ref, status, authority_role, assignee,
  created_at, version)                       -- SCHEMA_DRIFT | DQ_BLOCK | CONFLICT | EXTRACTION_LOW_CONF
governance.correction (correction_id, case_id, target_ref, new_value | new_mapping_version,
  reason_code, note, author, created_at)     -- append-only
governance.override (override_id, case_id, target_ref, rule, reason_code, author, created_at)
governance.approval (approval_id, case_id, checker, decision, expected_version, created_at)
```

- Authority resolver chọn người duyệt theo (entity, nguồn, loại case); maker ≠ checker.
- Mọi quyết định gửi `expected_version`; lệch → `409 VERSION_CONFLICT`.
- Approved → ghi effective version mới vào ClickHouse (`version_kind = correction|override`) →
  `review.approved` → `observation.published`.
- Không có đường nào sửa artifact hoặc candidate gốc.

### 8.4 Dependency graph và tính lại

`registry.series.derived_formula` định nghĩa chuỗi dẫn xuất (spread, return, volatility, basis
spot–futures, chênh vàng trong nước). `observation.published` của chuỗi đầu vào → tính lại chuỗi
phụ thuộc → xoá cache → `indicator.recomputed`.

## 9. STEP 8: Semantic Operators và QuerySpec

Operator là hàm tất định trong `indicator-api` (module `analytics`), khai báo tham số và kết quả:

| Operator | Dùng cho |
| --- | --- |
| `fetch_series(series, window, as_of)` | Mọi câu số |
| `compare_period(series, aggregation, period, compare_to)` | UC08 |
| `returns(series, horizons, kind)` | Trend |
| `rolling_stats(series, window, stat)` | Volatility |
| `detect_drop_episodes(series, threshold, sessions, merge_days)` | UC10, UC12 |
| `event_window(anchor, before, after)` | UC10 |
| `spread`, `basis(spot, futures)`, `fx_convert`, `unit_convert` | Dẫn xuất |

QuerySpec: Pydantic/JSON Schema (`operation`, `series`, `aggregation`, `period`, `compare_to`,
`as_of`, `filters`). Compiler: validate (series tồn tại và approved, tần suất, kỳ có dữ liệu, quyền)
→ SQLGlot dựng SQL ClickHouse → thực thi → trả `Quantity` + `input_refs`. Lỗi có cấu trúc:
`NO_DATA_FOR_PERIOD`, `FREQUENCY_NOT_AVAILABLE`, `SERIES_NOT_APPROVED`, `STALE_SOURCE`, `ACCESS_DENIED`.

## 10. STEP 9–10: Agent Core

### 10.1 Capability registry

Nguồn khai báo: file YAML trong `backend/services/indicator_api/capabilities/` được
`domains/capability` nạp vào bảng `registry.capability` (bảng là thứ runtime đọc). Ví dụ:

```yaml
capability_id: EXPLAIN_PRICE_MOVEMENT
requires: [entity, period]
returns: [change, drivers, evidence]
composition: [FETCH_SERIES, COMPARE_PERIOD, FIND_EVENTS, RETRIEVE_EVIDENCE, RANK_DRIVERS,
              VERIFY_DATA, VERIFY_EVIDENCE]
implementation: {type: composite}
---
capability_id: COMPARE_PERIOD
requires: [series, period]
returns: [current_value, baseline_value, absolute_change, pct_change]
implementation: {type: deterministic, service: indicator-api, operator: compare_period}
allowed_roles: [ANALYST, EXECUTIVE, DATA_STEWARD]
slo_ms: 800
```

### 10.2 MCP

MCP Gateway (Streamable HTTP, SDK `mcp` chính thức) chạy trong `indicator-api` cho POC, phơi mỗi
capability deterministic thành một tool, lọc theo vai trò. `workflow_runtime` có MCP client xác
thực bằng service assertion HMAC hiện có. Node `nodes/sandbox/mcp.py` (POST JSON tự chế) giữ cho
endpoint ngoài cũ, không dùng cho capability mới.

### 10.3 Luồng trong runtime

```text
input_refine (có) → resolution: entity + metric + time + intent (alias registry, không đoán)
→ planner: task graph JSON chỉ gồm capability đã đăng ký (node_id, capability, inputs, depends_on,
  evidence_requirements)
→ hybrid executor (executor DAG có sẵn; gọi MCP)
→ verifier (libs/indicator/verifier: numbers_grounded, precision_supported, vintage_consistent,
  citations_resolved) + freshness + evidence sufficiency
   thiếu → replan tối đa 2 vòng → INSUFFICIENT_EVIDENCE
→ final action: answer | report | alert candidate
```

Trạng thái từng node ghi `runtime.task_run` để resume sau restart; lỗi một node chỉ chạy lại
downstream theo `depends_on`. SSE thêm `__intent`, `__task_graph`, `__indicator_value`,
`__verification`, `__progress`, `__refusal`, `__lineage`.

## 11. STEP 11: Luồng chủ động và alert

```text
observation.published / indicator.recomputed
 → luật rẻ (ngưỡng, bước nhảy) trong consumer `detector`
 → detector thống kê (MAD, thay đổi chế độ)
 → significance (so với regime, độ tươi, trùng lặp)
 → investigation: workflow_runtime chạy capability EXPLAIN_PRICE_MOVEMENT không cần người hỏi
 → verifier → alert candidate → dedup/group/cooldown/suppression → alert.raised → hộp thư
```

Dedup theo `(rule_id, entity_id, window)`; gom các alert cùng sự kiện gốc (Brent, WTI, spread,
volatility trong cùng cửa sổ) thành một alert có nhiều biểu hiện; cooldown theo rule. Không gọi LLM
trước bước significance.

## 12. STEP 12–13: Forecast và Skill

> Ngoài POC 1 tháng (05 mục 6); thiết kế giữ để làm sau nghiệm thu.

- Forecast: `forecast-worker` (CPU; statsmodels, lightgbm, scikit-learn). Feature snapshot theo
  `available_at ≤ as_of`. Baseline random walk/drift/ETS, candidate LightGBM trên log-return,
  rolling origin, khoảng conformal 80%. Kết quả là knowledge object `FORECAST`, không bao giờ
  `verified`. Model registry: bảng `forecast.model_version`; MLflow khi kịp.
- Skill: trace Langfuse + `task_run` → bảng `experience_entry` → skill candidate (file có version
  trong git) → offline eval → người duyệt → registry. Tuỳ chọn trong POC.

## 13. Kafka topic

| Topic | Producer | Consumer | Khoá |
| --- | --- | --- | --- |
| `artifact.stored` | web_crawler | series_extractor, kb.promote (đường KB) | source_id |
| `source.sla.breached` | web_crawler | detector, notification | source_id |
| `extraction.completed` | series_extractor, semantic_extractor | DQ | release_id |
| `mapping.proposed` | series_extractor | governance | source_id |
| `validation.completed` | DQ/conflict | governance | release_id |
| `review.case.created` / `review.approved` | governance | notification / publisher | case_id |
| `observation.published` | indicator-api | recompute, detector, lineage, eval | series_key |
| `indicator.recomputed` | indicator-api | detector | series_key |
| `knowledge.proposal.created` / `knowledge.verified` | semantic_extractor / governance | Neo4j projector, indexer | object_id |
| `alert.raised` | detector/runtime | notification | entity_id |
| `forecast.requested` / `forecast.completed` | runtime / forecast-worker | forecast-worker / runtime | spec_id |
| `*.dlq` | mọi consumer | vận hành | |

Topic phải khai báo trong `kafka-topics` Job và ACL trước khi dùng; `auto.create.topics.enable=false` (P0-03).

## 14. Giao diện

| Màn | Nav | Nội dung | Dùng lại |
| --- | --- | --- | --- |
| Nguồn | Data Studio | licence, entity, SLA, release gần nhất; profile + mapping + drift; chạy thử profile | `SourceRegistry.tsx`, `SourceConfigFields.tsx` |
| Entity & Series | Data Studio | entity, alias, quan hệ; series, đơn vị, phía giá | mới |
| Series Explorer | Data Studio | chuỗi, as-of (hai chế độ), version, provenance từng điểm | mới |
| Data Quality | Data Studio | kết quả 5 tầng, conflict và phân loại | mới |
| Review Inbox | Toàn cục | ReviewCase theo authority, diff, evidence (ảnh trang + bbox), correction/override, checker, 409 khi lệch version | `StructuredTableReview.tsx` |
| Alert | Toàn cục | alert đã gom nhóm, investigation, nút "hỏi tiếp" mở Ask với ngữ cảnh | mới |
| Ask / Analyst Workspace | Hỏi | khối trả lời: bảng, chart, evidence, lineage, VerifierBadge, task graph | `features/ask` |

Mọi số hiển thị qua `formatVN.ts` đã sửa (E3).

## 15. Cấu hình riêng của vertical dầu/vàng

Không file Python nào mang tên nguồn hay hàng hoá. Vertical gồm: nguồn (`data/sources-oil-gold.yaml`),
entity và quan hệ, series và extraction profile, luật DQ/conflict, authority rule, capability
composite, forecast spec, golden set. Thêm vertical mới (ví dụ gạo Việt Nam 5% trong Pink Sheet)
phải làm được chỉ bằng các cấu hình đó (06 mục 8).
