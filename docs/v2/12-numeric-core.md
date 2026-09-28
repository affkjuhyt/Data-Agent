# 12. Lõi số: lỗi đã sửa, kỹ thuật, rủi ro, quyết định

> Đối tượng: BE làm P0-01/P0-04/S1-02/S8-01/S10-01, người review `backend/libs/indicator`. Đo ngày
> 27/09/2026 trên `feat/remove-redundant-service`. Phần kỹ thuật số của thiết kế v1 được giữ lại ở
> đây và viết lại theo V2; nơi hiện thực nằm ở [03](03-target-design.md) mục 3.4, 9, 10.3.
>
> **Đổi mã:** thiết kế v1 đánh số E1–E3, T1–T18, R1–R8, ADR-T1…T12. Vì [04](04-sources-oil-gold.md)
> mục 6 đã dùng T1–T12 cho bẫy dữ liệu, mọi mã ở file này thêm tiền tố `NC-` (numeric core):
> E2 → NC-E2, T1 → NC-T1, R2 → NC-R2, ADR-T5 → NC-ADR-T5. Docstring trong mã và test dùng mã mới.

## 0. Kết luận

1. **Đơn vị sự thật đổi trục.** Hệ cũ coi sự thật là "một câu SQL trên warehouse". V2 coi sự thật là
   *một observation đã công bố trong một release cụ thể, có bốn trục thời gian* (00 bất biến 8).
   Mọi kỹ thuật dưới đây phục vụ việc chuyển trục đó.
2. Ba lỗi cùng một họ, "hệ thống coi con số là chuỗi ký tự" (mục 1): E1, E2 đã sửa; **E3 còn mở** (P0-02).
3. Lõi số đã nằm trong repo: `backend/libs/indicator/` (1.179 dòng) và test unit; verifier tất định
   đã nối vào `judge`. Còn thiếu: kỳ D/W/I/P và đơn vị giá hàng hoá (P0-04), đọc as-of trên ClickHouse
   `obs` (S1-02 viết mới; `observation.py` cũ đọc schema `ankt.*` không tồn tại, đã xoá 28/09), operator chuỗi
   thời gian (S8-01), freshness và đủ bằng chứng trong verifier (S10-01).
4. LLM không tính số, không so ngưỡng, không chấm số (00 bất biến 6). Mọi chỗ cần chắc chắn là mã tất
   định có test.

## 1. Ba lỗi đang tạo số sai trông đáng tin

| Mã | Lỗi | Trạng thái 27/09 | Bằng chứng |
| --- | --- | --- | --- |
| NC-E1 | Docling chạy `do_ocr=False`: PDF scan vào hệ thống như tài liệu rỗng, không lỗi, không cảnh báo | **Đã sửa**: OCR Tesseract `vie+eng`, cấu hình cố định để cùng ảnh luôn ra cùng chữ. Chưa có benchmark độ chính xác token số (NC-ADR-T8) | `backend/libs/ingestion/docling_parser.py:55-71` |
| NC-E2 | Scorer `_numbers()` xoá dấu phân cách, nên `128,4` và `1284` là cùng một số | **Đã sửa**: `grounding.py` dùng `libs.indicator.numeric` (đọc theo locale, giữ mọi cách đọc khi mơ hồ, so ở độ chính xác công bố) | `model/eval/scorers/grounding.py:36`, `tests/unit/workflow_runtime/test_eval_numeric.py` |
| NC-E3 | `formatVN.ts` định dạng theo chuẩn Mỹ (`1,284,560`, `MM/dd/yyyy`) dù tên gợi ý ngược lại | **Còn mở** (P0-02). Quy ước đích ở [13](13-ui-principles.md) mục 2 | `frontend/src/lib/formatVN.ts:2` |

## 2. Kỹ thuật lõi số

### NC-T1. Số học có kiểu: Decimal, đơn vị, bội số, tiền tệ

| | |
| --- | --- |
| Trạng thái | **Có**: `libs/indicator/quantity.py`, `tests/unit/workflow_runtime/test_quantity.py` |
| Hợp đồng | `Quantity` bất biến `{value: Decimal, unit, multiplier (số mũ), currency}`. Constructor từ chối float. Cộng/trừ đòi cùng đơn vị và bội số; tỷ lệ cùng đơn vị ra không thứ nguyên; `%` trừ `%` ra **điểm %**. Làm tròn một lần, ở cuối, theo `decimal_precision` |
| Không làm | Không quy đổi tiền tệ bên trong: tỷ giá có kỳ, nguồn và **phía** (mua/bán/chuyển khoản, bẫy T8 ở 04 mục 6) nên là observation, không phải hằng số. Không dùng `pint`: đơn vị ở đây là đơn vị nghiệp vụ |
| Mở rộng V2 (P0-04) | `money_per(barrel \| troy_oz \| luong \| litre \| kg \| tonne \| gallon)`; 1 lượng = 1,20565 troy oz (03 mục 3.4) |
| Test | Property: `a + b - b == a` với Decimal; quy đổi lượng ↔ oz; `%` − `%` |

### NC-T2. Lưu trữ point-in-time

| | |
| --- | --- |
| Trạng thái | **Chưa có.** Bộ đọc as-of cũ `libs/indicator/observation.py` đọc schema `ankt.*` của v1 (không tồn tại trên cụm) và không ai import, đã xoá 28/09. S1-02 viết mới trên ClickHouse trong `indicator_api` (ADR-V2-36: `libs/indicator` không sở hữu I/O) |
| Thiết kế V2 | ClickHouse `obs.observation` (03 mục 3.2): chỉ ghi thêm, mỗi version effective là một dòng, bốn trục `event_time_*`, `publication_time`, `available_at`, `system_time`, cùng `effective_version`, `version_kind` |
| Truy vấn | `argMax(value, (available_at, effective_version))` với `available_at <= :as_of AND system_time <= :as_of_system`. Hai chế độ: "thế giới biết gì" và "hệ thống biết gì" (08 UC13) |
| Bẫy | NC-R4: ReplacingMergeTree gộp bất đồng bộ, không được giả định đã gộp |
| Test | Hai version cùng khoá chưa gộp, as-of ở giữa trả version cũ; ghi lại cùng giá trị không tạo dòng mới (03 mục 3.3); EIA thứ Hai 21/09 as-of trả Brent 15/09 (release 16/09) |

### NC-T3. Formula DAG

| | |
| --- | --- |
| Trạng thái | **Có**: `libs/indicator/formula.py` (`Ratio`, `Sum`, `Difference`, `AsPercent`, `Scale`, `compile_formula`, `evaluate`, `decompose_change`), `tests/unit/workflow_runtime/test_formula.py` |
| Nguyên tắc | Kiểm kiểu **lúc khai báo** công thức (chỉ cần khai báo biến), không phải lúc người dùng hỏi. Mỗi cạnh mang `Quantity`. Stock/flow phải khai báo quy tắc căn kỳ (NC-R2) |
| Mở rộng V2 | `Lag`, `LogReturn`, cửa sổ trượt, `FxConvert` (hôm nay chỉ có `Ratio/Sum/Difference/AsPercent/Scale`); chuỗi dẫn xuất khai báo ở `registry.series.derived_formula` và tính lại khi `observation.published` (03 mục 8.4): spread Brent–WTI, return, volatility, basis spot–futures, chênh vàng trong nước |
| Test | Tính lại cả chuỗi lịch sử, so với số công bố; lệch quá dung sai ([06](06-evaluation-plan.md) mục 4) thì không publish |

### NC-T4. Ngưỡng theo khoảng hiệu lực

| | |
| --- | --- |
| Trạng thái | **Có** phần chọn: `libs/indicator/threshold.py` chọn ngưỡng theo **kỳ của dữ liệu**, không theo ngày hỏi; `tests/unit/workflow_runtime/test_threshold.py`. Chưa có bảng |
| Dùng trong V2 | Ngưỡng luật DQ và alert có `valid_from/valid_to` (03 mục 3.1); regime thị trường (spread Brent–WTI 2026, 08 UC05) là ngưỡng có hiệu lực, không phải z-score toàn lịch sử |
| Ràng buộc | Hai ngưỡng cùng mức không chồng khoảng: `EXCLUDE USING gist` ở Postgres (NC-ADR-T4), không kiểm ở ứng dụng |
| Test | Case ở ranh giới hai khoảng hiệu lực |

### NC-T5. Verifier số tất định

| | |
| --- | --- |
| Trạng thái | **Có và đã nối**: `libs/indicator/verifier.py:99-111` chạy bốn kiểm tra độc lập; `workflow_runtime/nodes/model_harness/judge.py:78-90` chạy chúng **trước** khi hỏi LLM, trượt là `GROUNDING_ERROR` |
| Bốn kiểm tra | `numbers_grounded` (mọi số trong câu trả lời có trong evidence, theo dung sai) · `precision_supported` (không nêu số chính xác hơn nguồn công bố) · `vintage_consistent` (một câu trả lời không trộn hai release) · `citations_resolved` (mọi `[n]` trỏ tới evidence có thật) |
| Mở rộng V2 (S10-01) | Freshness (nguồn quá `freshness_sla`), đủ bằng chứng theo `evidence_requirements` của task graph, replan tối đa 2 vòng rồi `INSUFFICIENT_EVIDENCE` (03 mục 10.3). Kết quả đi qua frame `__verification` tới VerifierBadge ([13](13-ui-principles.md) P8) |
| Không làm | Không chấm số bằng LLM (NC-ADR-T5) |

### NC-T6. Giải entity/series

| | |
| --- | --- |
| Trạng thái | **Chưa có** (S1-03). Resolver hiện có (`libs/semantic/resolver.py`, "đồng nghĩa dài nhất thắng") tất định và giải thích được nhưng chỉ cho metric warehouse |
| Thiết kế | Ba tầng, dừng ở tầng đầu tiên trúng: (1) alias tất định trong `registry.entity` ("Brent", "dầu Brent", "Europe Brent Spot" → `BRENT_SPOT`); (2) hybrid BM25 + dense, gộp bằng `rrf_merge` có sẵn; (3) LLM chỉ chọn trong top-k. Mơ hồ (spot hay futures?) → hỏi lại, không đoán (NC-ADR-T7) |

### NC-T7. Trích xuất tài liệu có toạ độ

| | |
| --- | --- |
| Trạng thái | **Một phần**: OCR bật (NC-E1); `PageRegion` (page, bbox gốc trên-trái, kích thước trang) ở `libs/basemodel/services_docling/docling_model.py:17`; Silver/Gold và payload Qdrant ghi page/bbox; citation có `page`; `tests/unit/kb_promote_silver/test_docling_regions.py` |
| Còn thiếu | Link `#page=N` trên citation và re-ingest tài liệu demo (S4-01); khung bbox trên UI, heading "(continued)", bảng qua trang, bảng → observation candidate có `source_ref` trang/ô (S4-01b, Tier B) |
| Quy tắc | Số trích từ OCR/VLM mang `confidence`, luôn qua DQ numeric; dưới ngưỡng thì ReviewCase `EXTRACTION_LOW_CONF` (08 UC02) |
| Đo | Độ chính xác **token số** (từng chữ số và dấu phân cách), không phải CER/WER: CER thấp vẫn có thể sai đúng chữ số quan trọng. Bảng: Cell F1, TEDS |

## 3. Các kỹ thuật khác của thiết kế v1: V2 xử lý ở đâu

| Mã | Kỹ thuật | Trong V2 |
| --- | --- | --- |
| NC-T8 | QuerySpec compiler (mở rộng `AnalyticalSlots`, lỗi có cấu trúc) | 03 mục 9, S8-01 |
| NC-T9 | MCP đúng giao thức + registry tool động | 03 mục 10.1–10.2: một MCP Gateway trong `indicator-api`; POC gọi capability in-process (S9-01), gateway chuẩn là Tier B (S9-01b) |
| NC-T10 | Thu thập theo lịch, phát hiện bản mới (ETag → SHA-256 → cấu trúc) | Đã có một phần ở `web_crawler`; release + freshness ở 03 mục 4 |
| NC-T11 | DQ và đối chiếu (tái dùng `check_subtotals`, MAD thay z-score) | 03 mục 8.1–8.2, S6-01, S6-02 |
| NC-T12 | Alert engine (dedup theo rule, entity, cửa sổ) | 03 mục 11, S11-01 |
| NC-T13 | Lineage và phân tích tác động | Consumer lineage của `observation.published` (03 mục 13), frame `__lineage` |
| NC-T14 | Eval số (case có dung sai, scorer độc lập) | [06](06-evaluation-plan.md), S10-02 |
| NC-T15 | Kết xuất báo cáo PDF/DOCX | Ngoài POC; khối trả lời E-01 xuất CSV/XLSX có sẵn |
| NC-T16 | Thông báo realtime | SSE + Redis pub/sub (cùng kỹ thuật `cache_invalidation`), 08 UC07 |
| NC-T17 | Gói chuỗi thời gian trong sandbox (scipy, statsmodels) | Không mở sandbox; thống kê chạy trong operator và `forecast-worker` (03 mục 12) |
| NC-T18 | Bật và đo cờ LightRAG | `DOC_NARROWING=1`, `QUERY_ALIAS_RESOLVE=1`; `HYBRID_LEXICAL`, `DEGRADE_BUDGET_MS` vẫn tắt, chưa đo (`deploy/k8s/*/service/kb-backend.yaml`) |

## 4. Rủi ro, xếp theo mức nguy hiểm

| Mã | Rủi ro | Vì sao nguy hiểm | Giảm thiểu |
| --- | --- | --- | --- |
| NC-R1 | Sai một chữ số hoặc dấu thập phân trên chuỗi OCR → mapping → tính → hiển thị | Câu trả lời sai nhưng trông đáng tin | Đo độ chính xác token số; Decimal; verifier; số từ OCR/VLM luôn qua người duyệt; sửa NC-E3 |
| NC-R2 | Sai căn kỳ stock/flow (tồn kho cuối tuần chia tiêu thụ bình quân tháng; số cuối kỳ chia tổng cả năm khi kỳ là quý) | Sai theo hệ số, không phải sai làm tròn | Khai báo stock/flow bắt buộc; kiểm ở `compile_formula`; tính lại chuỗi và so số công bố |
| NC-R3 | Áp nhầm ngưỡng/regime theo ngày hỏi thay vì kỳ dữ liệu | Làm sai kết luận, không chỉ chữ số cuối | Chọn theo kỳ (`threshold.py`); ràng buộc không chồng khoảng; case ở ranh giới |
| NC-R4 | ReplacingMergeTree chưa gộp → đọc ra version cũ | Im lặng, không lỗi | Luôn `argMax`; test hai version chưa gộp (NC-T2) |
| NC-R5 | Retrieval chỉ dense trượt mã định danh (số quyết định, mã series) | Trả "không tìm thấy" cho tài liệu đã có | Bật `HYBRID_LEXICAL` và đo (NC-T18) |
| NC-R6 | GPU A100 40 GB dùng chung giữa chat và trích xuất | VLM không chạy thường trực cùng chat | Trích xuất theo lô; VLM đọc số luôn qua HITL |
| NC-R7 | Gate online chưa chứng nhận gì (`provisional: true`) | Tưởng có gate mà không có | Baseline thật (P0-05), bỏ cờ |
| NC-R8 | DQ báo động giả ở điểm gãy chuỗi/đổi regime | Người duyệt mất niềm tin, bỏ qua cả cảnh báo thật | Khai báo regime/structural break; MAD theo cửa sổ (08 UC05) |

## 5. Quyết định kỹ thuật

| Mã | Quyết định | Chốt | Trạng thái V2 |
| --- | --- | --- | --- |
| NC-ADR-T1 | Kiểu số khi tính | `Decimal` bọc trong `Quantity`; float chỉ cho thống kê/đồ thị | Đã áp dụng (`quantity.py`) |
| NC-ADR-T2 | Thư viện đơn vị | Bảng nội bộ, không `pint` | Đã áp dụng; mở rộng ở P0-04 |
| NC-ADR-T3 | Kho observation | ClickHouse ReplacingMergeTree, luôn `argMax` | Giữ; 00 mục 12, ADR-V2-16 (chỉ bản đã duyệt vào ClickHouse) |
| NC-ADR-T4 | Ngưỡng không chồng kỳ | Ràng buộc DB `EXCLUDE USING gist` | Giữ; áp cho bảng ngưỡng/regime khi dựng S6-01 |
| NC-ADR-T5 | Verifier số | Bốn kiểm tra tất định; LLM không chấm số | Đã áp dụng (`verifier.py`, `judge.py:78`) |
| NC-ADR-T6 | Ngôn ngữ truy vấn | Một schema QuerySpec, không tạo ngôn ngữ thứ ba | Giữ (03 mục 9) |
| NC-ADR-T7 | Tìm entity/series | Ba tầng: alias → hybrid RRF → LLM chọn trong top-k | Giữ (NC-T6) |
| NC-ADR-T8 | Model OCR/VLM | Chọn qua benchmark độ chính xác token số; lưu `model_version` vào mọi kết quả | Mở: Tesseract đang chạy làm baseline, chưa benchmark |
| NC-ADR-T9 | Luật DQ | Tự xây trong `libs/dq`, không Great Expectations/Soda | Giữ (08 UC05) |
| NC-ADR-T10 | Render PDF | WeasyPrint | Ngoài POC |
| NC-ADR-T11 | Scheduler | K8s CronJob → Dagster khi cần | Giữ (00 mục 12) |
| NC-ADR-T12 | Cờ LightRAG | Bật và đo từng cờ trên bộ retrieval của golden set | Mở: hai cờ đã bật chưa đo |
