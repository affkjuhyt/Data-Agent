# Đánh giá kiến trúc Data-Agent cho bài toán đa chỉ số An ninh kinh tế (ANKT)

> Phiên bản 15/09/2026 · Đối tượng: nhóm Data Research (WS-DATA), nhóm kỹ thuật, quản lý dự án.
> Căn cứ: khảo sát mã nguồn repo `Data-Agent` (nhánh `feat/optimize-agent-core-latency`) và các tài liệu ECO-001.1 → ECO-004.4.
> Sơ đồ PlantUML nằm trong [`diagrams/`](diagrams/) (mỗi file `.puml` có bản `.svg` render sẵn).
> Kiến trúc tổng quát cho **vô số chỉ số** (lớp dữ liệu đầu vào, agent core, Data Studio): [PLATFORM-ARCHITECTURE.md](PLATFORM-ARCHITECTURE.md).
> Thiết kế service, kết nối, database, luồng dữ liệu/nghiệp vụ/giao diện: [SERVICE-DESIGN.md](SERVICE-DESIGN.md).
> Kế hoạch dựng POC 16/09–23/10/2026: [POC-PLAN.md](POC-PLAN.md) · Danh mục nguồn dữ liệu: [DATA-SOURCES.md](DATA-SOURCES.md) ([sources.yaml](data/sources.yaml)).

---

## 0. Kết luận nhanh

| Câu hỏi | Trả lời |
|---|---|
| Hệ thống hiện tại có đáp ứng phân tích **nhiều chỉ số** không? | **Đáp ứng một phần.** Tầng agent (orchestrator, planner, retrieval, streaming, guardrail, evidence, trace) dùng lại được. **Lõi dữ liệu chỉ số chưa có**: hệ thống được thiết kế cho BI doanh nghiệp (“metric = phép gộp trên 1 bảng warehouse”), trong khi ANKT cần “chỉ số = công thức trên các **quan sát đã công bố**, có **vintage**, **tài liệu nguồn** và **ngưỡng theo kỳ**”. |
| Thiếu ở đâu? | 10 nhóm khoảng trống (mục 2), nặng nhất: Indicator registry, Observation store có vintage, Source registry, đấu nối nguồn chính thức (PDF/API/lịch), OCR, eval số có dung sai. |
| Có hướng giải quyết không? | **Có**, không phải viết lại: thêm một **Indicator Service** và **Connector Hub** cạnh runtime hiện có, agent gọi chúng như tool (sơ đồ [02](diagrams/02-target-architecture.puml)). |
| Yêu cầu ECO-008.14 (mỗi đáp án gắn tài liệu nguồn tên/số/ngày + dung sai) làm nhanh được không? | **Được, khoảng 2 tuần** trên hạ tầng sẵn có (MinIO, Docling, Langfuse, `model/eval`, release gate) — chi tiết mục 6. |
| Lộ trình | GĐ0 golden use case (→ Demo 1 ~cuối 10/2026) · GĐ1 6 chỉ tiêu nhóm A (→ ~giữa 11) · GĐ2 ~30 chỉ số domain 2–3 + cảnh báo (→ ~cuối 12) · GĐ3 dự phóng/EWS (2027). Gantt: [06](diagrams/06-roadmap-gantt.puml). |

---

## 1. Hệ thống hiện tại đáp ứng đến đâu

Sơ đồ hiện trạng: [01-current-architecture](diagrams/01-current-architecture.puml).

| Năng lực cần cho đa chỉ số | Hiện trạng | Căn cứ trong mã |
|---|---|---|
| Hỏi đáp tiếng Việt, làm rõ câu hỏi, stream | ✅ Có | `orchestrator/graph.py`, `nodes/input_refine.py` |
| Truy xuất tài liệu + trích dẫn | ✅ Có (mức đoạn, không tới trang) | `nodes/sandbox/kb_search.py`, `responder.py` |
| Chỉ số được duyệt, có phiên bản | ⚠️ Metric contract: 1 bảng, 1 cột, sum/count/avg/min/max | `backend/libs/semantic/models.py` |
| Chỉ số dạng tỷ lệ/dẫn xuất (Nợ/GDP) | ❌ Không biểu diễn được | `validate_identifier` chỉ nhận tên cột |
| Stock cuối kỳ, weighted average | ❌ | `compiler.py` |
| So sánh kỳ (`compare_to`) | ❌ Parse nhưng không compile | `libs/semantic/slots.py` |
| Đơn vị, bội số, tiền tệ, FX | ❌ `unit` là text tự do | `aggregation_check.check_units` |
| Vintage / số liệu theo thời điểm công bố | ❌ | không tìm thấy |
| Tài liệu nguồn (tên, số hiệu, ngày) | ❌ Chunk không có metadata văn bản | `gold/persistence.py` |
| Trang / bbox của số liệu | ❌ Docling có nhưng không lưu | `docling_model.py: ParsedChunk` |
| Ngưỡng theo kỳ, trạng thái cảnh báo | ❌ | không tìm thấy |
| Tính toán xác định (không để LLM làm toán) | ⚠️ Chỉ SQL; sandbox không có statsmodels/scipy | `security_visitor.py` |
| Governance: freshness, classification, audit | ✅ Có nền tốt | `libs/shared/freshness.py`, `audit.py` |
| Eval + release gate | ⚠️ Có khung, chưa có đáp số/dung sai/tài liệu nguồn | `model/eval/` |
| Lập lịch thu thập / báo cáo | ❌ UI lưu cron nhưng không có scheduler chạy | `workflow/SchedulingCenter.tsx` |

**Nhận định kiến trúc:** đơn vị sự thật của hệ thống hiện tại là *“một câu SQL trên warehouse”*. Với ANKT, đơn vị sự thật phải là *“một quan sát đã công bố trong một tài liệu cụ thể, tại một vintage cụ thể”*. Chuyển trục này là thay đổi quan trọng nhất; mọi thứ khác (UI, eval, cảnh báo) xây trên nó.

---

## 2. Khoảng trống chi tiết

| # | Khoảng trống | Hệ quả nếu không làm | Hướng giải quyết |
|---|---|---|---|
| G1 | **Indicator registry** theo ECO-001.5 (taxonomy, polarity, formula, biến nguyên tử, `thresholds[]`) | Mỗi chỉ số phải “code tay” trong prompt/SQL; không mở rộng lên hàng chục chỉ số | Bảng `indicator`, `indicator_version`, `variable`, `formula_input`, `threshold` (sơ đồ [05](diagrams/05-indicator-data-model.puml)); nạp từ YAML ECO-001.5 |
| G2 | **Observation store point-in-time** (release, vintage, revision) | Không phân biệt ước thực hiện / quyết toán; không tái lập được đáp án; so sánh sai qua điểm gãy GDP | Bảng `release` + `observation` (append-only, không ghi đè) |
| G3 | **Source registry** + trích dẫn tới trang/ô | Không đáp ứng ECO-008.14; người dùng không kiểm chứng được số | Bảng `source_document`; lưu `page_no`/`bbox` từ Docling; deep-link `#page=N` |
| G4 | **Đấu nối nguồn chính thức** | Dữ liệu vào bằng tay, không cập nhật | Connector Hub (mục 3) |
| G5 | **Computation engine xác định** | LLM tự chia/làm tròn → sai số không kiểm soát | Service tính theo formula DAG: căn kỳ stock/flow, đơn vị, làm tròn theo `decimal_precision` |
| G6 | **Reconciliation & Data Quality** | Không phát hiện đếm trùng (vay về cho vay lại), lệch phạm vi, lỗi OCR | So tự tính vs số chính thức với dung sai; rule tổng thành phần = tổng (tái dùng `check_subtotals`) |
| G7 | **Threshold & alert engine** | Không cảnh báo được; LLM so ngưỡng có thể áp nhầm giai đoạn | Chọn ngưỡng theo `valid_from/valid_to` của **kỳ dữ liệu**, không theo ngày hỏi |
| G8 | **Eval cho số liệu** | Scorer hiện so khớp chuỗi chữ số (“128,4” = 1284), không so với đáp án, không có dung sai | Case schema v2 + scorer dung sai + scorer trích dẫn/vintage (mục 6) |
| G9 | **Model tài liệu**: OCR tắt, không có VLM | PDF scan (phổ biến ở văn bản nhà nước) không trích được số; `VLM_MODEL=agent-main` là Qwen3-14B **chỉ xử lý văn bản** nên mô tả ảnh không hoạt động | Dựng OCR/layout/VLM service + benchmark (mục 7) |
| G10 | **UI chuyên biệt** | Người dùng chỉ có khung chat | Trang chỉ số, dashboard, workbench, eval console (mục 5) |

Rủi ro vận hành đi kèm: một GPU A100 40GB đang dùng chung cho mọi model (~32 GB đã cấp); `nvidia-smi` đang báo *Driver/library version mismatch* — cần reboot trước khi thêm/khởi động lại container GPU.

---

## 3. Đấu nối dữ liệu — phân tích chuyên sâu

### 3.1 Hiện trạng và lỗi phát hiện

| Nhóm | Hiện có | Hạn chế / lỗi phát hiện |
|---|---|---|
| **Warehouse** | Thực thi SQL read-only: Postgres, Snowflake, ClickHouse (`sandbox_executor/execution/sql_executor.py`); secret trong OpenBao; schema crawler 24h + sự kiện `ddl.changes` | • Text-to-SQL chỉ map dialect **postgres, clickhouse** (`handlers/data.py`) — Snowflake chạy được SQL nhưng không có dialect sinh SQL.<br>• UI wizard chỉ có **snowflake, databricks** (`warehouse.data.ts`) — Databricks không có backend, Postgres/ClickHouse không có trong UI.<br>• Trường `sync_schedule` không có consumer.<br>• Không có MySQL/Oracle/SQL Server/BigQuery. |
| **Tài liệu** | Upload doc/ảnh/video/web; bronze→silver→gold qua Kafka; Docling tách bảng | • `do_ocr=False` → **PDF scan không ra chữ**.<br>• Bảng trong PDF chỉ thành chunk markdown, **không vào được bảng dữ liệu**.<br>• Import có cấu trúc chỉ cho xlsx/csv và **chỉ bảng lớn nhất mỗi file** (`kb_structured_data/libs/propose.py`).<br>• Không lưu trang/bbox, không có metadata văn bản (số hiệu, ngày ban hành, hiệu lực).<br>• Không có dedup theo hash nội dung. |
| **Web / API** | Ingest 1 URL (trafilatura + Docling) | Không lịch thu thập lại, không phát hiện bản mới, không RSS, **không connector REST/SDMX** (IMF, World Bank, BIS, ADB). |
| **Điều phối pipeline** | Kafka cho KB | Không có scheduler cho dữ liệu (Airflow/Dagster/CronJob), không backfill, không retry có quản lý cho nguồn ngoài. |
| **Ngữ nghĩa dữ liệu** | Freshness watermark, classification, `upstream` | Không vintage, không chuẩn hoá đơn vị/FX, không reconciliation, không data contract, không rule DQ. |
| **MCP** | Node `mcp` | Gọi HTTP POST tự định dạng, **không theo giao thức MCP** → không cắm được MCP server chuẩn. |
| **Bảo mật dữ liệu** | RLS trên bảng nền tảng, audit truy cập | Chưa có row-level security theo người dùng trên warehouse; chưa gắn classification cho observation. |

### 3.2 Nguồn cần đấu nối cho 6 chỉ tiêu nhóm A (cần ECO-006 xác nhận)

| Biến nguyên tử | Nguồn dự kiến | Dạng | Connector cần |
|---|---|---|---|
| Dư nợ công, nợ Chính phủ, nợ được bảo lãnh, nợ CQĐP | Bộ Tài chính (bản tin nợ công, báo cáo trình Quốc hội) | PDF (có thể scan) | Document connector + OCR/table + review |
| GDP giá hiện hành (các vintage) | Cục Thống kê | Web, PDF, Excel | Web/document connector theo lịch |
| Nợ nước ngoài tự vay tự trả DN/TCTD | Ngân hàng Nhà nước | Báo cáo PDF/web | Document connector |
| Tổng thu NSNN, nghĩa vụ trả nợ, trả lãi | Bộ Tài chính (dự toán, quyết toán NSNN) | PDF, Excel | Document + structured import (nhiều bảng/file) |
| Kim ngạch XK hàng hoá, dịch vụ | Cục Thống kê, Hải quan, NHNN (BoP) | Web, Excel | Web/structured connector |
| Đối chiếu quốc tế | World Bank (IDS, WDI), IMF (GDD, dữ liệu SDMX) | REST/SDMX | API connector |
| Căn cứ pháp lý | Luật Quản lý nợ công (VBHN 59/2026), NQ 26/2026/QH16… | PDF/HTML | KB + metadata hiệu lực văn bản |

### 3.3 Thiết kế Connector Hub

Luồng chuẩn cho mọi nguồn (sơ đồ [03](diagrams/03-indicator-pipeline.puml)):

```
Source (định nghĩa nguồn, lịch, phân loại)
  → Release (một lần công bố: ngày, vintage)
    → Artifact (file gốc trong MinIO, SHA-256)
      → Extraction (OCR/API/parse → candidate observation + source_ref)
        → Review (duyệt/sửa, maker–checker)
          → Observation (append-only) → Computation → Reconciliation
```

Nguyên tắc:
1. **Idempotent theo hash**: cùng file tải lại không tạo bản mới; file khác hash tạo `release` mới (giữ bản cũ).
2. **Không ghi đè số liệu**: số điều chỉnh là observation mới gắn release mới.
3. **Mọi số đều có `source_ref`** (trang, bảng, ô, bbox hoặc endpoint API + tham số).
4. **Human-in-the-loop bắt buộc** với số trích từ PDF trước khi dùng cho golden set.
5. **Scheduler**: bắt đầu bằng K8s CronJob (k3s sẵn có), chuyển Dagster khi số nguồn > ~20.
6. **Data quality rules** chạy sau mỗi release: miền giá trị, đơn vị, tổng thành phần = tổng (dung sai), biến động bất thường so với kỳ trước.

---

## 4. Kiến trúc mục tiêu

- [02-target-architecture](diagrams/02-target-architecture.puml): 6 tầng, tô màu dùng lại / mở rộng / xây mới.
- [04-question-sequence](diagrams/04-question-sequence.puml): luồng trả lời một câu hỏi chỉ số, gồm chọn ngưỡng **theo kỳ dữ liệu** (năm 2025 không được áp ngưỡng NQ 26/2026 hiệu lực 2026–2030) và bước kiểm số trước khi hiển thị.
- [05-indicator-data-model](diagrams/05-indicator-data-model.puml): mô hình dữ liệu lõi, tách 3 tầng Indicator / Series-Variable / Observation như khuyến nghị cho ECO-001.5 v1.1.

Nguyên tắc xuyên suốt: **LLM không tự tính số**. LLM hiểu câu hỏi, gọi tool, diễn giải và trích dẫn; Computation Engine sinh số; Numeric verifier chặn mọi số không có trong evidence.

---

## 5. Giao diện người dùng cuối

### 5.1 Chức năng theo vai trò

| Vai trò | Chức năng | Hiện có để tái dùng |
|---|---|---|
| **Lãnh đạo** | Dashboard ANKT (heatmap domain × trạng thái ngưỡng); hộp cảnh báo; hỏi đáp có bằng chứng; bản tin/báo cáo PDF-DOCX; drill-down từ domain → chỉ số → nguồn | Ask Data, ExecutiveDashboard (chỉ số nền tảng) |
| **Chuyên viên phân tích** | Danh mục chỉ số (lọc theo domain/sub-domain); **trang chỉ số**: định nghĩa, công thức, biểu đồ chuỗi thời gian có **dải ngưỡng theo giai đoạn**, chuyển vintage, phân rã biến động (tử/mẫu); so sánh VN vs IMF/WB kèm cảnh báo phạm vi; xuất Excel + nguồn; lưu phân tích; phản hồi đúng/sai | Ask Data (evidence strip, chart, citation, xlsx export) |
| **Data steward / nhóm research** | Registry editor (chỉ số, biến, ngưỡng, nguồn — nhập từ YAML ECO-001.5); **workbench duyệt trích xuất**: ảnh trang + highlight ô + sửa giá trị; quản lý release/vintage; bảng reconciliation; cấu hình connector & lịch; giám sát DQ | Knowledge Ops (upload, preview PDF, StructuredTableReview), SchedulingCenter (UI) |
| **QA / Eval** | Quản lý golden set (ECO-008); chạy eval theo phiên bản model/cấu hình; so sánh lần chạy; dashboard benchmark model | Playground, Traces, Langfuse |
| **Quản trị** | RBAC theo phân loại dữ liệu, audit, phê duyệt chỉ số (maker–checker) | Settings/IAM, Audit |

### 5.2 Chưa có trong frontend (khảo sát)

Danh mục/trang chỉ số, biểu đồ có ngưỡng, hộp cảnh báo, báo cáo PDF/DOCX, UI golden set/eval, xem tài liệu tới trang + highlight, lineage, so sánh, kịch bản what-if, phản hồi người dùng, lịch sử hội thoại phía server (hiện lưu localStorage), i18n (nhãn trộn Việt/Anh).

### 5.3 Phạm vi UI cho Demo 1 (golden use case)

1. **Ask Data**: thẻ kết quả chỉ số (giá trị, kỳ, vintage, trạng thái ngưỡng) + trích dẫn mở đúng trang tài liệu.
2. **Trang chỉ số Nợ công/GDP**: định nghĩa, công thức, biểu đồ chuỗi năm có dải ngưỡng, bảng quan sát kèm nguồn.
3. **Kết quả eval**: bảng case đạt/không đạt (dùng Langfuse Datasets UI trước, chưa cần xây màn riêng).

---

## 6. Trả lời yêu cầu ECO-008.14: đáp án gắn tài liệu nguồn + dung sai

### 6.1 Khả thi trên hạ tầng hiện có

| Thành phần cần | Đã có | Cần làm thêm | Ước lượng |
|---|---|---|---|
| Lưu tài liệu gốc bất biến | MinIO bucket `knowledge-base`, upload UI | Tính SHA-256, chống trùng | 1 ngày |
| Metadata văn bản (tên, số hiệu, cơ quan, ngày ban hành, hiệu lực) | — | Bảng `source_document` + form trong IngestionWizard | 2–3 ngày |
| Trang/bbox của đoạn và bảng | Docling trả `prov` (page_no, bbox) | Lưu vào `ParsedChunk`/payload Qdrant; re-ingest tài liệu ANKT | 2–3 ngày |
| Mở đúng trang khi bấm trích dẫn | Preview PDF bằng iframe | Thêm `#page=N` + trả `page` trong citation | 1 ngày |
| Lưu golden set có phiên bản | Langfuse v3 đang chạy (chưa dùng Datasets/Scores) | Đẩy case lên Langfuse Datasets, ghi điểm từng lần chạy | 2 ngày |
| Chấm số có dung sai | Gate + scorer grounding (khớp chuỗi) | Scorer mới: số, trích dẫn, vintage, trạng thái ngưỡng | 3 ngày |
| Chặn release | `release-gate.yml` | Thêm metric vào `thresholds.json` | 0,5 ngày |

→ Tổng **khoảng 2 tuần công** cho 1 người backend + 0,5 người frontend.

### 6.2 Schema một golden case (đề xuất)

```json
{
  "case_id": "PD-GDP-L1-2024-001",
  "category": "L1_lookup",
  "question": "Tỷ lệ nợ công so với GDP năm 2024 là bao nhiêu?",
  "indicator_id": "ECO-D2-S22-001",
  "reference_period": "2024",
  "expected": { "value": "<điền từ ECO-009>", "unit": "%", "decimal_precision": 1 },
  "tolerance": { "type": "absolute", "value": 0.05, "unit": "percentage_point",
                 "basis": "published_precision" },
  "expected_threshold_status": "<below_warning | warning | above_ceiling>",
  "source": {
    "title": "<tên tài liệu>", "doc_number": "<số hiệu>", "issuer": "Bộ Tài chính",
    "issued_date": "<YYYY-MM-DD>", "page": "<trang>", "table_ref": "<bảng/ô>",
    "vintage": "<ví dụ 2024-uoc-thuc-hien>", "sha256": "<hash file gốc>"
  },
  "answerable": true,
  "reviewer": "<người duyệt>", "approved_at": "<datetime>"
}
```

### 6.3 Chính sách dung sai (đề xuất thay cho “±0,1 điểm % cho mọi case”)

Dung sai nên **suy ra từ cách tạo ra con số**, không đặt một mức chung:

| Loại case | Dung sai | Lý do |
|---|---|---|
| **Tra cứu số đã công bố** (tỷ lệ in sẵn trong bản tin) | So sau khi làm tròn tới `decimal_precision` công bố → **phải khớp tuyệt đối** (tương đương ±0,05 điểm %) | Không có phép tính nào → không có nguồn sai số hợp lệ |
| **Tự tính từ tử số, mẫu số đã công bố** | ±(0,05 + sai số lan truyền do làm tròn tử/mẫu), **trần 0,1 điểm %** | Dư nợ và GDP công bố ở đơn vị nghìn tỷ/tỷ đồng nên sai số lan truyền rất nhỏ; ±0,1 là giới hạn an toàn |
| **Số tiền tuyệt đối** | Nửa đơn vị của chữ số cuối được công bố (ví dụ công bố tới nghìn tỷ → ±0,5 nghìn tỷ) | Theo `unit_multiplier` + `decimal_precision` |
| **Đối chiếu nguồn khác phạm vi** (VN vs IMF) | **Không dùng dung sai**; chấm việc giải thích đúng nguyên nhân chênh lệch | Chênh lệch là do định nghĩa, không phải sai số |
| **Khác vintage** | **Không đạt**, dù số nằm trong dung sai | Đúng số nhưng sai nguồn vẫn là câu trả lời không kiểm chứng được |

Mỗi case chấm **4 điểm độc lập**: `value_correct`, `source_correct` (đúng tài liệu + trang), `vintage_correct`, `threshold_status_correct`; thêm `refusal_correct` cho case không trả lời được (năm chưa công bố, tần suất quý, dự báo).

Ngưỡng release đề xuất cho Demo 1: `value_correct` ≥ 0,95 · `source_correct` = 1,0 · `threshold_status_correct` = 1,0 · `unsupported_number_rate` = 0.

---

## 7. Model cần dựng và benchmark

### 7.1 Ứng viên (cần kiểm tra giấy phép và hỗ trợ tiếng Việt trước khi dựng)

| Nhóm | Hiện dùng | Ứng viên benchmark |
|---|---|---|
| **OCR** | Tắt | Tesseract 5 (`vie`, CPU, làm baseline) · EasyOCR (vi) · PaddleOCR PP-OCRv5 · VietOCR · Surya |
| **Parse tài liệu end-to-end / VLM** | Không có (Qwen3-14B không có thị giác) | Qwen2.5-VL-7B-Instruct (AWQ) · olmOCR · MinerU · dots.ocr · granite-docling-258M |
| **Cấu trúc bảng** | TableFormer (mặc định Docling) | Table Transformer · bảng do VLM trích |
| **Embedding** | bge-m3 | Qwen3-Embedding-0.6B/4B · multilingual-e5-large-instruct · bge-m3 fine-tune tiếng Việt |
| **Reranker** | bge-reranker-v2-m3 | Qwen3-Reranker-0.6B/4B · jina-reranker-v2-base-multilingual |
| **LLM** | Qwen3-14B-AWQ + Qwen3-4B-AWQ | Qwen3-30B-A3B (MoE) · Qwen3-32B-AWQ · gpt-oss-20b |

### 7.2 Bộ đo (dựng từ chính tài liệu ANKT)

| Hạng mục | Dữ liệu | Chỉ số đo |
|---|---|---|
| OCR | 50–100 trang bản tin nợ công, niên giám thống kê, báo cáo NHNN (cả scan và số hoá), gán nhãn tay | CER/WER; **độ chính xác token số** (từng chữ số, dấu phân cách thập phân `,`/`.`) |
| Bảng | Các bảng chứa biến nguyên tử | Cell F1, TEDS, **exact match các ô mục tiêu** |
| Retrieval | Cặp (câu hỏi → tài liệu, trang) từ golden set | Recall@5/10, MRR@10, nDCG@10 |
| LLM end-to-end | Golden set ECO-008 | value/source/vintage/threshold correct, refusal accuracy, text-to-SQL execution accuracy |
| Vận hành | — | VRAM, trang/phút, TTFT, tok/s, p95 latency |

Quan trọng nhất là **độ chính xác token số**: OCR đọc sai một chữ số hoặc nhầm dấu thập phân sẽ tạo ra câu trả lời sai nhưng có vẻ đáng tin.

### 7.3 Ngân sách GPU

A100 40GB đã cấp ~32 GB (vLLM 14B ~21,7 GB, vLLM 4B ~6 GB, TEI embed + rerank ~4 GB). Còn ~8 GB: đủ cho OCR nhỏ và model embedding/rerank benchmark, **không đủ** chạy thường trực VLM 7B cùng lúc với chat.
Đề xuất: (1) benchmark chạy theo lô ngoài giờ; (2) trích xuất tài liệu chạy batch, không realtime; (3) nếu chọn VLM cho production, bổ sung GPU thứ hai (ví dụ 48 GB) cho khối tài liệu. Sửa lỗi driver NVML trước khi thay đổi container GPU.

---

## 8. Lộ trình

Gantt: [06-roadmap-gantt](diagrams/06-roadmap-gantt.puml). Mốc tính từ 21/09/2026 (sau khi data research final cuối tuần 19–20/09).

| Giai đoạn | Thời gian (ước) | Kết quả | Tiêu chí nghiệm thu |
|---|---|---|---|
| **GĐ0 · Golden use case Nợ công/GDP** | 21/09 → ~20/10 | Source registry + trang/bbox; eval case v2 + scorer dung sai + Langfuse Datasets; indicator registry + observation/vintage store; computation engine (ratio, căn kỳ, ngưỡng theo kỳ); resolver + numeric verifier; golden set v1 | **Demo 1**: L1–L3 (tra cứu, tính & so ngưỡng, giải thích biến động); các ngưỡng mục 6.3 |
| **GĐ1 · 6 chỉ tiêu nhóm A** | ~01/10 → ~13/11 (chạy song song cuối GĐ0) | Connector bản tin PDF/NSO/SBV + WB/IMF API + CronJob; workbench duyệt trích xuất; reconciliation + DQ; trang chỉ số; chọn model OCR/VLM/embedding qua benchmark | **Demo 2**: 6 chỉ tiêu có số, nguồn, vintage, ngưỡng; reconciliation khớp số chính thức |
| **GĐ2 · ~30 chỉ số domain 2–3** | ~16/11 → ~25/12 | Registry editor + quy trình duyệt; alert engine + hộp cảnh báo; dashboard ANKT + báo cáo PDF/DOCX; RBAC theo phân loại; golden set ≥300 case + benchmark định kỳ | **Demo 3**: cảnh báo theo ngưỡng, bản tin tự sinh có trích dẫn |
| **GĐ3 · Dự phóng & cảnh báo sớm** | 2027 | DSA/stress test (nhóm G), dự báo, EWS, kịch bản what-if; mở rộng domain 1, 4, 5 | Backtest ngoài mẫu đạt ngưỡng thống nhất với nghiệp vụ |

**Việc có thể bắt đầu ngay tuần này (không chờ research final):**
1. Sửa lỗi driver GPU (reboot có kế hoạch).
2. Eval case schema v2 + scorer dung sai trên dữ liệu giả lập.
3. Lưu `page_no`/`bbox` từ Docling và bảng `source_document`.
4. Bảng registry + observation, nạp YAML mẫu Nợ công/GDP từ ECO-001.5.

---

## 9. Quyết định cần chốt

1. **Chính sách dung sai** theo loại case (mục 6.3) thay cho một mức ±0,1 điểm % chung.
2. **Vintage mặc định** khi người dùng không nói rõ: “bản mới nhất đã công bố tại thời điểm hỏi” hay “bản chính thức/quyết toán gần nhất”.
3. **Phạm vi Demo 1**: chỉ L1–L3, hay thêm L4 (đối chiếu IMF/WB kèm cảnh báo phạm vi).
4. **Hạ tầng**: có bổ sung GPU cho khối tài liệu/VLM không; lịch reboot sửa driver.
5. **Mức mật dữ liệu** được đưa vào hệ thống và dùng cho benchmark/huấn luyện.
6. **Sửa ECO-001.5 v1.1** (tách 3 tầng, `thresholds[]`, `stock_or_flow: ratio`, trường `on_lending`) trước ECO-007.3.

---

## 10. Danh mục tài liệu và sơ đồ

| Tài liệu | Nội dung |
|---|---|
| [README.md](README.md) | Đánh giá hệ thống cho golden use case Nợ công/GDP, ECO-008.14, model benchmark |
| [PLATFORM-ARCHITECTURE.md](PLATFORM-ARCHITECTURE.md) | Kiến trúc tổng quát cho vô số chỉ số: dữ liệu đầu vào, agent core, Data Studio |
| [SERVICE-DESIGN.md](SERVICE-DESIGN.md) | Service, kết nối, database, luồng dữ liệu/nghiệp vụ/giao diện |
| [OPS-DESIGN.md](OPS-DESIGN.md) | GitOps, DevOps, monitoring, MLOps, DataOps, backup/DR |
| [POC-PLAN.md](POC-PLAN.md) | Kế hoạch dựng POC 16/09–23/10/2026 |
| [DATA-SOURCES.md](DATA-SOURCES.md) · [data/sources.yaml](data/sources.yaml) | Nguồn dữ liệu đầu vào |

Quy ước màu trong sơ đồ: **xanh lá = đã có · xanh dương = có một phần · vàng = chưa có** (một số sơ đồ kiến trúc dùng nhãn tương đương *reuse / extend / new*).

Render: `docker run --rm -v "$PWD:/data" -w /data plantuml/plantuml:1.2024.8 -tsvg -charset UTF-8 *.puml` trong thư mục `diagrams/`. **Ghim bản 1.2024.8**: bản 1.2026.8 (tag `latest`) coi cú pháp tô màu action `#màu:...;` là lỗi thời — báo lỗi trong nhánh `if/else` (sơ đồ 03, 18, 23, 25, 27, 30) và vẽ sai ở chỗ khác (35); cú pháp mới `:...; <<#màu>>` thì bản 1.2024.8 lại chưa hỗ trợ. Nếu extension IDE dùng bản mới, xem file `.svg` render sẵn.

| # | Sơ đồ | Nhóm |
|---|---|---|
| 01 | [Kiến trúc hiện tại](diagrams/01-current-architecture.puml) | Đánh giá |
| 02 | [Kiến trúc mục tiêu golden use case](diagrams/02-target-architecture.puml) | Đánh giá |
| 03 | [Luồng dữ liệu một chỉ số](diagrams/03-indicator-pipeline.puml) | Đánh giá |
| 04 | [Sequence trả lời câu hỏi chỉ số](diagrams/04-question-sequence.puml) | Đánh giá |
| 05 | [Mô hình dữ liệu lõi](diagrams/05-indicator-data-model.puml) | Đánh giá |
| 06 | [Gantt lộ trình](diagrams/06-roadmap-gantt.puml) | Đánh giá |
| 07 | [Các lớp nền tảng đa chỉ số](diagrams/07-platform-architecture.puml) | Kiến trúc |
| 08 | [Service lớp dữ liệu đầu vào](diagrams/08-data-ingestion-services.puml) | Kiến trúc |
| 09 | [Agent core: sub-agent, MCP, API](diagrams/09-agent-core.puml) | Kiến trúc |
| 10 | [Sequence multi-agent](diagrams/10-multi-agent-sequence.puml) | Kiến trúc |
| 11 | [Chức năng Data Studio](diagrams/11-data-ui-map.puml) | Kiến trúc |
| 12 | [Mô hình dữ liệu tổng quát](diagrams/12-generalized-data-model.puml) | Kiến trúc |
| 13 | [Kiến trúc POC](diagrams/13-poc-architecture.puml) | POC |
| 14 | [Gantt POC](diagrams/14-poc-gantt.puml) | POC |
| 15 | [Bản đồ service](diagrams/15-service-landscape.puml) | Service |
| 16 | [Kết nối service](diagrams/16-service-connections.puml) | Service |
| 17 | [Database theo service](diagrams/17-database-ownership.puml) | Service |
| 18 | [Luồng dữ liệu đầu–cuối](diagrams/18-dataflow-end-to-end.puml) | Service |
| 19 | [Nghiệp vụ: vòng đời chỉ số](diagrams/19-bf-indicator-lifecycle.puml) | Service |
| 20 | [Nghiệp vụ: kỳ công bố số liệu](diagrams/20-bf-release-lifecycle.puml) | Service |
| 21 | [Nghiệp vụ: xử lý cảnh báo](diagrams/21-bf-alert-handling.puml) | Service |
| 22 | [Giao diện: lãnh đạo](diagrams/22-uf-executive.puml) | Service |
| 23 | [Giao diện: chuyên viên](diagrams/23-uf-analyst.puml) | Service |
| 24 | [Sequence trang chỉ số](diagrams/24-seq-indicator-page.puml) | Service |
| 25 | [CI/CD hiện tại](diagrams/25-cicd-current.puml) | Ops |
| 26 | [GitOps mục tiêu](diagrams/26-gitops-target.puml) | Ops |
| 27 | [Môi trường & promote](diagrams/27-environments-promotion.puml) | Ops |
| 28 | [Hạ tầng](diagrams/28-infra-topology.puml) | Ops |
| 29 | [Observability](diagrams/29-observability-architecture.puml) | Ops |
| 30 | [Cảnh báo & sự cố](diagrams/30-alerting-incident-flow.puml) | Ops |
| 31 | [Vòng đời MLOps](diagrams/31-mlops-lifecycle.puml) | Ops |
| 32 | [Kiến trúc MLOps](diagrams/32-mlops-architecture.puml) | Ops |
| 33 | [DataOps](diagrams/33-dataops-observability.puml) | Ops |
| 34 | [Backup & DR](diagrams/34-backup-dr.puml) | Ops |
| 35 | [Bảo mật chuỗi cung ứng](diagrams/35-security-supply-chain.puml) | Ops |
| 36 | [Bản đồ trạng thái toàn nền tảng](diagrams/36-capability-status-map.puml) | Tổng hợp |
