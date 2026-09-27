# 08. Mười bốn use case POC: đặc tả để hiện thực

> Mỗi UC ghi: luồng, nơi hiện thực trong repo, vấn đề bắt buộc xử lý, dữ liệu thật để kiểm, điều
> kiện xong, và ID việc trong [05-implementation-plan.md](05-implementation-plan.md). Kiến trúc gốc:
> [00-architecture-v2.md](00-architecture-v2.md). Thiết kế bảng/topic: [03-target-design.md](03-target-design.md).
> Dữ liệu thật: [04-sources-oil-gold.md](04-sources-oil-gold.md) (bẫy T1–T12).

## UC01. Onboard structured source

- **Luồng:** API/CSV/XLSX → fetch → raw bất biến → profile → reverse schema → mapping → normalize → DQ → observation.
- **Repo:** `platform_workers/workers/web_crawler` (fetch, Bronze, release) → topic `artifact.stored` →
  `platform_workers/workers/series_extractor` (profile, map, normalize, candidate) → `governance` → ClickHouse `obs`.
- **Phải xử lý:**
  - *Schema drift*: cột `price` thành `close_price` phải bị phát hiện (fingerprint profile khác bản đã duyệt) và chặn, không nạp im lặng (UC03).
  - *Thời gian*: lưu `event_time`, `market_timezone`, `publication_time`, `available_at`, `system_time`.
  - *Trùng*: connector retry 3 lần không tạo 3 observation. Khoá idempotency
    `(source_id, series_key, event_time, release_id, content_hash)`.
  - Mã thiếu theo nguồn (`-`, `NA`, `W`, `x`, `…`) thành trạng thái, không thành 0 (T9).
- **Dữ liệu thật:** EIA `RBRTEd.xls` (release thứ Tư), IMF PCPS SDMX, Vietcombank XML (locale en-US, 1 request/5 phút).
- **Xong khi:** thêm nguồn IMF PCPS bằng UI không cần code; chạy lại không sinh bản ghi; Data Layers thấy artifact; Series Explorer thấy series với release.
- **ID:** S2-01…S2-03, S3-01, S3-04.

## UC02. Onboard unstructured source

- **Luồng:** PDF → trang → layout → heading → đoạn → bảng → hình → trích xuất ngữ nghĩa.
- **Repo:** pipeline KB có sẵn (`kb_promote_silver`, `kb_promote_gold`, `libs/ingestion/docling_parser.py`) + `PageRegion` (cứu từ `feat/add-task`) + worker `semantic_extractor`.
- **Phải xử lý:**
  - Heading "3.1 Supply (continued)" không phải mục mới: gộp theo số hiệu mục và chuỗi tiêu đề.
  - Bảng tách qua hai trang: nối khi trang sau không có header mới và số cột khớp.
  - Hình không có bảng: lưu ảnh vùng làm evidence (page + bbox); VLM đọc số là tuỳ chọn, số đọc bằng VLM luôn qua HITL.
  - OCR đọc `72.34` thành `7234`: observation candidate từ OCR có `confidence` và luôn qua DQ numeric; dưới ngưỡng thì ReviewCase.
- **Dữ liệu thật:** EIA WPSR (HTML), OPEC MOMR (PDF tải tay), SPDR GLD holdings (URL `.csv` nhưng là PDF 120 trang).
- **Xong khi (POC):** citation mở đúng trang. **Tier B:** khung bbox; bảng GLD thành observation candidate có source_ref trang/ô.
- **ID:** S4-01 (page/bbox, `#page=N`) trong POC; S4-01b (heading, bảng qua trang, hình) là Tier B.

## UC03. Reverse schema

- **Luồng:** dữ liệu lạ (`period, settle, chg, pct`) → profiler → LLM đề xuất
  `period→reference_time, settle→settlement_price, chg→price_change, pct→percentage_change` → kiểm tất định → người duyệt → mapping có version.
- **Repo:** module `reverse_schema` trong `series_extractor`; bảng `ingest.mapping_proposal`, `ingest.mapping_version`; màn Data Studio → Nguồn → Mapping.
- **Phải xử lý:**
  - LLM có thể map `settle → close_price` trong khi settle ≠ close. Catalog ngữ nghĩa khai báo các khái niệm **không được coi là tương đương** (`settlement_price` ≠ `close_price`, `spot` ≠ `futures`, `bid` ≠ `ask` ≠ `mid`); validator từ chối mapping vi phạm.
  - Kiểm tất định: kiểu dữ liệu, miền giá trị theo đơn vị, tính đơn điệu của cột thời gian, locale số.
  - Drift: fingerprint = (tên cột chuẩn hoá, kiểu, đơn vị suy luận, locale). Khác bản đã duyệt → chặn nạp + ReviewCase `SCHEMA_DRIFT`.
- **Dữ liệu thật:** drift ngữ nghĩa RON95-III → E10RON95-III (T5); drift cấu trúc tạo bằng nguồn fixture có kiểm soát (06 mục 6).
- **Xong khi:** nguồn fixture đổi tên cột → nạp bị chặn → proposal mới → duyệt → nạp tiếp; không dòng nào vào store trong lúc chờ.
- **ID:** S3-02, S3-03, S3-05.

## UC04. Semantic extraction

- **Ví dụ vào:** "OPEC expects stronger demand in Q4…"
- **Ra:**

  ```yaml
  claim:
    subject: GLOBAL_OIL_DEMAND
    predicate: EXPECTED_TO_INCREASE
    period: 2026-Q4
    modality: expectation        # fact | expectation | conditional | reported
    attributed_to: OPEC
  source: OPEC_MOMR_2026_09
  evidence: {page: 17, bbox: [...], quote: "..."}
  publication_time: 2026-09-11
  ```

- **Repo:** worker `semantic_extractor` (sau Gold, cho nguồn bật `extract_knowledge`); bảng `knowledge.claim`, `knowledge.event`, `knowledge.evidence`.
- **Phải xử lý:** đại từ ("it", "the group") phải giải về entity; claim có điều kiện giữ điều kiện; dự báo không thành fact (`modality`); thời điểm sự kiện khác thời điểm công bố; nguồn dẫn lại nguồn khác (`attributed_to` khác `source`). Proposal không có `quote` khớp nguyên văn passage bị loại.
- **Phạm vi POC:** extractor chạy cho **thông cáo điều hành giá xăng dầu của Bộ Công Thương** (văn bản → observation candidate + event "điều chỉnh giá"); event/claim dầu từ EIA/OPEC do research nhập qua UI, vẫn qua ReviewCase.
- **Xong khi:** kỳ 24/9/2026 ra đúng giá thế giới bình quân theo sản phẩm, cửa sổ 17/9–24/9, giá bán, quỹ bình ổn, số quyết định, mỗi số có quote; ≥ 5 event dầu đã duyệt cho mỗi cửa sổ giá giảm.
- **ID:** S4-02 (VN-01), EV-01, S5-01.

## UC05. Data Quality

- **Năm tầng:** Schema DQ · Semantic DQ · Numeric DQ · Temporal DQ · Cross-source DQ.
- **Ví dụ:** vàng = −200 (range fail); Brent 7200 USD/thùng (outlier; thường là lỗi bội số/OCR); `reference_time > published_at` (hợp lệ với dự báo, lỗi với giá quan sát).
- **Repo:** `libs/dq/` (luật khai báo trong bảng `governance.dq_rule`, đánh giá bằng Python/Pydantic/SQL); không đưa Great Expectations/Soda vào POC.
- **Phải xử lý:** chế độ thị trường: spread Brent–WTI 18 USD năm 2026 là thật (T12); luật bước nhảy dùng MAD theo cửa sổ và có khai báo regime, không z-score toàn lịch sử.
- **Xong khi:** mỗi tầng có ít nhất một luật chạy trên dữ liệu thật và một case fixture vi phạm; kết quả hiện trên màn Data Quality.
- **ID:** S6-01.

## UC06. Conflict resolution

- **Không kết luận "conflict" ngay.** Kiểm theo thứ tự: cùng benchmark? cùng hợp đồng? spot hay futures? cùng mốc thời gian? múi giờ? tiền tệ? đơn vị? cùng bản công bố?
- **Phân loại:** `NO_CONFLICT`, `REVISION`, `TIME_DIFFERENCE`, `DEFINITION_DIFFERENCE`, `UNIT_DIFFERENCE`, `SOURCE_CONFLICT`, `UNKNOWN`. Chỉ `SOURCE_CONFLICT` và `UNKNOWN` sinh ReviewCase mặc định.
- **Repo:** module `conflict` cạnh DQ; luật trước, graph (quan hệ `SAME_UNDERLYING`, `SUCCEEDS`) sau, LLM chỉ phân loại phần `UNKNOWN`.
- **Phải xử lý:** xung đột giả sinh hàng nghìn ReviewCase là rủi ro vận hành lớn nhất. Đo tỷ lệ ReviewCase/1.000 observation mỗi tuần.
- **Dữ liệu thật:** `data/cross-source-reference-2026-08.csv` (Brent tháng 8: 91,08 / 90,9 / 88,00; spot vs futures 15/9; chênh vàng theo phía tỷ giá).
- **Xong khi:** mọi dòng trong file trên được phân loại đúng cột `expected_classification`.
- **ID:** S6-02.

## UC07. Human-in-the-loop

- **Luồng:** issue → ReviewCase → Authority Resolver → notification → reviewer → correction/override → checker → approved → effective data.
- **Repo:** bảng `governance.review_case`, `correction`, `override`, `approval`; API trong `indicator-api`; hộp thư SSE (Redis pub/sub như `cache_invalidation`); màn Review Inbox; vai trò `DATA_STEWARD`, `REVIEWER` trong Keycloak + `RolePermissions`.
- **Phải xử lý:**
  - Authority: người duyệt được chọn theo (entity, nguồn, loại case), không phải ai cũng duyệt được; maker ≠ checker.
  - OCC: A và B cùng sửa version 3; A lên 4; B gửi `expected_version=3` → `409 VERSION_CONFLICT`.
  - Sửa mapping hoặc thêm override, **không bao giờ** sửa raw.
- **Xong khi:** kịch bản bước 10–15 chạy qua UI; test tích hợp OCC trả 409.
- **ID:** S6-03, S7-01.

## UC08. Structured analytics

- **Ví dụ:** "Giá Brent trung bình 30 ngày qua so với 30 ngày trước?" → LLM sinh

  ```json
  {"operation": "compare_period", "series": "BRENT_SPOT", "aggregation": "mean",
   "period": "P30D", "compare_to": "PREVIOUS_PERIOD", "as_of": null}
  ```

  → validate → compile SQL (SQLGlot, ClickHouse) → thực thi → verifier số.
- **Không cho** LLM sinh SQL tự do trên observation rồi tin kết quả (đường text-to-SQL hiện có chỉ giữ cho warehouse ad-hoc).
- **Phải xử lý:** "30 ngày" là ngày lịch hay phiên (khai báo trong operator); EIA công bố theo tuần nên "30 ngày qua" kết thúc ở ngày có dữ liệu mới nhất, phải nói rõ as-of.
- **Xong khi:** 20 case fact/trend/so sánh trong golden set đạt `value_correct` với dung sai 06.
- **ID:** S8-01 (đã gộp S8-02: QuerySpec là input của operator, SQL dựng bằng SQLGlot trong operator).

## UC09. Knowledge investigation

- **Ví dụ:** "OPEC nhận định gì về nguồn cung?"
- **Luồng:** intent → lọc metadata → định vị tài liệu → định vị mục → truy xuất đoạn → mở rộng bằng chứng → kiểm claim.
- **Retrieval:** BM25 + dense + metadata + rerank + cấu trúc tài liệu. Không cố định `topK=10 → LLM` cho mọi câu. Hạ tầng LightRAG có sẵn; lưu ý bài học d116062: rerank chấm trên `summary` của chunk, summary hỏng làm chunk đúng tụt hạng.
- **Xong khi:** câu trả lời trích claim đã duyệt hoặc passage có trang; thiếu bằng chứng thì `INSUFFICIENT_EVIDENCE`.
- **ID:** S9-02, S10-01.

## UC10. Hybrid investigation (golden use case)

- **Ví dụ:** "Trong 3 năm qua, các lần Brent giảm trên 5% trong 5 ngày là những lần nào, và quanh mỗi lần ±3 ngày các nguồn chính thức nói gì?"
- **Luồng:** ClickHouse → `detect_drop_episodes` → cửa sổ sự kiện → với mỗi cửa sổ: tìm tài liệu, tìm event, duyệt graph, lấy claim → xếp hạng bằng chứng → báo cáo so sánh.
- **Phải xử lý:**
  - Định nghĩa "một lần giảm" là tham số của operator (ngưỡng, số phiên, quy tắc gộp). Với ngưỡng 5%/5 phiên và gộp ≤7 ngày có **32 đợt** trong 3 năm (`data/brent-5d-drop-episodes.csv`): báo cáo phải phân trang hoặc hỏi lại ngưỡng.
  - Chỉ dùng bằng chứng có `available_at` trong cửa sổ, không dùng tài liệu viết sau để "giải thích".
  - Nguồn tài liệu cho 2023–2025 phải được nạp lại (backfill); POC giới hạn ở các đợt có tài liệu trong KB và nói rõ đợt nào thiếu bằng chứng.
- **Xong khi:** bước 25–27 của kịch bản trả bảng + chart + evidence + lineage cho 3 đợt gần nhất (22/09/2026, 25/08/2026, 28/07/2026) với đúng số trong file CSV.
- **ID:** S8-01, S9-01, S9-02, S10-01.

## UC11. Forecast

> **Ngoài POC 1 tháng** (05 mục 6). Đặc tả giữ để làm ngay sau nghiệm thu 27/10.

- **Luồng:** observation → feature point-in-time → tập huấn luyện → rolling backtest → chọn model → forecast (artifact).
- **Model:** baseline last value/random walk; thống kê ARIMA/ETS; ML LightGBM/XGBoost; deep (PatchTST, N-HiTS, foundation model) chỉ là challenger sau POC.
- **Phải xử lý:** *look-ahead leakage*. Mọi feature có `event_time` và `available_at`; snapshot lấy theo `available_at ≤ as_of`. Ví dụ thật: giá spot EIA ngày thứ Hai chỉ có sau thứ Tư.
- **Phạm vi POC:** Brent/WTI spot ngày, vàng tháng (không có vàng ngày sạch licence, 04 mục 7).
- **Xong khi:** báo cáo backtest và trạng thái validated/rejected cho Brent 1D/5D/20D; forecast là object `FORECAST`, không bao giờ `verified`.
- **ID:** S12-01, S12-02.

## UC12. Proactive anomaly/alert

- **Luồng:** `observation.published` → luật tất định rẻ → detector thống kê → có đáng kể? → agent điều tra (chuỗi UC10) → kiểm bằng chứng → alert.
- **Không** gọi LLM cho mọi cập nhật.
- **Phải xử lý:** bão alert. Một cú giảm dầu kích hoạt Brent, WTI, spread, volatility, tỷ lệ vàng/dầu, forecast. Cần dedup (khoá `rule, entity, window`), gom nhóm theo sự kiện gốc, cooldown, suppression, tương quan. Năm 2026 có 8 đợt giảm ≥5%/5 phiên; không cooldown thì mỗi đợt sinh nhiều alert/ngày.
- **Xong khi:** replay release EIA 23/09/2026 sinh đúng **một** alert cho đợt giảm 12,16% (130,80 → 114,89) kèm investigation, không sinh alert trùng cho WTI/spread cùng sự kiện.
- **ID:** S11-01, S11-02.

## UC13. Historical/as-of

- **Câu hỏi:** "Ngày 15/9 hệ thống lúc đó biết gì?"
- **Phân biệt:** event time, valid time, publication time, system time. Giá của 10/9, công bố 10/9, sửa 11/9, hệ thống nạp 12/9: hỏi as-of 10/9 không được dùng bản sửa 11/9.
- **Repo:** truy vấn effective observation theo `available_at ≤ as_of` và `system_time ≤ as_of_system` (hai chế độ: "thế giới biết gì" và "hệ thống biết gì").
- **Dữ liệu thật:** EIA chỉ công bố thứ Tư: hỏi as-of thứ Hai 21/9/2026 thì Brent mới nhất là của 15/9 (release 16/9), không phải 21/9.
- **Xong khi:** case T6/T3 và as-of trên trả đúng; câu trả lời cũ tái lập được từ `release_id` ghi trong lineage.
- **ID:** S1-01, S1-02, S7-01.

## UC14. Skill improvement

> **Ngoài POC 1 tháng** (05 mục 6).

- **Luồng:** trace (task graph, tool call, lỗi, retry, bằng chứng, kết quả judge, phản hồi người) → phát hiện mẫu → skill candidate → offline eval → người duyệt → skill registry.
- **Agent không tự sửa workflow production.**
- **ID:** S13-01.

## Ánh xạ kịch bản nghiệm thu 27 bước

| Bước | UC | Hiện thực | Dữ liệu dùng | ID |
| --- | --- | --- | --- | --- |
| 1. Nguồn Brent mới xuất hiện | UC01 | Steward thêm nguồn qua Add Data | Nguồn fixture "Brent feed B" (file do đội kiểm soát trên MinIO, gắn nhãn dữ liệu thử) + EIA thật | S2-01 |
| 2. Connector lấy dữ liệu | UC01 | `web_crawler` | | S2-03 |
| 3. Raw bất biến | UC01 | MinIO theo SHA-256, release | | S2-02 |
| 4. Schema nguồn thay đổi | UC03 | Fixture đổi `price` → `settle` | Fixture | S3-03 |
| 5. Reverse schema phát hiện | UC03 | Fingerprint khác, chặn nạp | | S3-03 |
| 6. Mapping mới được đề xuất | UC03 | LLM đề xuất, **cố ý** map `settle` vào `BRENT_SPOT` nếu validator chưa bắt | | S3-02 |
| 7. DQ phát hiện bất thường | UC05 | Cross-source DQ: lệch ~18% so EIA | Giá fixture lấy theo mức futures thật ~107 ngày 15/9 | S6-01 |
| 8. Đối chiếu nguồn khác | UC06 | So EIA spot 130,80 | EIA thật | S6-02 |
| 9. Không tự giải quyết | UC06 | Phân loại `UNKNOWN` | | S6-02 |
| 10. Tạo ReviewCase | UC07 | | | S6-03 |
| 11. Người có thẩm quyền được báo | UC07 | Authority resolver + hộp thư SSE | | S6-03 |
| 12. Người xác định spot ≠ futures | UC07 | Reviewer chọn lý do `DEFINITION_DIFFERENCE` | | S6-03 |
| 13. Sửa mapping, không sửa raw | UC07 | Mapping mới: `settle → BRENT_FUTURES_M1.settlement_price` | | S7-01 |
| 14. Checker duyệt | UC07 | maker ≠ checker, OCC | | S6-03 |
| 15. Effective observation publish | UC07 | ClickHouse + `observation.published` | | S7-01 |
| 16. Tính lại dữ liệu dẫn xuất | UC08 | Dependency graph: basis spot–futures, returns | | S7-02 |
| 17. Phát hiện Brent giảm >5% | UC12 | Replay release EIA 23/09 | 130,80 → 114,89 (−12,16%) | S11-01 |
| 18. Agent tự mở investigation | UC12 | Không có người hỏi | | S11-01 |
| 19. Structured engine xác định mức giảm | UC08 | `detect_drop_episodes` | | S8-01 |
| 20. Tìm event/report | UC09 | Retrieval + claim/event | WPSR, OPEC, sự kiện 9/2026 | S9-02 |
| 21. Graph nối Event ↔ Brent ↔ Evidence | UC04 | Bảng `knowledge.relation` + khung đồ thị trên UI (Neo4j là Tier B) | | S5-01 |
| 22. Verifier kiểm số, độ tươi, bằng chứng | UC10 | Verifier | EIA release 23/09 là as-of | S10-01 |
| 23. Tạo alert | UC12 | Dedup/cooldown | | S11-01 |
| 24. Analyst nhận thông báo | UC12 | Hộp thư | | S11-02 |
| 25. "So với 3 lần gần nhất?" | UC10 | Ask với ngữ cảnh alert | 3 đợt gần nhất trong CSV | S9-02 |
| 26. Phân tích hybrid | UC10 | Task graph | | S9-01, S9-02 |
| 27. Bảng + chart + evidence + lineage | UC10 | Khối trả lời trong Ask | | S10-01, E-01 |

Nguồn fixture ở bước 1–7 là cần thiết vì không có nguồn futures Brent miễn phí sạch licence (04
mục 7) và không thể hẹn trước một lần đổi schema thật. Nó phải được gắn `use_rights: internal_analysis`,
`purpose: evaluation` và nhãn "dữ liệu thử" trên mọi màn hình.

## Kịch bản Việt Nam

Tám bước chạy trên dữ liệu thật, đặc tả ở [06-evaluation-plan.md](06-evaluation-plan.md) mục 7. Dùng
cùng cơ chế với 27 bước (không mã riêng cho Việt Nam): UC02/UC04 (thông cáo → observation), UC03/UC05
(E10RON95-III `SUCCEEDS` RON95-III, locale), UC07 (số trích từ văn bản luôn qua người duyệt), UC12
(alert khi giá bán điều chỉnh), UC06/UC08 (chênh vàng theo phía tỷ giá), UC10 (giải thích kỳ điều
hành). ID: S4-02, VN-02…VN-05, S11-01, S9-01, E-01.

