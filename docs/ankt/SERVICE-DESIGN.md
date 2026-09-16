# SERVICE\-DESIGN

# Thiết kế service, kết nối, database và các luồng — nền tảng Data Agent đa chỉ số

> Phiên bản 15/09/2026 · Đối tượng: kiến trúc sư, trưởng nhóm backend/data/frontend, DevOps, nhóm Data Research\.<br>Tiếp nối [PLATFORM\\\-ARCHITECTURE\\\.md](PLATFORM-ARCHITECTURE.md)\. Sơ đồ: [15](diagrams/15-service-landscape.puml) bản đồ service · [16](diagrams/16-service-connections.puml) kết nối · [17](diagrams/17-database-ownership.puml) database · [18](diagrams/18-dataflow-end-to-end.puml) luồng dữ liệu · [19](diagrams/19-bf-indicator-lifecycle.puml)–[21](diagrams/21-bf-alert-handling.puml) luồng nghiệp vụ · [22](diagrams/22-uf-executive.puml)–[23](diagrams/23-uf-analyst.puml) luồng giao diện · [24](diagrams/24-seq-indicator-page.puml) sequence trang chỉ số\.

## 0\. Tóm tắt

- Hiện có **17 service ứng dụng** \+ hạ tầng \(Kong, Keycloak, LiteLLM, Langfuse, 11 datastore\)\. Chúng phục vụ tốt hỏi đáp tài liệu và SQL trên warehouse\.
- Để phân tích **vô số chỉ số**, còn thiếu **14 service**: 5 cho domain chỉ số, 6 cho ingestion dữ liệu có cấu trúc, 3 cho chất lượng/vận hành; và **5 service hiện có cần mở rộng**\.
- Nguyên tắc kết nối: **REST đồng bộ** cho truy vấn, **sự kiện Kafka** cho thay đổi dữ liệu, **MCP** chỉ giữa agent và công cụ, **SSE** tới giao diện\.
- Nguyên tắc dữ liệu: **mỗi service sở hữu schema của mình**, service khác đọc qua API hoặc sự kiện; quan sát chuỗi thời gian nằm ở **ClickHouse**, metadata ở **Postgres**\.
- POC không triển khai 14 deployable riêng: gộp thành **3 deployable** \(mục 9\) nhưng giữ ranh giới module để tách sau\.

## 1\. Hiện trạng service và khoảng thiếu

### 1\.1 Service hiện có

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295499&cot=14)*

| Service | Vai trò | Datastore | Đánh giá cho đa chỉ số |
| --- | --- | --- | --- |
| frontend | Ask, Knowledge Ops, Builder, Settings | — | Mở rộng: catalog, trang chỉ số, dashboard, Data Studio |
| kong, keycloak | Gateway, SSO | Postgres | Dùng lại |
| auth\-api | Auth, API key, secret | Postgres, OpenBao, Keycloak | Mở rộng: quyết định quyền theo phân loại dữ liệu |
| flow\-builder | CRUD agent, workflow, canvas | Postgres, Mongo, Redis | Dùng lại |
| workflow\-runtime | Orchestrator, agent graph, DB connector, schema catalog, memory | Postgres, Mongo, Redis, Qdrant, Kafka, MinIO, OpenBao, Langfuse, ClickHouse | Mở rộng: router/supervisor, MCP client, verifier |
| sandbox\-executor/runner | SQL read\-only, Python | Postgres, Snowflake, ClickHouse, MinIO, Redis, OpenBao | Dùng lại |
| kb\-backend | Upload, web ingest, warehouse, structured table, conflict | Postgres, Mongo, MinIO, Qdrant, Neo4j, Kafka, Redis | Mở rộng: document registry, trang/bbox, hiệu lực văn bản |
| kb\-promote\-silver/gold | Docling, chunk, embedding | Postgres, Mongo, MinIO, Qdrant, Neo4j | Mở rộng: lưu trang/bbox, OCR |
| kb\-structured\-data | Đề xuất schema bảng xlsx/csv | Postgres | Mở rộng: nhiều sheet/bảng |
| kb\-conflict\-resolution | Phát hiện mâu thuẫn văn bản | Postgres, Qdrant | Dùng lại |
| model\-service | Proxy embed/rerank | Postgres, Redis | Dùng lại |
| release\-worker | Pipeline phát hành ứng dụng | Kafka, MinIO, Postgres | Dùng lại \(không phải data release\) |
| platform\-workers \(audit, memory, schema\-crawler\) | Consumer Kafka | Postgres, Qdrant | Dùng lại |
| llm\-exporter | Probe LLM → Prometheus | — | Dùng lại |

### 1\.2 Năng lực cần có → service đảm nhận

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295523&cot=14)*

| Năng lực | Hiện trạng | Service |
| --- | --- | --- |
| Quản lý định nghĩa chỉ số, công thức, ngưỡng theo kỳ, phiên bản, duyệt | Không có | indicator\-catalog\-svc \(mới\) |
| Lưu và truy vấn quan sát theo vintage, phát hành append\-only | Không có | observation\-svc \(mới\) |
| Tính chỉ số tất định, căn kỳ, phân rã, cache | Không có \(LLM tự tính / SQL\) | compute\-svc \(mới\) |
| Cảnh báo theo ngưỡng và bất thường | Không có | alert\-svc \(mới\) |
| Bản tin, báo cáo định kỳ | Chỉ export xlsx trong runtime; UI lịch báo cáo không có backend | report\-svc \(mới\) |
| Đăng ký nguồn, giấy phép, lịch công bố | Không có | source\-registry\-svc \(mới\) |
| Điều phối thu thập theo lịch, backfill | Không có | ingestion\-orchestrator \(Dagster\) \(mới\) |
| OCR, layout, bảng, VLM có toạ độ | Docling tắt OCR, không lưu trang | extraction\-svc \(mới\) |
| Mapping ô/cột → biến, chuẩn hoá đơn vị/FX/kỳ | Không có | mapping\-svc \(mới\) |
| DQ rules, reconciliation | Chỉ kiểm tra rủi ro SQL | data\-quality\-svc \(mới\) |
| Duyệt maker–checker cho số liệu | Chỉ duyệt schema bảng xlsx | curation\-svc \(mới\) |
| Golden set, chạy eval, regression theo dữ liệu | Eval offline viết tay | eval\-svc \(mới\) |
| Lineage đầu–cuối, phân tích tác động | Chỉ cột upstream | metadata\-lineage\-svc \(mới\) |
| Hộp thư, email, đẩy realtime | Chỉ node send\_notification | notification\-svc \(mới\) |
| Công cụ cho agent theo chuẩn MCP | Node MCP tự định dạng | mcp\-gateway \(mới\) |

## 2\. Danh mục service mục tiêu

Sơ đồ: [15\\\-service\\\-landscape](diagrams/15-service-landscape.puml)\.

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295540&cot=14)*

| Domain | Service | Trạng thái | Kiểu | Scale |
| --- | --- | --- | --- | --- |
| Truy cập | frontend · kong · keycloak · auth\-api | mở rộng / dùng lại | web, gateway | ngang |
| Agent | workflow\-runtime | mở rộng | API \+ SSE | ngang theo CCU |
| Agent | mcp\-gateway | mới | MCP server | ngang |
| Chỉ số | indicator\-catalog\-svc | mới | REST | 2 replica |
| Chỉ số | observation\-svc | mới | REST \+ consumer | ngang \(đọc\) |
| Chỉ số | compute\-svc | mới | REST \+ consumer | ngang |
| Chỉ số | alert\-svc | mới | consumer \+ REST | 1–2 replica |
| Chỉ số | report\-svc | mới | REST \+ job | theo hàng đợi |
| Ingestion | source\-registry\-svc | mới | REST | 1–2 replica |
| Ingestion | ingestion\-orchestrator \(Dagster webserver \+ daemon \+ code location connectors\) | mới | job | theo job |
| Ingestion | extraction\-svc | mới | consumer GPU/CPU | theo hàng đợi, GPU batch |
| Ingestion | mapping\-svc | mới | consumer \+ REST | ngang |
| Ingestion | data\-quality\-svc | mới | consumer \+ REST | ngang |
| Ingestion | curation\-svc | mới | REST \+ consumer | 2 replica |
| Tri thức | kb\-backend \+ workers | mở rộng | REST \+ consumer | như hiện tại |
| Chất lượng | eval\-svc | mới | REST \+ job | theo job |
| Vận hành | metadata\-lineage\-svc | mới | consumer \+ REST | 1–2 replica |
| Vận hành | notification\-svc | mới | consumer \+ SSE | ngang |

## 3\. Thiết kế từng service mới

Mọi service mới: Python FastAPI \(thống nhất với backend hiện có\), OpenAPI là hợp đồng, xác thực nội bộ bằng `X\-Service\-Assertion` \(HMAC đang dùng giữa runtime và kb\-backend\), `trace\_id` OTEL truyền qua cả HTTP header và envelope sự kiện, RLS theo `tenant\_id` trên Postgres\.

### 3\.1 indicator\-catalog\-svc

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295545&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | Taxonomy domain/sub\-domain; chỉ số và phiên bản; biến nguyên tử; formula DAG; binding biến ↔ series; ngưỡng theo kỳ; codelist \(SDMX\); quy trình duyệt |
| API | GET /indicators?q=&amp;domain=&amp;status= · GET /indicators/\{id\} · POST /indicators · POST /indicators/\{id\}/versions · POST /versions/\{vid\}/submit\|approve\|reject · GET /taxonomy · GET /variables/\{id\} · GET /codelists/\{id\} · POST /indicators:import\-yaml · GET /indicators/\{id\}/impact |
| Sự kiện phát | indicator\.version\.submitted, indicator\.version\.approved, indicator\.deprecated |
| Sự kiện nhận | — |
| Dữ liệu | PG indicator: taxonomy\_node, indicator, indicator\_version, variable, formula\_node, binding, threshold, codelist, code, approval; Qdrant indicator\_index \(tên, bí danh, định nghĩa\); Neo4j nhãn Concept/Indicator/Variable |
| Ràng buộc chính | Sub\-domain thuộc domain; polarity bắt buộc; công thức kiểm tra đơn vị và stock/flow; ngưỡng cùng level không chồng valid\_from/valid\_to; phiên bản đã duyệt là bất biến |
| Phụ thuộc | kb\-backend \(kiểm tra legal\_basis\_doc\_id tồn tại\) |

### 3\.2 observation\-svc

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295546&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | Series \(tổ hợp biến \+ chiều\); release/vintage; ghi quan sát append\-only; truy vấn point\-in\-time; rút lại \(retraction\) có lý do |
| API | GET /series?variable=&amp;REF\_AREA= · GET /series/\{key\}/observations?from=&amp;to=&amp;vintage=latest\|as\_of:&lt;date&gt;\|release:&lt;id&gt; · GET /series/\{key\}/vintages · GET /releases/\{id\} · POST /releases/\{id\}:retract \(quyền steward\) |
| Sự kiện nhận | review\.approved \(lô quan sát đã duyệt\) · api\.batch\.ready \(API/SDMX không cần duyệt thủ công, vẫn qua DQ\) |
| Sự kiện phát | observation\.published \(key: series\_key\), release\.retracted |
| Dữ liệu | ClickHouse obs\.observation; PG observation: series, release, retraction; Redis cache truy vấn nóng |
| Idempotency | Khoá tự nhiên \(series\_key, period, release\_id\); ghi lại cùng khoá không tạo bản mới |
| NFR | Truy vấn 1 chuỗi 50 năm &lt; 100 ms p95; ghi bulk ≥ 100k dòng/phút |

**ClickHouse \(phác thảo\):**

```sql
CREATE TABLE obs.observation (
  tenant_id      LowCardinality(String),
  series_key     String,
  period         String,            -- '2024', '2024-Q2', '2024-06'
  period_start   Date,
  value          Decimal(38, 6),
  unit           LowCardinality(String),
  unit_multiplier Int8,
  obs_status     LowCardinality(String),   -- estimate | preliminary | official | final
  release_id     UUID,
  release_date   Date,
  vintage_label  LowCardinality(String),
  source_ref     String,            -- JSON {doc_id, page, table, cell, bbox} | {api, params}
  classification LowCardinality(String),
  ingested_at    DateTime64(3)
) ENGINE = ReplacingMergeTree(ingested_at)
PARTITION BY toYear(period_start)
ORDER BY (tenant_id, series_key, period_start, release_date, release_id);

-- Point-in-time: giá trị mới nhất đã công bố tại ngày as_of
SELECT series_key, period, argMax(value, release_date) AS value, max(release_date) AS release_date
FROM obs.observation
WHERE series_key = {key} AND release_date <= {as_of}
GROUP BY series_key, period ORDER BY period;
```

Row policy ClickHouse theo `tenant\_id` và `classification`, đồng bộ với quyết định quyền của auth\-api\.

### 3\.3 compute\-svc

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295548&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | Nhận Analysis Query \(IQL\) → kiểm tra → lập kế hoạch → lấy quan sát → tính formula DAG \(ratio, sum, growth, share, fx\_convert\) → căn kỳ stock/flow → làm tròn theo decimal\_precision → chọn ngưỡng theo kỳ dữ liệu → phân rã biến động; cache kết quả |
| API | POST /query \(IQL\) · POST /compute/decompose · POST /compute/compare\-threshold · GET /indicators/\{id\}/values?vintage= · POST /query:validate |
| Sự kiện nhận | observation\.published, release\.retracted, indicator\.version\.approved → tính lại giá trị phụ thuộc |
| Sự kiện phát | indicator\.recomputed \(giá trị cũ/mới, trạng thái ngưỡng cũ/mới\) |
| Dữ liệu | ClickHouse obs\.indicator\_value \(cache có phiên bản engine\); Redis cache truy vấn; đọc indicator qua API \(cache 5 phút\) |
| Lỗi có cấu trúc | INDICATOR\_NOT\_APPROVED, NO\_DATA\_FOR\_PERIOD, FREQUENCY\_NOT\_AVAILABLE, VINTAGE\_NOT\_FOUND, ACCESS\_DENIED → agent dùng để hỏi lại hoặc từ chối đúng |
| Kiểm thử | Golden calculation \(ECO\-009\) chạy như unit test mỗi lần build |

### 3\.4 alert\-svc

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295551&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | Luật cảnh báo \(chạm cảnh báo/trần, đổi trạng thái, bất thường thống kê, dữ liệu quá SLA, lệch nguồn\); dedup; mức độ; người phụ trách; vòng đời open → acknowledged → resolved/false\_positive |
| API | GET /alerts?status=&amp;domain=&amp;indicator= · POST /alerts/\{id\}:ack\|resolve · GET/POST /alert\-rules |
| Sự kiện nhận | indicator\.recomputed, source\.sla\.breached, reconciliation\.failed |
| Sự kiện phát | alert\.raised, alert\.resolved |
| Dữ liệu | PG alert: alert\_rule, alert, alert\_event \(audit từng bước\) |

### 3\.5 report\-svc

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295555&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | Template bản tin; sinh báo cáo từ khối nội dung \(chỉ số, biểu đồ, bảng, đoạn do agent soạn có trích dẫn\); render DOCX/PDF; lịch định kỳ; quy trình duyệt |
| API | POST /reports · GET /reports/\{id\} · POST /reports/\{id\}:render\|submit\|approve · GET/POST /report\-schedules |
| Sự kiện | nhận report\.requested; phát report\.completed |
| Dữ liệu | PG report: template, report, report\_block, schedule; MinIO reports |
| Phụ thuộc | compute\-svc \(số\), workflow\-runtime \(đoạn văn\), kb\-backend \(trích dẫn\) |

### 3\.6 source\-registry\-svc

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295558&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | Nguồn, cơ quan công bố, giấy phép, phân loại, lịch công bố dự kiến, SLA độ tươi, cấu hình connector \(tham chiếu secret\), sức khoẻ nguồn |
| API | GET/POST /sources · GET /sources/\{id\}/runs · GET /sources/\{id\}/artifacts · POST /sources:import\-yaml \(từ docs/ankt/data/sources\.yaml\) · GET /release\-calendar |
| Sự kiện phát | source\.release\.detected, source\.sla\.breached |
| Dữ liệu | PG ingest\_source: source, publisher, license, schedule, artifact \(sha256, uri, size, fetched\_at\), run; OpenBao cho credential |

### 3\.7 ingestion\-orchestrator \(Dagster\)

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295561&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | Chạy connector theo lịch/sensor lịch công bố; retry; backfill; theo dõi lần chạy |
| Thành phần | Dagster webserver, daemon, code location ankt\-connectors \(plugin: http\-file, web\-crawl\+diff, rest\-api, sdmx, db\-incremental, sftp/email, rss\) |
| Sự kiện phát | artifact\.stored \(key: source\_id\), api\.batch\.ready |
| Dữ liệu | PG dagster \(run storage\); MinIO ankt\-raw \(\{source\_id\}/\{yyyy\}/\{sha256\}\.\{ext\}\) |
| Giai đoạn đầu | K8s CronJob chạy cùng package connector; chuyển Dagster khi &gt; ~20 nguồn |

### 3\.8 extraction\-svc

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295563&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | PDF/ảnh → render trang → layout → OCR → cấu trúc bảng → \(tuỳ chọn\) VLM → JSON có trang/bbox/ô/độ tin cậy |
| API | GET /extractions/\{id\} · GET /artifacts/\{sha\}/pages/\{n\}\.png · POST /extractions:rerun |
| Sự kiện | nhận artifact\.stored \(pdf/ảnh\); phát extraction\.completed |
| Dữ liệu | MinIO ankt\-derived \(ảnh trang, JSON\); PG ingest\_extraction \(job, model\_version, số trang, thời gian\) |
| Scale | Worker GPU theo lô, hàng đợi riêng theo độ ưu tiên; không chạy cùng giờ cao điểm chat khi dùng chung GPU |
| Model | Chọn qua benchmark \(xem README mục 7\); lưu model\_version vào mọi kết quả |

### 3\.9 mapping\-svc

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295568&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | Ánh xạ ô/cột/trường API → variable \+ chiều \+ kỳ \+ đơn vị; chuẩn hoá bội số, tiền tệ/FX, định dạng kỳ, codelist; LLM gợi ý khi chưa có quy tắc; ghi nhớ quy tắc đã duyệt |
| API | GET/POST /mapping\-rules · POST /mappings:suggest · GET /mappings/\{extraction\_id\} |
| Sự kiện | nhận extraction\.completed, api\.batch\.ready; phát mapping\.proposed |
| Dữ liệu | PG ingest\_mapping: mapping\_rule \(theo source \+ mẫu bảng\), candidate\_observation |

### 3\.10 data\-quality\-svc

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295571&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | Rule: miền giá trị, đơn vị hợp lệ, tổng thành phần = tổng \(dung sai\), biến động bất thường so kỳ trước, so release trước, so nguồn đối chiếu; phân loại mức: chặn / cảnh báo / thông tin |
| API | GET /dq/results?release= · GET/POST /dq/rules · GET /reconciliations?indicator= |
| Sự kiện | nhận mapping\.proposed, indicator\.recomputed \(reconcile với số chính thức\); phát validation\.completed, reconciliation\.failed |
| Dữ liệu | PG ingest\_dq: dq\_rule, dq\_result, reconciliation |

### 3\.11 curation\-svc

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295574&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | Hàng đợi duyệt theo nguồn/domain; maker–checker \(người duyệt 2 khác người duyệt 1\); sửa giá trị kèm lý do; duyệt hàng loạt; SLA duyệt |
| API | GET /reviews?status=&amp;assignee= · GET /reviews/\{id\} \(candidate \+ ảnh trang \+ DQ\) · POST /reviews/\{id\}:edit\|approve\|reject |
| Sự kiện | nhận validation\.completed; phát review\.approved, review\.rejected |
| Dữ liệu | PG ingest\_review: review\_task, review\_decision, value\_edit |
| Tái dùng | Giao diện StructuredTableReview và mô hình ReviewCenter hiện có |

### 3\.12 eval\-svc

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295576&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | Quản lý golden set; sinh case từ quan sát \+ template câu hỏi; chạy eval qua workflow\-runtime; scorer \(giá trị trong dung sai, nguồn/trang, vintage, trạng thái ngưỡng, từ chối\); regression khi dữ liệu hoặc chỉ số đổi; xuất gate cho release |
| API | GET/POST /golden\-cases · POST /golden\-cases:generate · POST /eval\-runs · GET /eval\-runs/\{id\} · GET /eval\-runs:compare?a=&amp;b= |
| Sự kiện | nhận observation\.published, indicator\.version\.approved; phát eval\.completed |
| Dữ liệu | PG eval: golden\_case, case\_version, eval\_run, case\_result; Langfuse datasets/scores để xem trace từng case |
| Tái dùng | model/eval \(scorer, thresholds, gate\) chuyển thành thư viện dùng chung |

### 3\.13 metadata\-lineage\-svc

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295578&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | Đồ thị lineage: Source → Artifact → Extraction → Observation → IndicatorValue → Answer/Report/Alert; phân tích tác động khi số liệu hoặc chỉ số đổi |
| API | GET /lineage/\{entity\_type\}/\{id\}?direction=up\|down&amp;depth= · GET /impact?release= |
| Sự kiện nhận | artifact\.stored, extraction\.completed, observation\.published, indicator\.recomputed, answer\.logged, report\.completed |
| Dữ liệu | Neo4j \(database riêng lineage\) |

### 3\.14 notification\-svc

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295582&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | Hộp thư trong ứng dụng, email, Slack/Teams; tuỳ chọn nhận theo người dùng; đẩy realtime qua SSE |
| API | GET /notifications · POST /notifications/\{id\}:read · GET /notifications/stream \(SSE\) · GET/PUT /preferences |
| Sự kiện nhận | alert\.raised, report\.completed, review\.assigned, eval\.completed |
| Dữ liệu | PG notification; Redis pub/sub cho fan\-out SSE |

### 3\.15 mcp\-gateway

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295587&cot=14)*

| Mục | Thiết kế |
| --- | --- |
| Trách nhiệm | Máy chủ MCP \(Streamable HTTP\) cho 7 nhóm tool: indicator, data, compute, source, warehouse, intl, report; ánh xạ tool → REST nội bộ; lọc tool theo vai trò sub\-agent và quyền người dùng |
| Xác thực | Nhận danh tính người dùng từ runtime \(service assertion kèm roles\), chuyển tiếp xuống service đích |
| Dữ liệu | Không lưu trạng thái; log gọi tool vào Langfuse |
| Kênh ngoài | Cho phép client MCP bên ngoài \(IDE, BI\) qua Kong khi được cấp quyền |

### 3\.16 Service hiện có cần mở rộng

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295596&cot=14)*

| Service | Mở rộng |
| --- | --- |
| workflow\-runtime | Router độ phức tạp; supervisor \+ sub\-agent; MCP client; verifier số; evidence frame \(indicator, vintage, doc, page\); phát answer\.logged |
| kb\-backend | source\_document \(số hiệu, cơ quan, ngày ban hành, hiệu lực, sha256\); API ảnh trang \+ bbox; lọc văn bản theo effective\_at |
| kb\-promote\-silver/gold | Lưu page\_no, bbox cho chunk/bảng; OCR; gắn doc\_id với source\-registry |
| kb\-structured\-data | Nhiều sheet, nhiều bảng mỗi file; đẩy kết quả sang mapping\-svc thay vì chỉ tạo bảng kb\_structured |
| auth\-api | Endpoint quyết định quyền theo phân loại \(ABAC\); đồng bộ row policy ClickHouse |
| frontend | Catalog, trang chỉ số, dashboard, hộp cảnh báo, báo cáo, Data Studio |

## 4\. Cách các service kết nối

Sơ đồ: [16\\\-service\\\-connections](diagrams/16-service-connections.puml)\.

### 4\.1 Nguyên tắc

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295604&cot=14)*

| Kiểu | Dùng khi | Công nghệ | Quy ước |
| --- | --- | --- | --- |
| REST đồng bộ | Truy vấn cần kết quả ngay \(UI, agent, compute đọc observation\) | HTTP/JSON, OpenAPI | Timeout ≤ 5 s nội bộ; retry chỉ với GET; circuit breaker |
| Sự kiện | Dữ liệu thay đổi, xử lý nhiều bước, fan\-out | Kafka | Envelope chuẩn; at\-least\-once; consumer idempotent |
| MCP | Agent gọi công cụ | MCP Streamable HTTP | Chỉ runtime ↔ mcp\-gateway \(và client ngoài được cấp quyền\) |
| SSE | Đẩy tới trình duyệt | HTTP SSE qua Kong | Stream câu trả lời, thông báo |
| Truy cập DB trực tiếp | Chỉ service sở hữu | — | Service khác không đọc chéo schema |

### 4\.2 Envelope sự kiện

```json
{
  "event_id": "uuid", "type": "observation.published", "version": 1,
  "occurred_at": "2026-10-05T08:00:00Z", "tenant_id": "…", "trace_id": "…",
  "source_id": "vn-mof-debt-bulletin", "release_id": "uuid", "artifact_sha256": "…",
  "data": { "series_keys": ["…"], "periods": ["2024"], "revision": true }
}
```

### 4\.3 Danh mục topic

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295614&cot=14)*

| Topic | Producer | Consumer | Partition key | Lưu giữ |
| --- | --- | --- | --- | --- |
| source\.release\.detected | Dagster | source\-registry\-svc, lineage | source\_id | 30 ngày |
| artifact\.stored | Dagster | extraction\-svc, kb\-promote\-silver, lineage | source\_id | 30 ngày |
| api\.batch\.ready | Dagster | mapping\-svc | source\_id | 7 ngày |
| extraction\.completed | extraction\-svc | mapping\-svc, lineage | artifact\_sha256 | 30 ngày |
| mapping\.proposed | mapping\-svc | data\-quality\-svc | release\_id | 30 ngày |
| validation\.completed | data\-quality\-svc | curation\-svc | release\_id | 30 ngày |
| review\.approved / review\.rejected | curation\-svc | observation\-svc | release\_id | 90 ngày |
| observation\.published | observation\-svc | compute\-svc, eval\-svc, lineage | series\_key | 90 ngày |
| release\.retracted | observation\-svc | compute\-svc, eval\-svc, alert\-svc | release\_id | 90 ngày |
| indicator\.version\.approved | indicator\-catalog\-svc | compute\-svc, eval\-svc, lineage | indicator\_id | vĩnh viễn \(compacted\) |
| indicator\.recomputed | compute\-svc | alert\-svc, data\-quality\-svc, lineage | indicator\_id | 30 ngày |
| reconciliation\.failed | data\-quality\-svc | alert\-svc | indicator\_id | 30 ngày |
| source\.sla\.breached | source\-registry\-svc | alert\-svc | source\_id | 30 ngày |
| alert\.raised / alert\.resolved | alert\-svc | notification\-svc | indicator\_id | 90 ngày |
| report\.requested / report\.completed | report\-svc | report worker, notification\-svc | report\_id | 30 ngày |
| eval\.completed | eval\-svc | notification\-svc | run\_id | 30 ngày |
| answer\.logged | workflow\-runtime | lineage, eval\-svc | conversation\_id | 30 ngày |
| \*\.dlq | mọi consumer | vận hành | — | 30 ngày |

### 4\.4 Ma trận gọi REST

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295625&cot=14)*

| Bên gọi → | catalog | observation | compute | alert | report | kb\-backend | runtime | curation | extraction |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| frontend \(qua Kong\) | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ \(ảnh trang\) |
| mcp\-gateway | ✓ | ✓ | ✓ |  | ✓ | ✓ |  |  |  |
| compute\-svc | ✓ | ✓ |  |  |  |  |  |  |  |
| report\-svc |  |  | ✓ |  |  | ✓ | ✓ |  |  |
| eval\-svc | ✓ | ✓ |  |  |  |  | ✓ |  |  |
| mapping\-svc | ✓ |  |  |  |  |  |  |  |  |
| curation\-svc |  |  |  |  |  |  |  |  | ✓ |

## 5\. Database

Sơ đồ: [17\\\-database\\\-ownership](diagrams/17-database-ownership.puml)\.

### 5\.1 Nguyên tắc

1. **Mỗi service một schema**, chỉ service đó ghi; đọc chéo qua API/sự kiện\.
2. **Postgres** cho metadata, quy trình, giao dịch \(RLS theo tenant\)\.
3. **ClickHouse** cho quan sát và giá trị chỉ số \(chuỗi thời gian lớn, point\-in\-time\)\.
4. **Qdrant** cho tìm kiếm ngữ nghĩa; **Neo4j** cho đồ thị khái niệm và lineage\.
5. **MinIO** cho file gốc bất biến và sản phẩm dẫn xuất\.
6. **Redis** chỉ là cache/pub\-sub, mất dữ liệu không ảnh hưởng tính đúng\.

### 5\.2 Phân bổ

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295638&cot=14)*

| Store | Database / schema / collection | Chủ sở hữu | Nội dung |
| --- | --- | --- | --- |
| Postgres | platform, auth \(hiện có\) | runtime, flow\-builder, auth\-api | agent, workflow, hội thoại, audit |
| Postgres | kb, semantic \(hiện có\) | kb\-backend, runtime | tài liệu, block, metric contract; thêm source\_document |
| Postgres | indicator | indicator\-catalog\-svc | taxonomy, chỉ số, phiên bản, biến, công thức, binding, ngưỡng, codelist |
| Postgres | observation | observation\-svc | series, release, retraction |
| Postgres | ingest\_source, ingest\_extraction, ingest\_mapping, ingest\_dq, ingest\_review | từng service ingestion | nguồn, artifact, job, quy tắc, DQ, duyệt |
| Postgres | alert, notification, report, eval | service cùng tên | vòng đời cảnh báo, hộp thư, báo cáo, golden set |
| Postgres | dagster | Dagster | run storage |
| ClickHouse | obs\.observation, obs\.indicator\_value | observation\-svc, compute\-svc | chuỗi thời gian \(tách database khỏi ClickHouse của Langfuse\) |
| Qdrant | knowledge, knowledge\_documents, schema\_catalog, memory \(hiện có\); indicator\_index | kb\-backend, runtime, catalog | vector |
| Neo4j | entity graph \(hiện có\); lineage, nhãn Concept | kb\-backend, catalog, lineage | đồ thị |
| MinIO | knowledge\-base \(hiện có\); ankt\-raw, ankt\-derived, reports, exports | kb, Dagster, extraction, report | file |
| Redis | session, query cache, rate limit connector, pub/sub | runtime, compute, observation, notification | tạm |
| OpenBao | tenants/\{tenant\}/connectors/\* | source\-registry, sandbox | credential |
| Langfuse | traces, datasets, scores | runtime, eval\-svc | quan sát LLM |

### 5\.3 Ước lượng dung lượng \(100 chỉ số, 5 năm vận hành\)

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295648&cot=14)*

| Dữ liệu | Ước lượng | Ghi chú |
| --- | --- | --- |
| Quan sát ClickHouse | ~10–50 triệu dòng, &lt; 5 GB nén | Gồm dữ liệu quốc tế đa quốc gia và nhiều vintage |
| File gốc MinIO | 50–200 GB | PDF, Excel, ảnh trang dẫn xuất gấp 3–5 lần file gốc |
| Postgres mới | &lt; 10 GB | Metadata, duyệt, eval |
| Qdrant | Tăng theo KB: corpus pháp luật 171k văn bản cỡ vài chục GB vector | Theo kích thước embedding \(bge\-m3 1024 chiều\) |

## 6\. Luồng dữ liệu

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295651&cot=14)*

| Luồng | Mô tả | Sơ đồ |
| --- | --- | --- |
| DF1 · Nguồn → quan sát | Dagster → artifact → trích xuất → mapping → DQ → duyệt → phát hành | 18, 08 |
| DF2 · Quan sát → chỉ số → cảnh báo | observation\.published → compute → indicator\.recomputed → alert → notification | 18 |
| DF3 · Câu hỏi → câu trả lời | UI → runtime → mcp\-gateway → compute/observation/kb → verifier → SSE | 04, 10, 24 |
| DF4 · Dữ liệu đổi → eval regression | observation\.published / indicator\.version\.approved → eval\-svc chọn case bị ảnh hưởng qua lineage → chạy → eval\.completed | 18 |
| DF5 · Báo cáo | report\-svc lấy số từ compute, đoạn văn từ runtime, trích dẫn từ kb → render → MinIO → notification | 16 |

**Tính đúng khi dữ liệu thay đổi:**

- Quan sát không bao giờ bị ghi đè; điều chỉnh = release mới\. Mọi câu trả lời ghi `release\_id` đã dùng \(`answer\_lineage`\), nên tái lập được\.
- Cache giá trị chỉ số gắn `\(indicator\_version, vintage, engine\_version\)`; sự kiện `observation\.published` xoá cache theo `series\_key` liên quan\.
- Consumer idempotent theo `event\_id`; lỗi sau N lần retry vào `\*\.dlq` và sinh cảnh báo vận hành\.

## 7\. Luồng nghiệp vụ

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295654&cot=14)*

| Luồng | Tác nhân | Sơ đồ |
| --- | --- | --- |
| BF1 · Vòng đời chỉ số | Research → Reviewer → Data steward → Hệ thống | 19 |
| BF2 · Một kỳ công bố số liệu | Connector → Extraction/DQ → Maker → Checker → Hệ thống → Người dùng | 20 |
| BF3 · Xử lý cảnh báo | Hệ thống → Chuyên viên → Lãnh đạo | 21 |

### 7\.1 RACI

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295659&cot=14)*

| Hoạt động | Research | Reviewer nghiệp vụ | Data steward | Chuyên viên | Lãnh đạo | Hệ thống |
| --- | --- | --- | --- | --- | --- | --- |
| Định nghĩa/sửa chỉ số | R | A | C | I | I | kiểm tra tự động |
| Gắn nguồn, binding | C | I | R/A |  |  | reconcile |
| Duyệt số liệu kỳ mới | C |  | R \(maker\) / A \(checker\) |  |  | DQ |
| Golden case | R | A | C | C |  | sinh nháp |
| Xử lý cảnh báo | C |  | C \(nếu lỗi dữ liệu\) | R | A | phát hiện, thông báo |
| Bản tin định kỳ | C | A |  | R | I | sinh nháp, lịch |

\(R = thực hiện · A = chịu trách nhiệm phê duyệt · C = tham vấn · I = được thông báo\)

## 8\. Luồng sử dụng giao diện người dùng cuối

> Đặc tả chi tiết từng màn hình, bộ primitive miền ANKT, hợp đồng render theo `intent`, trạng thái<br>từ chối/suy giảm và kiến trúc frontend: [UI\\\-DESIGN\\\.md](UI-DESIGN.md)<br>\(sơ đồ [37](diagrams/37-ui-information-architecture.puml) · [38](diagrams/38-answer-render-contract.puml) ·<br>[39](diagrams/39-uf-steward.puml) · [40](diagrams/40-frontend-architecture.puml)\)\.

### 8\.1 Màn hình và service phía sau

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295663&cot=14)*

| Màn hình | Người dùng | Chức năng | API gọi |
| --- | --- | --- | --- |
| Dashboard ANKT | Lãnh đạo, chuyên viên | Heatmap domain × trạng thái; cảnh báo mới; bản tin gần nhất | compute /indicators/values, alert /alerts, report /reports |
| Hộp cảnh báo | Lãnh đạo, chuyên viên | Danh sách, lọc, xác nhận, giao việc | alert, notification \(SSE\) |
| Danh mục chỉ số | Chuyên viên | Tìm theo tên/bí danh, lọc taxonomy, trạng thái ngưỡng | catalog /indicators, compute \(trạng thái\) |
| Trang chỉ số | Mọi người | Định nghĩa, công thức, ngưỡng theo giai đoạn, chuỗi thời gian, vintage, so sánh, phân rã, nguồn, cảnh báo đang mở, hỏi trong ngữ cảnh | catalog, compute, observation, kb, alert, runtime \(24\) |
| Ask Workspace | Mọi người | Hỏi tự do \+ gắn ngữ cảnh \(@ bảng/tài liệu/chỉ số/tệp\), block có danh tính, thao tác BI kéo thả, SQL/pandas, xuất — xem ASK\-WORKSPACE\.md | runtime \(SSE\), sandbox, kb, schema\-catalog, compute |
| Xem tài liệu nguồn | Mọi người | Ảnh trang \+ highlight ô/đoạn, metadata văn bản, hiệu lực | kb\-backend, extraction \(ảnh trang\) |
| Báo cáo | Chuyên viên, lãnh đạo | Tạo từ template, thêm khối chỉ số/biểu đồ/đoạn agent, duyệt, tải PDF/DOCX | report, compute, runtime |
| Phân tích đã lưu | Chuyên viên | Lưu bộ lọc/kỳ/ghi chú, chia sẻ | compute \(lưu query\), platform |
| Explorer | Chuyên viên | Dựng Analysis Query bằng form \(không viết JSON\), chia sẻ bằng URL | compute /query:validate, /query, catalog |
| Workbench duyệt trích xuất | Data steward | Ảnh trang \+ bbox ↔ bảng ứng viên sửa được, duyệt bằng bàn phím, maker–checker | curation, extraction \(ảnh trang\), data\-quality, mapping |
| Hộp thư &amp; giao việc | Mọi người | Thông báo realtime, nhiệm vụ được giao kèm deep\-link ngữ cảnh | notification \(SSE\), alert, curation |
| Eval console | QA | Golden set, chạy eval, so sánh lần chạy, scorecard theo họ chỉ số | eval, runtime, Langfuse |

### 8\.2 Luồng theo vai trò

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295664&cot=14)*

| Luồng | Sơ đồ | Tóm tắt |
| --- | --- | --- |
| UF1 · Lãnh đạo | 22\-uf\-executive | Dashboard → ô vàng/đỏ hoặc cảnh báo → trang tóm tắt chỉ số \(giá trị, ngưỡng, “vì sao thay đổi” có nguồn\) → hỏi thêm / mở nguồn / giao chuyên viên / tải bản tin |
| UF2 · Chuyên viên | 23\-uf\-analyst | Catalog hoặc Ask → trang chỉ số → chọn kỳ/vintage/so sánh → phân rã → hỏi agent → mở trích dẫn → báo sai \(issue\) → lưu / xuất Excel \+ nguồn / đưa vào báo cáo |
| UF3 · Mở trang chỉ số \(kỹ thuật\) | 24\-seq\-indicator\-page | 4 request song song \(catalog, compute, kb, alert\), đổi vintage = point\-in\-time query, câu hỏi trong ngữ cảnh đi qua runtime → MCP → compute |
| UF4 · Data steward | 39\-uf\-steward | Hàng đợi duyệt → workbench ảnh trang \+ bbox → sửa kèm lý do → mapping thành quy tắc → checker duyệt → phát hành release → tính lại \+ cảnh báo \+ eval regression |

### 8\.3 Nguyên tắc trải nghiệm

1. **Mọi con số đều bấm được tới nguồn** \(tài liệu, trang, vintage\)\.
2. **Ngữ cảnh đi theo người dùng**: hỏi từ trang chỉ số thì agent đã biết chỉ số, kỳ, vintage\.
3. **Hiển thị rõ khi số đã điều chỉnh** và bản trước là bao nhiêu\.
4. **Từ chối có lý do** \(không có dữ liệu quý, kỳ chưa công bố, không đủ quyền\) thay vì trả lời chung chung\.
5. **Phản hồi đúng/sai ngay tại câu trả lời**, nối thẳng vào issue dữ liệu hoặc eval\.
6. **`intent` quyết định bố cục**, không phải LLM — 9 intent ánh xạ vào 10 khối hiển thị dùng lại \([38](diagrams/38-answer-render-contract.puml)\)\.
7. **Giả định luôn hiển thị và sửa được**: mọi slot agent tự điền hiện thành chip; sửa chip → chạy lại truy vấn\.
8. **Số không truy được nguồn thì không hiển thị** — verifier chặn ở backend, giao diện không hiện kèm cảnh báo nhỏ\.

## 9\. Triển khai theo giai đoạn

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295669&cot=14)*

| Service logic | POC \(đến 23/10\) | Sau POC |
| --- | --- | --- |
| indicator\-catalog · observation · compute · alert \(tối thiểu\) | Gộp 1 deployable ankt\-api \(FastAPI, module tách riêng\), observation dùng Postgres | Tách service; observation sang ClickHouse |
| mcp\-gateway | Module trong ankt\-api \(/mcp\) | Deployable riêng |
| source\-registry · connectors · Dagster | ankt\-connectors chạy K8s CronJob đọc sources\.yaml | Dagster \+ source\-registry\-svc |
| extraction · mapping · DQ · curation | Mở rộng kb\-promote\-silver \(trang/bbox, OCR\) \+ màn duyệt đơn giản trong kb\-backend | Tách 4 service theo sự kiện |
| eval\-svc | model/eval \+ Langfuse datasets | Service riêng \+ regression theo sự kiện |
| report · notification · lineage | Chưa làm | Giai đoạn cảnh báo &amp; báo cáo |
| workflow\-runtime · kb\-backend · frontend | Mở rộng như mục 3\.16 ở mức POC | Đầy đủ |

Việc gộp POC giữ **ranh giới module = ranh giới service tương lai** \(package, schema, API prefix riêng\), để tách ra không phải viết lại\.

## 10\. Yêu cầu phi chức năng

*[Table view](https://miro.com/app/board/uXjVHmCuWlQ=/?moveToWidget=3458764683910295670&cot=14)*

| Nhóm | Yêu cầu |
| --- | --- |
| Bảo mật | Service assertion cho mọi lời gọi nội bộ; RLS Postgres \+ row policy ClickHouse theo tenant và phân loại; secret chỉ trong OpenBao; audit mọi truy cập số liệu restricted |
| Truy vết | trace\_id qua HTTP và envelope Kafka; Langfuse cho LLM; Jaeger cho service; lineage cho dữ liệu |
| Tin cậy | Consumer idempotent; DLQ; retry có backoff; backfill theo nguồn; quan sát append\-only |
| Hiệu năng | Trang chỉ số &lt; 1,5 s p95; truy vấn chuỗi &lt; 100 ms p95; TTFT câu tra cứu ≤ 2 s |
| Sao lưu | Postgres backup job hiện có mở rộng cho schema mới; ClickHouse backup theo partition; MinIO versioning bucket ankt\-raw |
| Vận hành | Health/readiness mọi service; dashboard Grafana theo nguồn \(độ trễ công bố, lỗi connector\), theo hàng đợi duyệt, theo eval |
