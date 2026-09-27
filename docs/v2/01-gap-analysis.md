# 01. Đối chiếu hiện trạng với thiết kế v2

> Đo ngày 27/09/2026 trên `main` (8a43577, sau đó thêm d116062), nhánh `feat/add-task` (e5f2aa5) và cụm dev
> `10.10.55.101`. Mọi dòng "đã có" dưới đây đều có đường dẫn mã hoặc truy vấn đi kèm để kiểm
> lại được. Trạng thái: **Có** · **Một phần** · **Chưa có** · **Thừa** (có nhưng trùng lặp hoặc
> đi ngược quyết định đã chốt). Mã NC-… là mã lõi số ở [12-numeric-core.md](12-numeric-core.md).

## 1. Tài liệu thiết kế đang lạc hậu ở đâu

Chương 14 của tài liệu v2 chốt số liệu ngày 17/09. Mười ngày sau, sáu dòng đã đổi. Kế hoạch
phải dựa trên cột bên phải.

| Hạng mục | Tài liệu ghi (17/09) | Thực tế (27/09) | Căn cứ |
| --- | --- | --- | --- |
| NC-E1. OCR tắt | Chưa sửa | **Đã sửa**: Docling bật OCR Tesseract vie+eng cho PDF scan và ảnh. Chưa có benchmark độ chính xác token số (NC-ADR-T8) | `backend/libs/ingestion/docling_parser.py:66`, commit 5b1d72a |
| Driver GPU lệch phiên bản | Chưa xác nhận | **Đã chạy lại**: `nvidia-smi` trả 33,2/40 GB; `vllm-main`, `vllm-fast` chạy image `:latest` | `nvidia-smi`, `docker ps` |
| Connector tự động | "Chưa có connector tự động nào" | **Có**: 35 nguồn đã đăng ký, 18 có cron, 17 chạy tay (9 http_file, 5 web_crawl, 2 web_crawl_headless, 12 rest_api, 6 sdmx, 1 rss) | bảng `KBSource` trên cụm dev |
| NC-T18. Cờ LightRAG | Cả bốn tắt | `DOC_NARROWING=1`, `QUERY_ALIAS_RESOLVE=1` ở dev; `HYBRID_LEXICAL`, `DEGRADE_BUDGET_MS` vẫn tắt; chưa đo | `deploy/k8s/dev/service/kb-backend.yaml:29,32` |
| Kafka idempotence (P0-03) | Chưa sửa | **Một phần**: client dùng chung bật idempotence, producer của `platform_workers` chỉ đặt `acks=all` | `libs/database_connector/kafka_connector.py:49`, `platform_workers/common/kafka.py:117` |
| Ổ đĩa | 81%, ~31 GB trống | **83%, 28 GB trống**; kubelet tự đuổi pod khi còn dưới ~8,7 GB | `df -h /` |
| Lõi chỉ số | "chưa có mắt xích nào" | **Viết xong nhưng chưa merge** (mục 2) | nhánh `feat/add-task` |

## 2. Phát hiện lớn nhất: lõi số nằm trên nhánh bị bỏ quên

Ngày 16–17/09, nhánh `feat/add-task` (commit 08bb777, e5f2aa5) đã hiện thực gần đủ "Đợt 1. Lõi
số" của Bảng 9.6: khoảng 2.200 dòng gồm mã và test. Nhánh chưa từng merge, hiện chậm `main` 43
commit, và cụm dev **không có** schema `ankt` (truy vấn `information_schema.schemata` ngày
27/09). Trên `main` chỉ còn thư mục rỗng `backend/libs/indicator/__pycache__`.

| Thành phần trên `feat/add-task` | Dòng | Ứng với thiết kế | Đề xuất |
| --- | --- | --- | --- |
| `libs/indicator/quantity.py`: Decimal + đơn vị + bội số, % trừ % ra điểm % | 215 | NC-T1, NC-ADR-T1/T2 | Lấy nguyên |
| `libs/indicator/numeric.py`: đọc số theo locale, giữ mọi cách đọc khi mơ hồ, dung sai theo độ chính xác công bố | 168 | NC-E2, NC-T5 | Lấy nguyên, dùng để sửa NC-E2 |
| `libs/indicator/formula.py`: formula DAG có kiểu, kiểm stock/flow lúc compile, `decompose_change` | 377 | NC-T3, NC-R2 | Lấy, mở rộng thêm phép chuỗi thời gian (mục 4 của 03) |
| `libs/indicator/threshold.py`: chọn ngưỡng theo kỳ dữ liệu | 126 | NC-T4, NC-R3 | Lấy nguyên |
| `libs/indicator/verifier.py`: 4 kiểm tra tất định, không gọi LLM | 227 | NC-T5, NC-ADR-T5 | Lấy, nối vào runtime |
| `libs/indicator/period.py`: chỉ A/Q/M | 63 | NC-T2 | Lấy, **bổ sung D và W** cho giá dầu/vàng |
| `libs/indicator/observation.py`: đọc point-in-time theo `as_of` | 186 | NC-T2 | Lấy |
| `PageRegion` trong `docling_model.py`: page + bbox gốc toạ độ trên-trái | ~30 | NC-T7; trích dẫn tới trang/ô (khoảng trống G3 của v1) | Lấy |
| 7 file test unit (`test_quantity`, `test_threshold`, `test_verifier`…) | ~900 | | Lấy |
| Migration `ankt.variable/source_document/release/observation` (trigger chặn sửa giá trị, RLS) và `ankt.indicator/threshold` (EXCLUDE gist) | ~350 | Indicator registry (G1 v1), observation store point-in-time (G2 v1), NC-T4 | Viết lại trên head alembic hiện tại, đổi tên schema (xem 03) |
| Migration `ankt.source/source_binding/connector_run` | 127 | Đấu nối nguồn chính thức (G4 v1) | **Thừa**: trùng `KBSource`/`KBSourceRun` đã có UI. Bỏ |
| `services/ankt_connectors/` (CLI, mỗi nhà công bố một hàm fetch) | ~1.100 | Đấu nối nguồn chính thức (G4 v1) | **Thừa**: đi ngược quyết định "connector theo giao thức, cấu hình được từ UI" (16/09). Bỏ, chỉ giữ ý tưởng `promote.py` (artifact → observation) |

Cứu phần này tiết kiệm khoảng 3–4 tuần-người so với viết lại, và code đã được viết đúng tinh
thần thiết kế (comment giải thích NC-R2, NC-R3, NC-ADR-T5).

**Cập nhật 27/09/2026 (D4):** đã lấy sang nhánh `feat/remove-redundant-service` (working tree, chưa
commit): `libs/indicator`, 8 file test unit, `PageRegion`, ghi page/bbox ở Silver/Gold và payload
Qdrant, `page` trong citation (`kb_search`, `responder`), verifier tất định trong `judge`, sửa NC-E2 ở
`grounding.py`. Bốn file ingestion được ghép ba chiều với các sửa sau đó của `main` (OCR vie+eng,
header bảng nhiều dòng); `sizes` của `_build_parsed_tables` thành tham số tuỳ chọn để test header
bảng của `main` vẫn chạy. Kết quả trong container: 173 test unit và 33 test Docling đạt; ruff và
mypy không có lỗi mới so với HEAD. Không lấy: `ankt_connectors/`, migration `ankt.*`, test tích hợp
`ankt` (phụ thuộc schema sẽ viết lại ở S1-01), `SourceCenter`/`SourceWizard`.

Phát hiện phụ: `tests/unit/test_docling_table_headers.py` trên `main` không gắn marker `unit`,
nên `tests/conftest.py` luôn bỏ qua nó, kể cả trong CI.

## 3. Đối chiếu theo 9 thay đổi kiến trúc của v2 (Bảng 15.1)

| # | Thay đổi v2 | Trạng thái | Đã có trong mã | Còn thiếu |
| --- | --- | --- | --- | --- |
| 1 | Acquisition chủ động | **Một phần, khá** | Source Registry trong DB + UI (`KBSource*`, `components/knowledge/sources/SourceRegistry.tsx`); worker `platform_workers/workers/web_crawler` chạy cron, retry/backoff (`KBSourceRun.retry_count`), crawl đệ quy (`KBSourceCrawlQueue`), bỏ qua trang không đổi theo hash (`register_web_bronze`); `promotion_mode=auto` | Licence/use_rights, owner, SLA độ tươi; thực thể **release** riêng; phát hiện link mới trên trang landing (ví dụ thật ở mục 6); egress proxy |
| 2 | Reverse schema | **Chưa có** | `kb_structured_data` đề xuất schema cho xlsx/csv, chỉ bảng lớn nhất mỗi file; bảng `KBTable` trên cụm dev có **0 dòng** | Suy luận đơn vị/kỳ/tần suất/entity; profile trích xuất lưu dạng cấu hình; màn duyệt |
| 3 | Post-data 3 lớp | **Chưa có** | Mọi thứ đi một đường chunk → embed. 12 nguồn `rest_api` + 6 `sdmx` dừng ở Bronze dạng byte thô (`connectors/rest_api.py` docstring: "that's the not-yet-built Indicator Service warehouse's job") | Canonical series, epistemic type, knowledge compilation |
| 4 | HITL theo rủi ro | **Một phần** | Node `human_approval`, router `orchestrator/routers/reviews.py`, `StructuredTableReview.tsx`, phát hiện mâu thuẫn văn bản (`kb_conflict_resolution` + màn Conflicts) | Write gate theo rủi ro, maker–checker, OCC, vai trò DATA_STEWARD/REVIEWER (hiện chỉ có PLATFORM_ADMIN, AI_ENGINEER, BUSINESS_OPERATOR, EXECUTIVE) |
| 5 | Persistent knowledge | **Chưa có** | Entity graph Neo4j từ Gold (`libs/ingestion/entity_registry.py`); `KBData.content_hash` | Claim/Event/Episode/Forecast/Decision có version, valid/system time, supersession |
| 6 | Retrieval-as-reasoning | **Một phần** | LightRAG: vector + FTS Postgres (migration `f2b3c4d5e6f7_kb_fts_index`) + mở rộng Neo4j + RRF + rerank; `kb_search` một lượt | Vòng search/read/traverse có giới hạn; EvidenceRequirement; citation tới trang |
| 7 | Task/Capability/Runtime graph | **Một phần** | LangGraph 8 node, checkpointer Postgres schema `langgraph`, planner DPTS, executor DAG (`orchestrator/executor/executor.py`) | Router theo độ phức tạp (hiện định tuyến chỉ theo có `agent_id` hay không, `orchestrator/graph.py:123`); capability registry (hiện `TOOL_REGISTRY` tĩnh, `tool_executor/node.py:31`); job bất đồng bộ; resume sau restart worker chưa có test |
| 8 | Forecasting | **Chưa có** | Sandbox chỉ cho `pandas, numpy, plotly, matplotlib` (`sandbox_executor/security_visitor.py:17`) | Toàn bộ pipeline |
| 9 | Evolution | **Chưa có** | Langfuse trace, `model/eval` với tier online chạy được từ a38df91 | Error Book, registry thí nghiệm, skill có version |

## 4. Đối chiếu theo mảng kỹ thuật

### 4.1 Dữ liệu và nguồn

| Năng lực | Trạng thái | Ghi chú |
| --- | --- | --- |
| Artifact bất biến + SHA-256 | Một phần | `KBData.content_hash` có index; nhưng **re-crawl thay thế và xoá vết tài liệu cũ** (Phase A, 22/09) → đi ngược "raw immutable" (00 bất biến 2, ADR-V2-21). Sửa ở S2-02 |
| Nguồn dầu/vàng | Chưa có | `docs/v2/data/sources-public-debt.yaml` không có EIA, OPEC, JODI, WGC, LBMA; chỉ có FRED cho nợ công |
| Observation store | Chưa có trên main | Thiết kế sẵn trên `feat/add-task` |
| Tần suất ngày/tuần | Chưa có | `Period` chỉ A/Q/M; giá dầu/vàng là chuỗi ngày, tồn kho EIA là tuần |
| Chuỗi dẫn xuất (spread, return, volatility) | Chưa có | Formula DAG có `Ratio/Sum/Difference/AsPercent/Scale`; thiếu `Lag`, `LogReturn`, cửa sổ trượt, `FxConvert` |
| ClickHouse cho observation | Chưa có | ClickHouse đang chạy cho Langfuse và sandbox; chưa có database `obs` |

### 4.2 Agent và truy xuất

| Năng lực | Trạng thái | Ghi chú |
| --- | --- | --- |
| Verifier số tất định | Đã lấy vào working tree (P0-01) | `judge.py` chạy 4 kiểm tra tất định của `libs/indicator/verifier` trước khi hỏi LLM; chưa có freshness và evidence sufficiency (S10-01) |
| MCP chuẩn | Chưa có | `nodes/sandbox/mcp.py:51` POST JSON tới `{url}/execute`, không phải MCP Streamable HTTP |
| Công cụ chuỗi thời gian | Chưa có | Handler hiện có: agent, code_interpreter, data (SQL), filesystem, langchain, shell |
| Trả lời đúng số trong bảng rộng | Đã sửa (d116062) | Eval 26/09 sai 2/3 câu tra bảng "Bản tin nợ công". Nguyên nhân: tóm tắt chunk bằng LLM lỗi ~32% khi chạy song song, fallback làm summary bảng chỉ còn tiêu đề, rerank chấm trên summary. Đã sửa fallback, backfill 480 chunk. Số vẫn phụ thuộc một bước LLM → ADR-V2-09 (07) |

### 4.3 Đánh giá

| Năng lực | Trạng thái | Ghi chú |
| --- | --- | --- |
| Case set | Một phần | 123 case: grounding 22, intent 32, policy 44, sql 25. Không case nào về dầu/vàng |
| NC-E2. Scorer so chuỗi chữ số | Đã sửa trong working tree (P0-01) | `grounding.py` lấy từ `feat/add-task`, dùng `libs.indicator.numeric` |
| `unsupported_number_rate` cho case chạy agent thật | Sai phương pháp | So với `case["contexts"]`, case live không có contexts → mọi số đều bị tính là không có nguồn |
| Ngưỡng online | Tạm | `model/eval/thresholds.json` ghi `provisional: true` cho cả 4 metric online |
| Metric retrieval (Recall@K, MRR) | Chưa có | Cần nhãn evidence vàng |

### 4.4 Giao diện

| Năng lực | Trạng thái | Ghi chú |
| --- | --- | --- |
| NC-E3. `formatVN.ts` theo chuẩn Mỹ | **Chưa sửa** | Docstring dòng 2: "US number / currency / date formatting" |
| Màn chuỗi/chỉ số/forecast/HITL tri thức | Chưa có | `frontend/src/features/` chỉ có `ask/` |
| Source Registry, lịch, Bulk Import | Có | Tái dùng cho nguồn dầu/vàng, chỉ thêm trường licence và profile trích xuất |

### 4.5 Hạ tầng liên quan trực tiếp tới POC

| Việc | Trạng thái | Vì sao chặn POC |
| --- | --- | --- |
| `auto.create.topics.enable` (P0-03) | Còn `true` (`deploy/k8s/dev/database/kafka.yaml:30`) | Thêm 10+ topic v2; gõ sai tên topic sẽ tạo topic mới im lặng |
| Topic v2 | Chưa có | `kafka-topics` Job chỉ tạo `kb.promote.*`, `ddl.changes`, `audit.data.access`, `memory.*`, `release.*` |
| Egress proxy | Chưa có | Worker đã gọi ra 35 nguồn ngoài mà không có allowlist tên miền (điều kiện bắt buộc trước khi mở kết nối ra nguồn ngoài, [11-ops.md](11-ops.md), P0-08) |
| Backup ngoài node | Chưa có | Observation và tri thức đã duyệt là dữ liệu không tái tạo được từ nguồn (công sức duyệt) |

## 5. Những gì **thừa** hoặc cần gộp

1. **Hai source registry**: `ankt.source` (nhánh chưa merge) và `KBSource` (đang chạy, có UI). Giữ `KBSource`.
2. **YAML làm registry**: thiết kế v2 (V2-001) ghi "sources-v2.yaml + DB migration". Nhóm đã chốt ngày 16/09 rằng registry phải nằm trong DB để màn hình liệt kê, sửa, lập lịch được. YAML chỉ còn vai trò file seed nhập qua Bulk Import.
3. **Deployable đặt tên theo miền** (`commodity-api`, `commodity-connectors`, `commodity-forecast-worker`, Bảng 17.2): đi ngược mục tiêu "thêm chỉ số bằng cấu hình, không sửa mã". Dầu/vàng phải là cấu hình (nguồn, profile, series, golden set) trên service chung.
4. **Hai lộ trình song song**: POC v1 (Nợ công/GDP, hạn 23/10, Chương 13) và kế hoạch v2 8 tuần (Chương 17) dùng chung một đội 7 người mà tài liệu không nói cái nào kết thúc ngày 23/10.
5. **Dagster, MLflow đầy đủ, Neo4j lineage đầy đủ, mcp-gateway deployable riêng** trong 8 tuần: dữ liệu dầu/vàng của POC dưới 200 nghìn dòng (Brent ngày từ 1987 ≈ 9 nghìn dòng). K8s CronJob đủ; MCP Gateway chạy trong `indicator-api`. ClickHouse cho effective observation đã được kiến trúc chính thức chốt (00, ADR-V2-16) và giữ.

## 6. Kiểm tra nguồn dầu/vàng từ máy chủ (27/09)

| Endpoint | Kết quả | Ý nghĩa cho thiết kế |
| --- | --- | --- |
| `eia.gov/dnav/pet/hist_xls/RBRTEd.xls` | 200, 451 KB | Brent ngày tải được không cần key |
| `api.eia.gov/v2/...` | 403 khi không có key | API cần key miễn phí, lưu OpenBao |
| `api.stlouisfed.org/fred/...` | 400 khi không có key | Cần key; `fredgraph.csv` timeout từ máy chủ |
| World Bank Pink Sheet (URL trong tài liệu cũ) | 200 nhưng **dữ liệu dừng ở 2024M12**, "Updated on January 03, 2025" | URL cố định đã chết về nội dung. Trang landing hiện trỏ tới `.../74e8be41...-0050012026/.../CMO-Historical-Data-Monthly.xlsx`. Đây là ví dụ thật cho "release detection phải đọc trang landing", không được ghim URL file |
| `jodidata.org/oil/` | 200 | Landing tải được |
| `gold.org/goldhub/data` | 200 | Landing tải được; tải dữ liệu cần đăng nhập, cần đọc điều khoản |
| `publications.opec.org/momr` | 403 | Cần headless hoặc tải tay |
| `sjc.com.vn` | 403 | Cần headless; giá vàng trong nước |
| `petrolimex.com.vn` | 200 | Giá bán lẻ xăng dầu trong nước |

Chi tiết từng nguồn và cấu hình đề xuất: [04-sources-oil-gold.md](04-sources-oil-gold.md).

## 7. Hiện trạng theo 10 vấn đề kiến trúc của bản chính thức (00 mục 13)

| Vấn đề | Hiện trạng trong repo | Việc (05) |
| --- | --- | --- |
| Semantic identity | Không có entity registry; semantic layer 1 bảng 1 cột (`libs/semantic/models.py`) | S1-03 |
| Temporal semantics | Không có `available_at`; `Period` (trên `feat/add-task`) chỉ A/Q/M | P0-04, S1-01, S1-02 |
| Provenance | `KBData.content_hash` có; không có release; chunk chưa có page/bbox trên `main` | S2-02, S2-03, S4-01 |
| Schema drift | Không phát hiện; `rest_api`/`sdmx` lưu byte thô | S3-01, S3-03 |
| Conflict semantics | Có phát hiện mâu thuẫn văn bản (`kb_conflict_resolution`); không có cho số | S6-02 |
| Effective data | Không có; re-crawl thay thế và xoá vết | S2-02, S6-03, S7-01 |
| HITL authority | 4 vai trò (`PLATFORM_ADMIN`, `AI_ENGINEER`, `BUSINESS_OPERATOR`, `EXECUTIVE`); không có maker/checker | S6-03 |
| Cross-modal join | Entity graph Neo4j từ Gold nhưng không nối với series | S5-01 |
| Evidence sufficiency | Verifier số tất định đã vào judge (P0-01); chưa có kiểm đủ bằng chứng | S10-01 |
| Deterministic eval | Có khung `model/eval`, ngưỡng online `provisional`; NC-E2 đã sửa (P0-01), NC-E3 còn mở | P0-02, S10-02 |
